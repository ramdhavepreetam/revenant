"""Tests for project-doc loading (F6 tier a): REVENANT.md into the preamble."""
from __future__ import annotations

from pathlib import Path

from revenant_cli.project_context import (
    MAX_DOC_CHARS,
    compose_preamble,
    find_project_doc,
    load_project_doc,
)


def test_no_doc_returns_none(tmp_path: Path):
    assert find_project_doc(tmp_path) is None


def test_finds_revenant_md(tmp_path: Path):
    (tmp_path / "REVENANT.md").write_text("hi")
    assert find_project_doc(tmp_path) == (tmp_path / "REVENANT.md")


def test_revenant_md_preferred_over_claude_md(tmp_path: Path):
    (tmp_path / "REVENANT.md").write_text("revenant")
    (tmp_path / "CLAUDE.md").write_text("claude")
    assert find_project_doc(tmp_path).name == "REVENANT.md"


def test_falls_back_to_claude_md(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("claude")
    assert find_project_doc(tmp_path).name == "CLAUDE.md"


def test_falls_back_to_agents_md(tmp_path: Path):
    (tmp_path / "AGENTS.md").write_text("agents")
    assert find_project_doc(tmp_path).name == "AGENTS.md"


def test_load_returns_empty_without_doc(tmp_path: Path):
    assert load_project_doc(tmp_path) == ""


def test_load_reads_and_strips(tmp_path: Path):
    (tmp_path / "REVENANT.md").write_text("\n  Use tabs.  \n")
    assert load_project_doc(tmp_path) == "Use tabs."


def test_load_caps_large_doc(tmp_path: Path):
    (tmp_path / "REVENANT.md").write_text("x" * (MAX_DOC_CHARS + 500))
    out = load_project_doc(tmp_path)
    assert len(out) <= MAX_DOC_CHARS + 60  # cap + truncation marker
    assert "truncated" in out


def test_compose_preamble_no_doc_is_unchanged(tmp_path: Path):
    assert compose_preamble("BASE PREAMBLE", tmp_path) == "BASE PREAMBLE"


def test_compose_preamble_appends_doc(tmp_path: Path):
    (tmp_path / "REVENANT.md").write_text("Run pytest before committing.")
    out = compose_preamble("BASE PREAMBLE", tmp_path)
    assert out.startswith("BASE PREAMBLE")
    assert "Run pytest before committing." in out
    assert "Project conventions" in out
