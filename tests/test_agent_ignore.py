"""Tests for the ignore matcher (F7): .revenantignore / .gitignore support,
plus integration through the fs tools (glob/grep/list_dir cull ignored paths)."""
from __future__ import annotations

from pathlib import Path

from nerva_agent.agent_ignore import IgnoreMatcher, _parse_line, load_ignore_matcher
from nerva_agent.agent_fs_tools import build_fs_tools


def _matcher(*patterns: str) -> IgnoreMatcher:
    return IgnoreMatcher([r for r in (_parse_line(p) for p in patterns) if r])


# --- pattern semantics -------------------------------------------------------

def test_dir_pattern_ignores_contents():
    m = _matcher("node_modules/")
    assert m.match("node_modules", is_dir=True)
    assert m.match("node_modules/pkg/index.js", is_dir=False)


def test_dir_pattern_matches_at_any_depth():
    m = _matcher("build/")
    assert m.match("a/b/build/out.o", is_dir=False)
    assert m.match("a/b/build", is_dir=True)


def test_glob_pattern():
    m = _matcher("*.log")
    assert m.match("app.log", is_dir=False)
    assert m.match("logs/server.log", is_dir=False)
    assert not m.match("app.txt", is_dir=False)


def test_anchored_pattern_is_root_only():
    m = _matcher("/dist")
    assert m.match("dist", is_dir=True)
    assert not m.match("src/dist", is_dir=True)


def test_negation_unignores():
    # Ignore all .log, but keep important.log (last matching rule wins).
    m = _matcher("*.log", "!important.log")
    assert m.match("debug.log", is_dir=False)
    assert not m.match("important.log", is_dir=False)


def test_comments_and_blanks_skipped():
    m = _matcher("# a comment", "", "   ", "keep_me/")
    assert m.match("keep_me/x", is_dir=False)
    assert not m.match("other/x", is_dir=False)


def test_non_matching_path_not_ignored():
    m = _matcher("node_modules/", "*.log")
    assert not m.match("src/main.py", is_dir=False)


# --- loading from files ------------------------------------------------------

def test_default_ignores_apply_without_files(tmp_path: Path):
    m = load_ignore_matcher(tmp_path)
    assert m.match(".git/config", is_dir=False)
    assert m.match("node_modules/x", is_dir=False)
    assert m.match("__pycache__/y.pyc", is_dir=False)


def test_reads_revenantignore(tmp_path: Path):
    (tmp_path / ".revenantignore").write_text("secret/\n*.tmp\n")
    m = load_ignore_matcher(tmp_path)
    assert m.match("secret/keys", is_dir=False)
    assert m.match("scratch.tmp", is_dir=False)


def test_reads_gitignore(tmp_path: Path):
    (tmp_path / ".gitignore").write_text("dist/\n")
    m = load_ignore_matcher(tmp_path)
    assert m.match("dist/bundle.js", is_dir=False)


# --- integration through fs tools --------------------------------------------

def test_glob_skips_ignored_dirs(tmp_path: Path):
    (tmp_path / ".revenantignore").write_text("vendor/\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.py").write_text("y = 2\n")

    tools = {t.name: t for t in build_fs_tools(tmp_path)}
    out = tools["glob"].run(pattern="**/*.py")
    assert "src/app.py" in out
    assert "vendor/lib.py" not in out


def test_list_dir_hides_ignored_entries(tmp_path: Path):
    (tmp_path / ".revenantignore").write_text("build/\n")
    (tmp_path / "build").mkdir()
    (tmp_path / "keep.py").write_text("z = 3\n")

    tools = {t.name: t for t in build_fs_tools(tmp_path)}
    out = tools["list_dir"].run()
    assert "keep.py" in out
    assert "build/" not in out


def test_grep_skips_ignored_files(tmp_path: Path):
    (tmp_path / ".revenantignore").write_text("vendor/\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("TARGET here\n")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.py").write_text("TARGET there\n")

    tools = {t.name: t for t in build_fs_tools(tmp_path)}
    out = tools["grep"].run(pattern="TARGET")
    assert "src/app.py" in out
    assert "vendor/lib.py" not in out
