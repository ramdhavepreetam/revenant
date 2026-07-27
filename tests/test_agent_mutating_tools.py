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
