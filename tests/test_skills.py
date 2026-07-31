"""Tests for skill discovery + parsing (F12.1/F12.2, ADR-0005).

Covers +++ TOML frontmatter parsing, discovery over project/user roots with
project-overrides-user precedence, malformed-skip degradation, and the compact
skill index rendering for progressive disclosure.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from nerva_agent.skills import (
    Skill, parse_skill_md, discover_skills, render_skill_index,
    scope_registry, compose_skill_body,
)
from nerva_agent.agent_tools import Tool, ToolRegistry


def _write_skill(root: Path, name: str, body="do the thing", *,
                 description="A skill.", trigger=None, tools=None, raw=None):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (d / "SKILL.md").write_text(raw)
        return d / "SKILL.md"
    fm = [f'name = "{name}"', f'description = "{description}"']
    if trigger:
        fm.append(f'trigger = "{trigger}"')
    if tools:
        fm.append("tools = [" + ", ".join(f'"{t}"' for t in tools) + "]")
    content = "+++\n" + "\n".join(fm) + "\n+++\n" + body
    (d / "SKILL.md").write_text(content)
    return d / "SKILL.md"


# --- frontmatter parsing -----------------------------------------------------

def test_parse_frontmatter_and_body():
    text = textwrap.dedent('''\
        +++
        name = "run-tests"
        description = "Run the suite."
        tools = ["run_bash", "read_file"]
        +++
        Step 1. Run the tests.
        Step 2. Summarize failures.''')
    meta, body = parse_skill_md(text)
    assert meta["name"] == "run-tests"
    assert meta["tools"] == ["run_bash", "read_file"]
    assert body.startswith("Step 1.")


def test_parse_no_frontmatter_is_body_only():
    meta, body = parse_skill_md("just instructions, no fences")
    assert meta == {}
    assert body == "just instructions, no fences"


def test_parse_unclosed_fence_is_body_only():
    meta, body = parse_skill_md("+++\nname = \"x\"\n(no closing fence)")
    assert meta == {}
    assert "no closing fence" in body


def test_parse_malformed_toml_raises():
    import tomllib
    with pytest.raises(tomllib.TOMLDecodeError):
        parse_skill_md("+++\nname = = broken\n+++\nbody")


# --- discovery ---------------------------------------------------------------

def test_discover_from_project_root(tmp_path):
    proj = tmp_path / ".revenant" / "skills"
    _write_skill(proj, "run-tests", description="Run tests.")
    skills = discover_skills(proj, None)
    assert len(skills) == 1
    s = skills[0]
    assert s.name == "run-tests" and s.description == "Run tests."
    assert s.source == "project"
    assert s.slash == "/run-tests"


def test_trigger_overrides_default_slash(tmp_path):
    proj = tmp_path / "skills"
    _write_skill(proj, "rt", trigger="/run-tests")
    (skill,) = discover_skills(proj, None)
    assert skill.slash == "/run-tests"


def test_project_overrides_user_by_name(tmp_path):
    user = tmp_path / "user_skills"
    proj = tmp_path / "proj_skills"
    _write_skill(user, "deploy", description="user version")
    _write_skill(proj, "deploy", description="project version")
    (skill,) = discover_skills(proj, user)
    assert skill.description == "project version"
    assert skill.source == "project"


def test_name_defaults_to_directory(tmp_path):
    proj = tmp_path / "skills"
    # No name in frontmatter -> directory name is used.
    _write_skill(proj, "lint", raw='+++\ndescription = "Lint it."\n+++\nrun the linter')
    (skill,) = discover_skills(proj, None)
    assert skill.name == "lint"


def test_malformed_skill_is_skipped_with_warning(tmp_path, capsys):
    proj = tmp_path / "skills"
    _write_skill(proj, "good", description="fine")
    _write_skill(proj, "bad", raw="+++\nname = = broken\n+++\nbody")
    skills = discover_skills(proj, None)
    assert [s.name for s in skills] == ["good"]
    assert "malformed frontmatter" in capsys.readouterr().err


def test_missing_roots_return_empty(tmp_path):
    assert discover_skills(tmp_path / "nope", tmp_path / "also-nope") == []
    assert discover_skills(None, None) == []


def test_discovery_is_sorted_by_name(tmp_path):
    proj = tmp_path / "skills"
    for n in ("zeta", "alpha", "mid"):
        _write_skill(proj, n)
    assert [s.name for s in discover_skills(proj, None)] == ["alpha", "mid", "zeta"]


# --- index rendering ---------------------------------------------------------

def test_render_index_lists_name_and_description(tmp_path):
    skills = [
        Skill(name="run-tests", description="Run the suite.", body="..."),
        Skill(name="deploy", description="Ship it.", body="...", trigger="/ship"),
    ]
    out = render_skill_index(skills)
    assert "/run-tests: Run the suite." in out
    assert "/ship: Ship it." in out
    # Bodies must NOT be in the index (progressive disclosure).
    assert "..." not in out


def test_render_index_empty_is_blank():
    assert render_skill_index([]) == ""


# --- tool scoping (F12.3) ----------------------------------------------------

def _reg(*names):
    return ToolRegistry([Tool(name=n, description=n, run=lambda **k: "ok") for n in names])


def test_scope_limits_registry_to_declared_tools():
    full = _reg("run_bash", "read_file", "edit_file", "git.status")
    skill = Skill(name="rt", description="", body="", tools=["run_bash", "read_file"])
    scoped = scope_registry(full, skill)
    assert set(scoped.names()) == {"run_bash", "read_file"}


def test_scope_composes_builtin_and_mcp_tools():
    full = _reg("run_bash", "git.status")
    skill = Skill(name="s", description="", body="", tools=["run_bash", "git.status"])
    scoped = scope_registry(full, skill)
    assert set(scoped.names()) == {"run_bash", "git.status"}  # both sources compose


def test_scope_empty_tools_returns_full_registry():
    full = _reg("a", "b")
    skill = Skill(name="s", description="", body="", tools=[])
    assert scope_registry(full, skill) is full


def test_scope_missing_tool_warns_and_continues(capsys):
    full = _reg("run_bash")
    skill = Skill(name="s", description="", body="", tools=["run_bash", "nope"])
    scoped = scope_registry(full, skill)
    assert scoped.names() == ["run_bash"]
    assert "not available" in capsys.readouterr().err


# --- body injection (F12.2) --------------------------------------------------

def test_compose_body_injects_under_header():
    skill = Skill(name="run-tests", description="", body="1. run pytest")
    out = compose_skill_body("BASE", skill)
    assert "BASE" in out
    assert "Active skill: run-tests" in out
    assert "1. run pytest" in out
