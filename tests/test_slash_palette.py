"""V4 (ADR-0017): the slash-command registry — discoverability logic.

Pure-Python (no textual): the palette's data + resolution are unit-testable so the
"all options visible" behavior is locked without the optional TUI dependency.
"""
from __future__ import annotations

from revenant_cli.tui.commands import (
    SlashRegistry, SlashCommand, BUILTIN_COMMANDS,
)


class _FakeSkill:
    def __init__(self, name, desc):
        self.name = name
        self.slash = f"/{name}"
        self.description = desc
        self.tools = []


class _FakeLoop:
    def __init__(self, skills=None):
        self._skills = skills or {}


def test_builtins_present_with_summaries():
    reg = SlashRegistry.from_loop(_FakeLoop())
    names = {c.name for c in reg.all()}
    assert {"/help", "/skills", "/skill", "/exit", "/context", "/agents"} <= names
    # every command carries a non-empty summary (that's the discoverability point).
    assert all(c.summary for c in reg.all())


def test_skills_become_commands():
    loop = _FakeLoop({"fix": _FakeSkill("fix", "fix failing tests")})
    reg = SlashRegistry.from_loop(loop)
    fix = reg.get("/fix")
    assert fix is not None
    assert fix.kind == "skill"
    assert fix.summary == "fix failing tests"


def test_match_prefix_filters():
    reg = SlashRegistry.from_loop(_FakeLoop())
    m = reg.match("/sk")
    names = {c.name for c in m}
    assert "/skill" in names and "/skills" in names
    assert "/help" not in names


def test_bare_slash_lists_everything():
    reg = SlashRegistry.from_loop(_FakeLoop({"fix": _FakeSkill("fix", "d")}))
    assert len(reg.match("/")) == len(reg.all())
    assert len(reg.match("")) == len(reg.all())


def test_match_is_case_insensitive():
    reg = SlashRegistry.from_loop(_FakeLoop())
    assert any(c.name == "/help" for c in reg.match("/HE"))


def test_get_ignores_args_after_space():
    reg = SlashRegistry.from_loop(_FakeLoop({"fix": _FakeSkill("fix", "d")}))
    assert reg.get("/skill some name").name == "/skill"
    assert reg.get("/fix now").name == "/fix"


def test_unknown_command_returns_none():
    reg = SlashRegistry.from_loop(_FakeLoop())
    assert reg.get("/nope") is None


def test_dedup_keeps_first():
    reg = SlashRegistry([
        SlashCommand("/x", "first"), SlashCommand("/x", "second"),
    ])
    assert reg.get("/x").summary == "first"
