from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

from nerva_agent.agent_tools import ToolRegistry
from nerva_agent.agent_fs_tools import build_fs_tools, WorkspaceError
from nerva_agent.agent_edit_tools import build_edit_tools
from nerva_agent.agent_bash_tool import build_bash_tool, BashBlocked


class WriteEditTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.reg = ToolRegistry(build_fs_tools(self.root) + build_edit_tools(self.root))

    def tearDown(self):
        self._tmp.cleanup()

    def test_write_and_read_back(self):
        self.reg.dispatch("write_file", {"path": "a.txt", "content": "one\ntwo\n"})
        self.assertEqual((self.root / "a.txt").read_text(), "one\ntwo\n")

    def test_write_creates_parent_dirs(self):
        self.reg.dispatch("write_file", {"path": "sub/dir/f.txt", "content": "x"})
        self.assertTrue((self.root / "sub" / "dir" / "f.txt").exists())

    def test_write_flags_require_approval(self):
        self.assertTrue(self.reg.get("write_file").requires_approval)
        self.assertTrue(self.reg.get("write_file").mutating)
        self.assertFalse(self.reg.get("write_file").parallel_safe)

    def test_edit_exact_single_match(self):
        (self.root / "b.py").write_text("x = 1\ny = 2\n")
        self.reg.dispatch("edit_file", {"path": "b.py", "old": "y = 2", "new": "y = 3"})
        self.assertEqual((self.root / "b.py").read_text(), "x = 1\ny = 3\n")

    def test_edit_no_match_errors(self):
        (self.root / "b.py").write_text("x = 1\n")
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("edit_file", {"path": "b.py", "old": "nope", "new": "z"})

    def test_edit_ambiguous_match_errors(self):
        (self.root / "b.py").write_text("a\na\n")
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("edit_file", {"path": "b.py", "old": "a", "new": "b"})

    def test_edit_identical_errors(self):
        (self.root / "b.py").write_text("a\n")
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("edit_file", {"path": "b.py", "old": "a", "new": "a"})

    def test_edit_missing_file_errors(self):
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("edit_file", {"path": "ghost.py", "old": "a", "new": "b"})

    # --- W4a (ADR-0020): replace_all ---------------------------------------
    def test_edit_replace_all_renames_every_occurrence(self):
        # An in-file rename: the symbol `greet` appears 3x (def + 2 calls).
        (self.root / "m.py").write_text(
            "def greet(n):\n    return n\n\n"
            "print(greet(1))\nprint(greet(2))\n"
        )
        obs = self.reg.dispatch("edit_file",
                                {"path": "m.py", "old": "greet", "new": "hail", "all": True})
        text = (self.root / "m.py").read_text()
        self.assertNotIn("greet", text)
        self.assertEqual(text.count("hail"), 3)
        self.assertIn("3 replacements", obs)

    def test_edit_default_still_errors_on_ambiguous(self):
        # Without all=True, a non-unique match still errors (byte-parity contract).
        (self.root / "b.py").write_text("a\na\n")
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("edit_file", {"path": "b.py", "old": "a", "new": "b"})
        # all=False explicitly is the same.
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("edit_file", {"path": "b.py", "old": "a", "new": "b", "all": False})

    def test_edit_replace_all_still_errors_on_no_match(self):
        (self.root / "b.py").write_text("x = 1\n")
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("edit_file", {"path": "b.py", "old": "nope", "new": "z", "all": True})

    def test_edit_replace_all_accepts_string_bool(self):
        # Weak models pass all="true"; it must be coerced, not treated as a name.
        (self.root / "b.py").write_text("a\na\n")
        self.reg.dispatch("edit_file", {"path": "b.py", "old": "a", "new": "b", "all": "true"})
        self.assertEqual((self.root / "b.py").read_text(), "b\nb\n")

    def test_edit_single_match_unchanged_by_all_flag(self):
        # A unique match still yields "1 replacement" regardless of all.
        (self.root / "b.py").write_text("x = 1\ny = 2\n")
        obs = self.reg.dispatch("edit_file",
                                {"path": "b.py", "old": "y = 2", "new": "y = 3", "all": True})
        self.assertEqual((self.root / "b.py").read_text(), "x = 1\ny = 3\n")
        self.assertIn("1 replacement", obs)

    # --- W4c (ADR-0020): apply_edits (atomic multi-file) -------------------
    def test_apply_edits_multi_file_rename_all_or_nothing(self):
        (self.root / "a.py").write_text("def greet():\n    pass\ngreet()\n")
        (self.root / "b.py").write_text("from a import greet\ngreet()\n")
        obs = self.reg.dispatch("apply_edits", {"edits": [
            {"path": "a.py", "old": "greet", "new": "hail", "all": True},
            {"path": "b.py", "old": "greet", "new": "hail", "all": True},
        ]})
        self.assertNotIn("greet", (self.root / "a.py").read_text())
        self.assertNotIn("greet", (self.root / "b.py").read_text())
        self.assertIn("2 file(s)", obs)

    def test_apply_edits_rolls_back_all_on_any_failure(self):
        (self.root / "x.py").write_text("value = 1\n")
        (self.root / "y.py").write_text("other = 2\n")
        before_x = (self.root / "x.py").read_text()
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("apply_edits", {"edits": [
                {"path": "x.py", "old": "value = 1", "new": "value = 99"},
                {"path": "y.py", "old": "NOPE", "new": "z"},   # fails -> rollback
            ]})
        # The first (successful) edit was reverted: workspace is as it started.
        self.assertEqual((self.root / "x.py").read_text(), before_x)
        self.assertEqual((self.root / "y.py").read_text(), "other = 2\n")

    def test_apply_edits_reverts_a_newly_created_file_on_rollback(self):
        # If an edit set touches multiple spots in one file and a later edit fails,
        # the file returns to its pre-set content (not a partial state).
        (self.root / "f.py").write_text("a = 1\nb = 2\n")
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("apply_edits", {"edits": [
                {"path": "f.py", "old": "a = 1", "new": "a = 10"},
                {"path": "f.py", "old": "NOPE", "new": "z"},   # fails
            ]})
        self.assertEqual((self.root / "f.py").read_text(), "a = 1\nb = 2\n")

    def test_apply_edits_requires_approval_and_mutating(self):
        t = self.reg.get("apply_edits")
        self.assertTrue(t.mutating)
        self.assertTrue(t.requires_approval)

    def test_apply_edits_rejects_malformed_input(self):
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("apply_edits", {"edits": []})              # empty
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("apply_edits", {"edits": [{"path": "a"}]})  # missing old/new

    def test_apply_edits_confined_to_workspace(self):
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("apply_edits", {"edits": [
                {"path": "../evil.py", "old": "x", "new": "y"},
            ]})

    # --- confinement -------------------------------------------------------
    def test_write_escape_blocked(self):
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("write_file", {"path": "../evil.txt", "content": "x"})

    def test_write_absolute_escape_blocked(self):
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("write_file", {"path": "/tmp/evil.txt", "content": "x"})


class BashTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.reg = ToolRegistry([build_bash_tool(self.root)])

    def tearDown(self):
        self._tmp.cleanup()

    def test_runs_and_reports_exit(self):
        out = self.reg.dispatch("run_bash", {"command": "echo hi"})
        self.assertIn("hi", out)
        self.assertIn("exit 0", out)

    def test_nonzero_exit_reported(self):
        out = self.reg.dispatch("run_bash", {"command": "exit 7"})
        self.assertIn("exit 7", out)

    def test_runs_in_workspace_cwd(self):
        (self.root / "marker.txt").write_text("")
        out = self.reg.dispatch("run_bash", {"command": "ls"})
        self.assertIn("marker.txt", out)

    def test_flags_require_approval(self):
        t = self.reg.get("run_bash")
        self.assertTrue(t.requires_approval)
        self.assertTrue(t.mutating)

    def test_empty_command_errors(self):
        with self.assertRaises(Exception):
            self.reg.dispatch("run_bash", {"command": "   "})

    # --- footgun hard-blocks (apply even in yolo) --------------------------
    def test_footguns_blocked(self):
        for cmd in [
            "rm -rf /",
            "rm -rf ~",
            "rm -fr /home",
            ":(){ :|:& };:",
            "dd if=/dev/zero of=/dev/sda",
            "mkfs.ext4 /dev/sda1",
            "shutdown now",
            "reboot",
        ]:
            with self.subTest(cmd=cmd):
                with self.assertRaises(BashBlocked):
                    self.reg.dispatch("run_bash", {"command": cmd})

    def test_more_rm_footguns_blocked(self):
        for cmd in ["rm -fr /home", "rm --recursive --force /", "rm -rf /usr/local",
                    "rm -Rf /etc", "rm -rf /*", "rm -rf ../../.."]:
            with self.subTest(cmd=cmd):
                with self.assertRaises(BashBlocked):
                    self.reg.dispatch("run_bash", {"command": cmd})

    def test_workspace_relative_rm_allowed(self):
        # Recursive delete of a workspace-relative path is NOT a footgun.
        (self.root / "build").mkdir()
        (self.root / "build" / "x").write_text("")
        out = self.reg.dispatch("run_bash", {"command": "rm -rf ./build"})
        self.assertIn("exit 0", out)
        self.assertFalse((self.root / "build").exists())

    def test_benign_dev_commands_allowed(self):
        # No content censorship: ordinary dev commands run fine.
        for cmd in ["echo test", "python3 -c 'print(1)'", "git --version"]:
            with self.subTest(cmd=cmd):
                out = self.reg.dispatch("run_bash", {"command": cmd})
                self.assertIn("exit", out)


if __name__ == "__main__":
    unittest.main()
