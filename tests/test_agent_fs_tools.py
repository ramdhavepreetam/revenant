from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "core", _ROOT / "tts", _ROOT / "apps"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agent_tools import ToolRegistry
from agent_fs_tools import build_fs_tools, WorkspaceError


class FsToolTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "core").mkdir()
        (self.root / "core" / "a.py").write_text("def alpha():\n    return 1\n")
        (self.root / "core" / "b.py").write_text("import os\n# beta helper\n")
        (self.root / "README.md").write_text("# hello\nbeta appears here too\n")
        self.reg = ToolRegistry(build_fs_tools(self.root))

    def tearDown(self):
        self._tmp.cleanup()

    def test_read_file(self):
        out = self.reg.dispatch("read_file", {"path": "core/a.py"})
        self.assertIn("def alpha", out)

    def test_read_missing(self):
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("read_file", {"path": "core/nope.py"})

    def test_read_dir_rejected(self):
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("read_file", {"path": "core"})

    def test_list_dir(self):
        out = self.reg.dispatch("list_dir", {"path": "core"})
        self.assertIn("a.py", out)
        self.assertIn("b.py", out)

    def test_list_dir_default_root(self):
        out = self.reg.dispatch("list_dir", {})
        self.assertIn("core/", out)
        self.assertIn("README.md", out)

    def test_glob(self):
        out = self.reg.dispatch("glob", {"pattern": "core/*.py"})
        self.assertIn("core/a.py", out)
        self.assertIn("core/b.py", out)
        self.assertNotIn("README.md", out)

    def test_grep_finds_across_files(self):
        out = self.reg.dispatch("grep", {"pattern": "beta"})
        self.assertIn("b.py", out)
        self.assertIn("README.md", out)

    def test_grep_scoped_to_path(self):
        out = self.reg.dispatch("grep", {"pattern": "beta", "path": "core"})
        self.assertIn("b.py", out)
        self.assertNotIn("README.md", out)

    def test_grep_no_match(self):
        out = self.reg.dispatch("grep", {"pattern": "zzz_nomatch_zzz"})
        self.assertEqual(out.strip(), "(no matches)")

    # --- security: path confinement ---------------------------------------
    def test_escape_relative(self):
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("read_file", {"path": "../../../etc/passwd"})

    def test_escape_absolute(self):
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("read_file", {"path": "/etc/passwd"})

    def test_escape_mixed(self):
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("list_dir", {"path": "core/../../.."})

    def test_symlink_escape_blocked(self):
        # A symlink pointing outside the root must not be readable through it.
        outside = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        outside.write("SECRET")
        outside.close()
        link = self.root / "core" / "leak.py"
        try:
            link.symlink_to(outside.name)
        except OSError:
            self.skipTest("symlinks not supported here")
        with self.assertRaises(WorkspaceError):
            self.reg.dispatch("read_file", {"path": "core/leak.py"})
        Path(outside.name).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
