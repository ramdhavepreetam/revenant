"""Tests for the revenant CLI subcommand structure + REPL (F1).

Covers argv normalization (bare-goal back-compat), subcommand parsing, and the
chat REPL's multi-turn history threading — driven with a fake AgentLoop so no
model or network is touched.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from revenant_cli import cli
from revenant_cli.checkpoint import Checkpointer


# --- argv normalization / back-compat ---------------------------------------

def test_bare_goal_becomes_implicit_run():
    assert cli._normalize_argv(["summarize core/"]) == ["run", "summarize core/"]


def test_bare_goal_with_flags_becomes_run():
    assert cli._normalize_argv(["do x", "--read-only"]) == ["run", "do x", "--read-only"]


def test_explicit_subcommand_is_untouched():
    assert cli._normalize_argv(["run", "goal"]) == ["run", "goal"]
    assert cli._normalize_argv(["chat"]) == ["chat"]


def test_help_flag_is_untouched():
    assert cli._normalize_argv(["--help"]) == ["--help"]
    assert cli._normalize_argv(["-h"]) == ["-h"]


def test_empty_argv():
    assert cli._normalize_argv([]) == []


# --- parser wiring -----------------------------------------------------------

def test_run_parses_goal_and_flags():
    p = cli.build_parser()
    ns = p.parse_args(cli._normalize_argv(["run", "hello", "--yolo", "--workspace", "/tmp"]))
    assert ns.command == "run"
    assert ns.goal == "hello"
    assert ns.yolo is True
    assert ns.workspace == "/tmp"


def test_chat_parses_flags_without_goal():
    p = cli.build_parser()
    ns = p.parse_args(cli._normalize_argv(["chat", "--read-only"]))
    assert ns.command == "chat"
    assert ns.read_only is True


# --- REPL multi-turn behavior ------------------------------------------------

class _FakeResult:
    def __init__(self, messages):
        self.messages = messages
        self.stopped_reason = "final"


class _FakeLoop:
    """Records the (goal, history) of each run and returns a growing transcript."""

    def __init__(self):
        self.calls = []
        self._transcript = [{"role": "system", "content": "sys"}]

    def run(self, goal, history=None):
        self.calls.append((goal, history))
        # Simulate the real loop: append the user goal + an answer, return it all.
        self._transcript = list(history) if history else list(self._transcript)
        self._transcript.append({"role": "user", "content": goal})
        self._transcript.append({"role": "assistant", "content": f"answer to {goal}"})
        return _FakeResult(list(self._transcript))


def _patch_build_agent(monkeypatch, fake_loop):
    color = cli._color(False)
    monkeypatch.setattr(
        cli, "_build_agent",
        lambda args: ("/ws", type("C", (), {"model": "m"})(),
                      type("R", (), {"note": "n"})(), fake_loop, color),
    )


def _chat_args():
    return argparse.Namespace(
        workspace=".", base_url="", model="", max_steps=0, max_context_tokens=0,
        no_native_tools=False, read_only=True, yolo=False, no_color=True,
    )


def _chat_args_ws(workspace):
    """chat args bound to a real workspace (so session autosave has a home)."""
    ns = _chat_args()
    ns.workspace = str(workspace)
    return ns


def _patch_build_agent_ws(monkeypatch, fake_loop, workspace):
    """Like _patch_build_agent, but returns the given workspace + a model name,
    so cmd_chat's session autosave writes under it."""
    color = cli._color(False)
    monkeypatch.setattr(
        cli, "_build_agent",
        lambda args: (Path(workspace), type("C", (), {"model": "m"})(),
                      type("R", (), {"note": "n"})(), fake_loop, color),
    )


def test_repl_threads_history_across_turns(monkeypatch):
    fake = _FakeLoop()
    _patch_build_agent(monkeypatch, fake)
    lines = iter(["first goal", "second goal", "/exit"])
    rc = cli.cmd_chat(_chat_args(), input_fn=lambda _prompt: next(lines))
    assert rc == 0
    # Two real turns ran.
    assert [g for g, _ in fake.calls] == ["first goal", "second goal"]
    # First turn had no prior history; second turn received the first turn's transcript.
    assert fake.calls[0][1] is None
    second_history = fake.calls[1][1]
    assert second_history is not None
    assert {"role": "user", "content": "first goal"} in second_history


def test_repl_reset_clears_history(monkeypatch):
    fake = _FakeLoop()
    _patch_build_agent(monkeypatch, fake)
    lines = iter(["first", "/reset", "second", "/exit"])
    rc = cli.cmd_chat(_chat_args(), input_fn=lambda _prompt: next(lines))
    assert rc == 0
    # After /reset, the second real turn starts fresh (history None again).
    assert fake.calls[0][1] is None  # first
    assert fake.calls[1][1] is None  # second, post-reset


def test_repl_blank_lines_are_skipped(monkeypatch):
    fake = _FakeLoop()
    _patch_build_agent(monkeypatch, fake)
    lines = iter(["", "   ", "real goal", "/exit"])
    rc = cli.cmd_chat(_chat_args(), input_fn=lambda _prompt: next(lines))
    assert rc == 0
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "real goal"


def test_repl_exits_on_eof(monkeypatch):
    fake = _FakeLoop()
    _patch_build_agent(monkeypatch, fake)

    def raise_eof(_prompt):
        raise EOFError

    rc = cli.cmd_chat(_chat_args(), input_fn=raise_eof)
    assert rc == 0
    assert fake.calls == []


# --- undo subcommand (F8, ADR-0010) -----------------------------------------

def test_undo_parser_flags():
    p = cli.build_parser()
    ns = p.parse_args(cli._normalize_argv(["undo", "--all", "--workspace", "/tmp"]))
    assert ns.command == "undo"
    assert ns.all is True
    assert ns.workspace == "/tmp"


def test_checkpoint_store_path_under_data_dir(tmp_path):
    store = cli._checkpoint_store(tmp_path)
    assert store.name == "checkpoints.json"
    assert store.parent.name == ".aibot"
    assert store.parent.parent == tmp_path


def _undo_args(workspace, *, all_=False):
    return argparse.Namespace(workspace=str(workspace), all=all_, no_color=True)


def test_cmd_undo_nothing_to_undo(tmp_path, capsys):
    rc = cli.cmd_undo(_undo_args(tmp_path))
    assert rc == 0
    assert "nothing to undo" in capsys.readouterr().out.lower()


def test_cmd_undo_last_restores_from_store(tmp_path, capsys):
    (tmp_path / "a.txt").write_text("original\n")
    # Seed a persisted checkpoint the way a prior session would have.
    cp = Checkpointer(tmp_path, store_path=cli._checkpoint_store(tmp_path))
    cp.snapshot("edit_file", {"path": "a.txt"})
    (tmp_path / "a.txt").write_text("mutated\n")

    rc = cli.cmd_undo(_undo_args(tmp_path))
    assert rc == 0
    assert (tmp_path / "a.txt").read_text() == "original\n"
    assert "a.txt" in capsys.readouterr().out


def test_cmd_undo_all_reverts_everything(tmp_path, capsys):
    (tmp_path / "a.txt").write_text("a0\n")
    cp = Checkpointer(tmp_path, store_path=cli._checkpoint_store(tmp_path))
    cp.snapshot("edit_file", {"path": "a.txt"})
    (tmp_path / "a.txt").write_text("a1\n")
    cp.snapshot("write_file", {"path": "new.txt"})
    (tmp_path / "new.txt").write_text("created\n")

    rc = cli.cmd_undo(_undo_args(tmp_path, all_=True))
    assert rc == 0
    assert (tmp_path / "a.txt").read_text() == "a0\n"
    assert not (tmp_path / "new.txt").exists()
    assert "reverted 2 change(s)" in capsys.readouterr().out


# --- _build_agent wires the checkpointer only in write mode -----------------

def _agent_args(workspace, *, read_only):
    return argparse.Namespace(
        workspace=str(workspace), base_url="", model="", max_steps=0,
        max_context_tokens=0, no_native_tools=False, read_only=read_only,
        yolo=False, no_color=True,
    )


def _stub_agent_build(monkeypatch):
    """Neutralize model/hardware probing so _build_agent runs offline.

    Captures the before_tool passed into the constructed AgentLoop so tests can
    assert the checkpointer is wired (write mode) or not (read-only).
    """
    captured = {}

    class _Rec:
        max_context_tokens = 6000
        max_steps = 15
        keep_recent_steps = 3
        note = "stub"

    def fake_loop_ctor(*_a, before_tool=None, **_k):
        captured["before_tool"] = before_tool
        return type("L", (), {})()  # attributable (cli sets loop._mcp_clients)

    monkeypatch.setattr(cli, "AgentLoop", fake_loop_ctor)
    monkeypatch.setattr(cli, "recommend", lambda *a, **k: _Rec())
    monkeypatch.setattr(cli, "load_config", lambda ws: {})
    monkeypatch.setattr(cli, "load_profiles", lambda *a, **k: {})
    monkeypatch.setattr(
        cli, "build_config",
        lambda *a, **k: type("C", (), {"model": "m", "base_url": "http://x"})(),
    )
    monkeypatch.setattr(cli, "find_project_doc", lambda *a, **k: None)
    return captured


def test_build_agent_wires_before_tool_in_write_mode(tmp_path, monkeypatch):
    captured = _stub_agent_build(monkeypatch)
    cli._build_agent(_agent_args(tmp_path, read_only=False))
    assert captured["before_tool"] is not None


def test_build_agent_no_before_tool_in_read_only(tmp_path, monkeypatch):
    captured = _stub_agent_build(monkeypatch)
    cli._build_agent(_agent_args(tmp_path, read_only=True))
    assert captured["before_tool"] is None


# --- mcp subcommand (F11.4, ADR-0004) ---------------------------------------

import sys as _sys
import textwrap as _textwrap

_FAKE_MCP_SERVER = _textwrap.dedent('''
    import json, sys
    def send(o): sys.stdout.write(json.dumps(o)+"\\n"); sys.stdout.flush()
    for line in sys.stdin:
        line=line.strip()
        if not line: continue
        m=json.loads(line); mid=m.get("id"); method=m.get("method")
        if method=="initialize":
            send({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":"2024-11-05","capabilities":{}}})
        elif method=="notifications/initialized": pass
        elif method=="tools/list":
            send({"jsonrpc":"2.0","id":mid,"result":{"tools":[
                {"name":"status","description":"repo status","inputSchema":{"type":"object","properties":{}}}]}})
        elif method=="tools/call":
            send({"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text","text":"clean"}]}})
        else:
            send({"jsonrpc":"2.0","id":mid,"error":{"code":-32601,"message":"nope"}})
''')


def _mcp_workspace(tmp_path):
    """A workspace with a .revenant.toml pointing at a fake stdio MCP server."""
    server = tmp_path / "srv.py"
    server.write_text(_FAKE_MCP_SERVER)
    toml = tmp_path / ".revenant.toml"
    toml.write_text(
        "[[mcp.servers]]\n"
        'name = "git"\n'
        'transport = "stdio"\n'
        f'command = "{_sys.executable}"\n'
        f'args = ["{server}"]\n'
        'read_only = ["status"]\n'
    )
    return tmp_path


def _mcp_args(workspace, action=None, name=None):
    ns = argparse.Namespace(workspace=str(workspace), no_color=True, mcp_action=action)
    if name is not None:
        ns.name = name
    return ns


def test_cmd_mcp_list_shows_server_and_tools(tmp_path, capsys):
    rc = cli.cmd_mcp(_mcp_args(_mcp_workspace(tmp_path), action="list"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "git" in out
    assert "git.status" in out


def test_cmd_mcp_test_reports_health(tmp_path, capsys):
    rc = cli.cmd_mcp(_mcp_args(_mcp_workspace(tmp_path), action="test", name="git"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "git" in out and "git.status" in out


def test_cmd_mcp_test_unknown_server_errors(tmp_path, capsys):
    rc = cli.cmd_mcp(_mcp_args(_mcp_workspace(tmp_path), action="test", name="nope"))
    assert rc == 2
    assert "no configured server" in capsys.readouterr().err


def test_cmd_mcp_no_servers_configured(tmp_path, capsys):
    rc = cli.cmd_mcp(_mcp_args(tmp_path, action="list"))
    assert rc == 0
    assert "no MCP servers configured" in capsys.readouterr().out


def test_mcp_parser_accepts_flags_before_and_after_action():
    """Regression: parent optionals must parse on either side of the sub-action."""
    p = cli.build_parser()
    # flags AFTER the sub-action
    a = p.parse_args(["mcp", "list", "--workspace", "/w", "--no-color"])
    assert a.command == "mcp" and a.mcp_action == "list" and a.workspace == "/w"
    # flags BEFORE the sub-action (parent value must survive, not reset to '.')
    b = p.parse_args(["mcp", "--workspace", "/w", "list"])
    assert b.mcp_action == "list" and b.workspace == "/w"
    # test action carries its positional
    c = p.parse_args(["mcp", "test", "git", "--workspace", "/w"])
    assert c.mcp_action == "test" and c.name == "git" and c.workspace == "/w"


# --- skills subcommand + /skill REPL (F12.4, ADR-0005) ----------------------

def _skill_workspace(tmp_path, tools=None):
    d = tmp_path / ".revenant" / "skills" / "run-tests"
    d.mkdir(parents=True)
    fm = ['name = "run-tests"', 'description = "Run the suite."']
    if tools:
        fm.append("tools = [" + ", ".join(f'"{t}"' for t in tools) + "]")
    (d / "SKILL.md").write_text("+++\n" + "\n".join(fm) + "\n+++\n1. run pytest")
    return tmp_path


def _skills_args(workspace, action=None, name=None):
    ns = argparse.Namespace(workspace=str(workspace), no_color=True, skills_action=action)
    if name is not None:
        ns.name = name
    return ns


def test_cmd_skills_list(tmp_path, capsys):
    rc = cli.cmd_skills(_skills_args(_skill_workspace(tmp_path), action="list"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "/run-tests" in out and "Run the suite." in out


def test_cmd_skills_show(tmp_path, capsys):
    rc = cli.cmd_skills(_skills_args(_skill_workspace(tmp_path), action="show", name="run-tests"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "1. run pytest" in out  # body is shown


def test_cmd_skills_show_unknown(tmp_path, capsys):
    rc = cli.cmd_skills(_skills_args(_skill_workspace(tmp_path), action="show", name="nope"))
    assert rc == 2
    assert "no skill named" in capsys.readouterr().err


def test_cmd_skills_none_found(tmp_path, capsys):
    rc = cli.cmd_skills(_skills_args(tmp_path, action="list"))
    assert rc == 0
    assert "no skills found" in capsys.readouterr().out


def test_skills_parser_flag_ordering():
    p = cli.build_parser()
    a = p.parse_args(["skills", "list", "--workspace", "/w"])
    assert a.command == "skills" and a.skills_action == "list" and a.workspace == "/w"
    b = p.parse_args(["skills", "show", "run-tests", "--workspace", "/w"])
    assert b.skills_action == "show" and b.name == "run-tests"


# /skill in the REPL: a fake loop carrying skill state.

from nerva_agent.skills import Skill as _Skill
from nerva_agent.agent_tools import Tool as _Tool, ToolRegistry as _Registry


class _SkillLoop(_FakeLoop):
    def __init__(self, skills, registry):
        super().__init__()
        self._skills = {s.name: s for s in skills}
        self._base_preamble = "BASE"
        self.system_preamble = "BASE"
        self.registry = registry


def test_repl_skill_command_loads_body_and_scopes_tools(monkeypatch):
    skill = _Skill(name="rt", description="d", body="RUN PYTEST", tools=["run_bash"])
    reg = _Registry([_Tool(name=n, description=n, run=lambda **k: "ok")
                     for n in ("run_bash", "read_file", "edit_file")])
    loop = _SkillLoop([skill], reg)
    color = cli._color(False)
    monkeypatch.setattr(
        cli, "_build_agent",
        lambda args: ("/ws", type("C", (), {"model": "m"})(),
                      type("R", (), {"note": "n"})(), loop, color),
    )
    lines = iter(["/skill rt", "/exit"])
    rc = cli.cmd_chat(_chat_args(), input_fn=lambda _p: next(lines))
    assert rc == 0
    # Body injected into the preamble; registry scoped to the skill's tools.
    assert "RUN PYTEST" in loop.system_preamble
    assert loop.registry.names() == ["run_bash"]
    # The skill body was run as the turn goal.
    assert loop.calls[0][0] == "RUN PYTEST"


def test_repl_unknown_skill_prints_and_skips(monkeypatch):
    loop = _SkillLoop([], _Registry([]))
    color = cli._color(False)
    monkeypatch.setattr(
        cli, "_build_agent",
        lambda args: ("/ws", type("C", (), {"model": "m"})(),
                      type("R", (), {"note": "n"})(), loop, color),
    )
    lines = iter(["/skill ghost", "/exit"])
    rc = cli.cmd_chat(_chat_args(), input_fn=lambda _p: next(lines))
    assert rc == 0
    assert loop.calls == []  # unknown skill: no turn ran


# --- resume subcommand (F3, ADR-0007) ---------------------------------------

from revenant_cli import session_store as _ss


def test_resume_parser_optional_id():
    p = cli.build_parser()
    a = p.parse_args(cli._normalize_argv(["resume"]))
    assert a.command == "resume" and a.session_id is None
    b = p.parse_args(cli._normalize_argv(["resume", "123", "--workspace", "/w"]))
    assert b.session_id == "123" and b.workspace == "/w"


def _resume_args(workspace, session_id=None):
    return argparse.Namespace(
        workspace=str(workspace), session_id=session_id, base_url="", model="",
        max_steps=0, max_context_tokens=0, no_native_tools=False,
        read_only=True, yolo=False, no_color=True,
    )


def test_chat_autosaves_session(tmp_path, monkeypatch):
    fake = _FakeLoop()
    _patch_build_agent_ws(monkeypatch, fake, tmp_path)
    lines = iter(["do a thing", "/exit"])
    rc = cli.cmd_chat(_chat_args_ws(tmp_path), input_fn=lambda _p: next(lines))
    assert rc == 0
    sessions = _ss.list_sessions(tmp_path)
    assert len(sessions) == 1
    assert sessions[0]["goal"] == "do a thing"


def test_resume_list_renders_sessions(tmp_path, capsys):
    _ss.save_session(tmp_path, goal="earlier goal", model="m",
                     messages=[{"role": "user", "content": "x"}])
    rc = cli.cmd_resume(_resume_args(tmp_path, session_id="list"))
    assert rc == 0
    assert "earlier goal" in capsys.readouterr().out


def test_resume_list_empty(tmp_path, capsys):
    rc = cli.cmd_resume(_resume_args(tmp_path, session_id="list"))
    assert rc == 0
    assert "no saved sessions" in capsys.readouterr().out


def test_resume_unknown_id_errors(tmp_path, capsys):
    rc = cli.cmd_resume(_resume_args(tmp_path, session_id="0000000000000"))
    assert rc == 2
    assert "no session" in capsys.readouterr().err


def test_resume_none_with_no_sessions(tmp_path, capsys):
    rc = cli.cmd_resume(_resume_args(tmp_path, session_id=None))
    assert rc == 0
    assert "no saved sessions to resume" in capsys.readouterr().out


def test_resume_id_rehydrates_history_into_loop(tmp_path, monkeypatch):
    prior = [{"role": "user", "content": "old goal"},
             {"role": "assistant", "content": "old answer"}]
    sid = _ss.save_session(tmp_path, goal="old goal", model="m", messages=prior)

    fake = _FakeLoop()
    _patch_build_agent_ws(monkeypatch, fake, tmp_path)
    lines = iter(["keep going", "/exit"])
    rc = cli.cmd_resume(_resume_args(tmp_path, session_id=sid),
                        input_fn=lambda _p: next(lines))
    assert rc == 0
    # The resumed turn received the prior transcript as history.
    first_history = fake.calls[0][1]
    assert first_history is not None
    assert {"role": "user", "content": "old goal"} in first_history
