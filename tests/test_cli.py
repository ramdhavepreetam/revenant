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

def test_bare_no_args_opens_chat():
    # `revenant` with nothing after it opens interactive chat (TUI/REPL).
    assert cli._normalize_argv([]) == ["chat"]


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


def test_version_flag_is_untouched():
    # --version must NOT be rewritten into an implicit `run` goal.
    assert cli._normalize_argv(["--version"]) == ["--version"]


def test_version_flag_prints_version_and_exits(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("revenant ")
    # A resolvable version (installed) or the graceful "unknown" fallback.
    assert out.split()[1]  # non-empty version token


def test_revenant_version_returns_a_string():
    v = cli._revenant_version()
    assert isinstance(v, str) and v


def test_empty_argv():
    # Empty argv opens interactive chat (was a no-op before; changed by design).
    assert cli._normalize_argv([]) == ["chat"]


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
        yolo=False, no_color=True, skip_preflight=True,
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


# --- W6 (ADR-0021): mcp add -------------------------------------------------

def test_mcp_add_parser():
    p = cli.build_parser()
    a = p.parse_args(["mcp", "add", "fs", "--command", "mcp-fs",
                      "--arg", "serve", "--arg", "/data", "--project"])
    assert a.mcp_action == "add" and a.name == "fs"
    assert a.command == "mcp-fs" and a.args == ["serve", "/data"] and a.project is True


def test_cmd_mcp_add_writes_entry(tmp_path, capsys):
    args = argparse.Namespace(
        workspace=str(tmp_path), no_color=True, mcp_action="add", name="fs",
        transport="stdio", command="mcp-fs", args=["--root", "."], url=None, project=True,
    )
    rc = cli.cmd_mcp(args)
    assert rc == 0
    assert "added MCP server" in capsys.readouterr().out
    # The written entry is readable by the config loader.
    from revenant_cli.config import mcp_server_specs, load_config
    names = {s.name for s in mcp_server_specs(load_config(tmp_path))}
    assert "fs" in names


def test_cmd_mcp_add_duplicate_errors(tmp_path, capsys):
    args = argparse.Namespace(
        workspace=str(tmp_path), no_color=True, mcp_action="add", name="fs",
        transport="stdio", command="mcp-fs", args=[], url=None, project=True,
    )
    assert cli.cmd_mcp(args) == 0
    capsys.readouterr()
    assert cli.cmd_mcp(args) == 2                      # second add = duplicate
    assert "already exists" in capsys.readouterr().err


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


# --- loop subcommand (F13, ADR-0006) ----------------------------------------

def test_loop_parser_flags():
    p = cli.build_parser()
    a = p.parse_args(cli._normalize_argv(
        ["loop", "do it", "--until-tests", "--max-iterations", "3", "--autonomous"]))
    assert a.command == "loop" and a.goal == "do it"
    assert a.until_tests is True and a.max_iterations == 3 and a.autonomous is True


class _LoopFakeLoop:
    """A loop whose run() succeeds only once `flag` flips; carries checkpointer attrs."""
    def __init__(self, succeed_on=1):
        self.n = 0
        self.succeed_on = succeed_on
        self._mcp_clients = []
        self._checkpointer = None

    def run(self, goal, history=None):
        self.n += 1
        return type("R", (), {
            "messages": (history or []) + [{"role": "user", "content": goal[:30]}],
            "stopped_reason": "final" if self.n >= self.succeed_on else "max_steps",
            "answer": "",
        })()


def _loop_args(workspace, **over):
    ns = argparse.Namespace(
        goal="accomplish X", workspace=str(workspace), base_url="", model="",
        max_steps=0, max_context_tokens=0, no_native_tools=False, read_only=False,
        yolo=False, no_color=True, autonomous=False, dry_run=False,
        until=None, until_tests=False, until_file=None, test_cmd="pytest -q",
        max_iterations=5, max_wall=0.0,
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _patch_loop_build(monkeypatch, fake, workspace):
    color = cli._color(False)
    monkeypatch.setattr(
        cli, "_build_agent",
        lambda args: (Path(workspace), type("C", (), {"model": "m"})(),
                      type("R", (), {"note": "n"})(), fake, color),
    )
    return color


def test_loop_stops_when_model_reports_done(tmp_path, monkeypatch, capsys):
    fake = _LoopFakeLoop(succeed_on=1)
    _patch_loop_build(monkeypatch, fake, tmp_path)
    rc = cli.cmd_loop(_loop_args(tmp_path))  # default predicate = model final
    assert rc == 0
    assert fake.n == 1
    assert "done" in capsys.readouterr().out


def test_loop_until_file_iterates_until_created(tmp_path, monkeypatch):
    target = tmp_path / "out.txt"

    class Creator(_LoopFakeLoop):
        def run(self, goal, history=None):
            self.n += 1
            if self.n == 2:
                target.write_text("x")
            return type("R", (), {
                "messages": (history or []) + [{"role": "user", "content": goal[:30]}],
                "stopped_reason": "max_steps",  # model never says final; file drives
                "answer": "",
            })()

    fake = Creator(succeed_on=99)
    _patch_loop_build(monkeypatch, fake, tmp_path)
    rc = cli.cmd_loop(_loop_args(tmp_path, until_file="out.txt"))
    assert rc == 0
    assert fake.n == 2


def test_loop_hits_iteration_budget(tmp_path, monkeypatch, capsys):
    fake = _LoopFakeLoop(succeed_on=99)  # never done
    _patch_loop_build(monkeypatch, fake, tmp_path)
    rc = cli.cmd_loop(_loop_args(tmp_path, max_iterations=3))
    assert rc == 3  # non-zero: did not reach the goal
    assert fake.n == 3
    assert "max_iterations" in capsys.readouterr().out


def test_loop_autonomous_forces_yolo(tmp_path, monkeypatch):
    fake = _LoopFakeLoop(succeed_on=1)
    _patch_loop_build(monkeypatch, fake, tmp_path)
    args = _loop_args(tmp_path, autonomous=True)
    cli.cmd_loop(args)
    assert args.yolo is True  # autonomous implies auto-approve within budget


def test_loop_dry_run_forces_read_only(tmp_path, monkeypatch):
    fake = _LoopFakeLoop(succeed_on=1)
    _patch_loop_build(monkeypatch, fake, tmp_path)
    args = _loop_args(tmp_path, dry_run=True, autonomous=True)
    cli.cmd_loop(args)
    assert args.read_only is True   # dry-run never executes edits
    assert args.yolo is False       # and never auto-approves


def test_loop_journals_a_resumable_session(tmp_path, monkeypatch):
    fake = _LoopFakeLoop(succeed_on=2)
    _patch_loop_build(monkeypatch, fake, tmp_path)
    cli.cmd_loop(_loop_args(tmp_path))
    # A session was persisted (the run journal) and is resumable.
    assert len(_ss.list_sessions(tmp_path)) == 1


# --- code graph tools wiring (F14, ADR-0008) --------------------------------

def _capture_registry_build(monkeypatch):
    """Patch _build_agent's deps and capture the ToolRegistry the loop is built with."""
    captured = {}

    def fake_loop(config, registry, **k):
        captured["names"] = registry.names()
        return type("L", (), {})()

    class _Rec:
        max_steps = 5; max_context_tokens = 6000; keep_recent_steps = 3; note = "n"

    monkeypatch.setattr(cli, "AgentLoop", fake_loop)
    monkeypatch.setattr(cli, "recommend", lambda *a, **k: _Rec())
    monkeypatch.setattr(cli, "load_config", lambda ws: {})
    monkeypatch.setattr(cli, "load_profiles", lambda *a, **k: {})
    monkeypatch.setattr(cli, "build_config",
                        lambda *a, **k: type("C", (), {"model": "m", "base_url": "x"})())
    monkeypatch.setattr(cli, "find_project_doc", lambda *a, **k: None)
    return captured


def _graph_ws(tmp_path):
    (tmp_path / "m.py").write_text("def helper():\n    return 1\n\ndef top():\n    return helper()\n")
    return tmp_path


def test_graph_tools_registered_in_readonly(tmp_path, monkeypatch):
    captured = _capture_registry_build(monkeypatch)
    cli._build_agent(_agent_args(_graph_ws(tmp_path), read_only=True))
    for name in ("defn_of", "who_calls", "neighbors", "impact_of"):
        assert name in captured["names"]


def test_no_graph_flag_skips_graph_tools(tmp_path, monkeypatch):
    captured = _capture_registry_build(monkeypatch)
    args = _agent_args(_graph_ws(tmp_path), read_only=True)
    args.no_graph = True
    cli._build_agent(args)
    assert "defn_of" not in captured["names"]


# --- P8: sub-agent tool + git-native undo wiring (ADR-0009) -----------------

import subprocess as _sp


def test_spawn_subagent_registered_in_write_mode(tmp_path, monkeypatch):
    captured = _capture_registry_build(monkeypatch)
    args = _agent_args(tmp_path, read_only=False)
    args.no_graph = True
    cli._build_agent(args)
    assert "spawn_subagent" in captured["names"]


def test_spawn_subagent_absent_in_read_only(tmp_path, monkeypatch):
    captured = _capture_registry_build(monkeypatch)
    args = _agent_args(tmp_path, read_only=True)
    args.no_graph = True
    cli._build_agent(args)
    assert "spawn_subagent" not in captured["names"]


# --- W5 (ADR-0021): role-routed sub-agent factory ---------------------------

class _FakeLoopWithConfig:
    def __init__(self):
        self.config = type("C", (), {"base_url": "http://x", "model": "parent-model"})()
        self.registry = type("R", (), {})()


def _patch_factory_build(monkeypatch, loop):
    monkeypatch.setattr(cli, "_build_agent",
                        lambda a: (None, None, None, loop, None))


def test_subagent_factory_routes_role_via_config_for_role(tmp_path, monkeypatch):
    loop = _FakeLoopWithConfig()
    _patch_factory_build(monkeypatch, loop)
    routed = type("C", (), {"base_url": "http://x", "model": "summary-model"})()
    monkeypatch.setattr(cli, "config_for_role", lambda role, url, prof: routed)
    monkeypatch.setattr(cli, "load_profiles", lambda: {})

    factory = cli._make_subagent_factory(argparse.Namespace(workspace=str(tmp_path)))
    built = factory("summarize this", None, 1, "summary")
    assert built.config.model == "summary-model"   # routed to the role's model


def test_subagent_factory_no_role_keeps_parent_config(tmp_path, monkeypatch):
    loop = _FakeLoopWithConfig()
    _patch_factory_build(monkeypatch, loop)
    called = {"routed": False}
    monkeypatch.setattr(cli, "config_for_role",
                        lambda *a: called.__setitem__("routed", True) or None)

    factory = cli._make_subagent_factory(argparse.Namespace(workspace=str(tmp_path)))
    built = factory("do it", None, 1, "")            # no role
    assert built.config.model == "parent-model"      # unchanged
    assert called["routed"] is False                 # routing not even attempted


def test_subagent_factory_unresolved_role_falls_back(tmp_path, monkeypatch):
    loop = _FakeLoopWithConfig()
    _patch_factory_build(monkeypatch, loop)
    monkeypatch.setattr(cli, "config_for_role", lambda *a: None)  # unresolved
    monkeypatch.setattr(cli, "load_profiles", lambda: {})

    factory = cli._make_subagent_factory(argparse.Namespace(workspace=str(tmp_path)))
    built = factory("g", None, 1, "bogus-role")
    assert built.config.model == "parent-model"      # kept parent config, no crash


def _make_git_repo(path):
    _sp.run(["git", "init", "-q"], cwd=path, check=True)
    _sp.run(["git", "config", "user.email", "t@e.com"], cwd=path, check=True)
    _sp.run(["git", "config", "user.name", "T"], cwd=path, check=True)
    (path / "f.txt").write_text("v1\n")
    _sp.run(["git", "add", "."], cwd=path, check=True)
    _sp.run(["git", "commit", "-qm", "init"], cwd=path, check=True)


def test_cmd_undo_uses_git_when_repo(tmp_path, capsys):
    _make_git_repo(tmp_path)
    from revenant_cli.git_checkpoint import GitCheckpointer
    cp = GitCheckpointer(tmp_path)
    cp.snapshot("run_bash", {"command": "x"})
    (tmp_path / "f.txt").write_text("MUTATED\n")
    (tmp_path / "shell_artifact.txt").write_text("from bash\n")

    rc = cli.cmd_undo(_undo_args(tmp_path, all_=True))
    assert rc == 0
    # git-native undo reverts the tracked edit AND removes the shell artifact.
    assert (tmp_path / "f.txt").read_text() == "v1\n"
    assert not (tmp_path / "shell_artifact.txt").exists()


def test_cmd_undo_git_nothing_to_undo(tmp_path, capsys):
    _make_git_repo(tmp_path)
    rc = cli.cmd_undo(_undo_args(tmp_path))
    assert rc == 0
    assert "nothing to undo" in capsys.readouterr().out


# --- run --skill one-shot (P4 follow-up) ------------------------------------

def test_run_goal_is_optional_with_skill():
    p = cli.build_parser()
    ns = p.parse_args(cli._normalize_argv(["run", "--skill", "run-tests"]))
    assert ns.command == "run" and ns.skill == "run-tests" and ns.goal == ""


def test_run_requires_goal_or_skill(tmp_path, capsys, monkeypatch):
    # Neither goal nor skill -> error, exit 2, no agent built.
    monkeypatch.setattr(cli, "_build_agent", lambda a: (_ for _ in ()).throw(AssertionError("should not build")))
    args = _agent_args(tmp_path, read_only=True)
    args.goal = ""
    args.skill = None
    rc = cli.cmd_run(args)
    assert rc == 2
    assert "provide a GOAL" in capsys.readouterr().err


def test_run_skill_loads_body_and_scopes_tools(tmp_path, monkeypatch):
    from nerva_agent.skills import Skill as _Skill
    from nerva_agent.agent_tools import Tool as _Tool, ToolRegistry as _Registry

    skill = _Skill(name="rt", description="d", body="RUN THE SUITE", tools=["run_bash"])
    reg = _Registry([_Tool(name=n, description=n, run=lambda **k: "ok")
                     for n in ("run_bash", "read_file")])

    class L:
        def __init__(self):
            self._skills = {"rt": skill}
            self._base_preamble = "BASE"
            self.system_preamble = "BASE"
            self.registry = reg
            self._mcp_clients = []
            self.ran = None
        def run(self, goal, history=None):
            self.ran = goal
            return type("R", (), {"messages": [], "stopped_reason": "final", "answer": ""})()

    loop = L()
    color = cli._color(False)
    monkeypatch.setattr(cli, "_build_agent",
        lambda a: (Path(tmp_path), type("C", (), {"model": "m"})(),
                   type("Rec", (), {"note": "n"})(), loop, color))
    args = _agent_args(tmp_path, read_only=False)
    args.goal = ""
    args.skill = "rt"
    rc = cli.cmd_run(args)
    assert rc == 0
    assert loop.ran == "RUN THE SUITE"          # skill body became the goal
    assert "RUN THE SUITE" in loop.system_preamble
    assert loop.registry.names() == ["run_bash"]  # scoped to the skill's tools


def test_run_unknown_skill_errors(tmp_path, monkeypatch, capsys):
    class L:
        _skills = {}
        _base_preamble = "B"
        system_preamble = "B"
        registry = None
        _mcp_clients = []
    loop = L()
    color = cli._color(False)
    monkeypatch.setattr(cli, "_build_agent",
        lambda a: (Path(tmp_path), type("C", (), {"model": "m"})(),
                   type("Rec", (), {"note": "n"})(), loop, color))
    args = _agent_args(tmp_path, read_only=True)
    args.goal = ""
    args.skill = "ghost"
    rc = cli.cmd_run(args)
    assert rc == 2
    assert "no skill named" in capsys.readouterr().err


# --- loop --watch trigger (F13.3) -------------------------------------------

def test_loop_watch_parser():
    p = cli.build_parser()
    ns = p.parse_args(cli._normalize_argv(["loop", "g", "--watch", "src/**"]))
    assert ns.watch == "src/**"


def test_tree_signature_respects_ignore(tmp_path):
    (tmp_path / "keep.py").write_text("x")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "skip.py").write_text("y")
    (tmp_path / ".gitignore").write_text("vendor/\n")
    sig = cli._tree_signature(tmp_path, "**/*.py")
    assert "keep.py" in sig
    assert "vendor/skip.py" not in sig


def test_loop_watch_reruns_on_change(tmp_path, monkeypatch):
    (tmp_path / "f.py").write_text("v1")
    runs = {"n": 0}
    monkeypatch.setattr(cli, "_cmd_loop_once", lambda a: runs.__setitem__("n", runs["n"] + 1) or 0)

    # First tick: no change (no rerun). Second tick: mutate the file, expect a rerun.
    def make_change():
        (tmp_path / "f.py").write_text("v2")

    ticks = [lambda: None, make_change]  # each yielded item is called as sleep()
    args = argparse.Namespace(workspace=str(tmp_path), watch="**/*.py",
                              watch_interval=0.0, no_color=True)
    cli._cmd_loop_watch(args, watch_ticks=iter(ticks))
    # initial run + one rerun after the change = 2
    assert runs["n"] == 2


# --- loop --every trigger (W3, ADR-0020) ------------------------------------

def test_loop_every_parser():
    p = cli.build_parser()
    ns = p.parse_args(cli._normalize_argv(["loop", "g", "--every", "5"]))
    assert ns.every == 5.0


def test_loop_every_reruns_each_interval(tmp_path, monkeypatch):
    runs = {"n": 0}
    monkeypatch.setattr(cli, "_cmd_loop_once", lambda a: runs.__setitem__("n", runs["n"] + 1) or 0)
    # Three interval ticks -> initial run + 3 reruns = 4 (no change detection;
    # time-triggered fires every interval unconditionally).
    ticks = [lambda: None, lambda: None, lambda: None]
    args = argparse.Namespace(workspace=str(tmp_path), every=0.0, no_color=True)
    cli._cmd_loop_every(args, ticks=iter(ticks))
    assert runs["n"] == 4


def test_loop_dispatch_routes_every(tmp_path, monkeypatch):
    # cmd_loop must dispatch to the --every branch when --every is set.
    called = {"every": False}
    monkeypatch.setattr(cli, "_cmd_loop_every", lambda a, t=None: called.__setitem__("every", True) or 0)
    args = argparse.Namespace(workspace=str(tmp_path), every=5.0, watch=None, no_color=True)
    cli.cmd_loop(args)
    assert called["every"] is True


# --- H3: --plan decompose + per-step driver (ADR-0014) ----------------------

def test_run_plan_flag_parses():
    p = cli.build_parser()
    ns = p.parse_args(cli._normalize_argv(["run", "build it", "--plan"]))
    assert ns.command == "run" and ns.plan is True


class _PlanLoop:
    """A loop whose run() records goals; model call is patched separately.

    `stopped` is the reason returned every call. `script` (optional) overrides it
    with a per-call sequence of reasons (list), for adaptive-driver tests where a
    step must fail then succeed.
    """
    def __init__(self, stopped="final", script=None):
        self.config = type("C", (), {"model": "m"})()
        self.calls = []
        self._stopped = stopped
        self._script = list(script) if script else None
        self._mcp_clients = []
    def run(self, goal, history=None):
        self.calls.append(goal)
        reason = self._script.pop(0) if self._script else self._stopped
        return type("R", (), {
            "messages": (history or []) + [{"role": "user", "content": goal[:20]}],
            "stopped_reason": reason, "answer": "",
        })()


def test_run_planned_drives_steps_in_order(monkeypatch, capsys):
    loop = _PlanLoop(stopped="final")
    # Patch the planning call to return a 3-step checklist.
    monkeypatch.setattr(cli, "_make_plan",
        lambda lp, goal: __import__("nerva_agent.planner", fromlist=["parse_plan"])
                          .parse_plan("1. one\n2. two\n3. three", goal))
    rc = cli._run_planned(loop, "big goal", cli._color(False))
    assert rc == 0
    assert loop.calls == ["one", "two", "three"]  # each step ran, in order
    assert "plan complete: 3 step(s)" in capsys.readouterr().out


def _plan(*steps):
    """A fake _make_plan returning a checklist of the given step goals."""
    text = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    from nerva_agent.planner import parse_plan
    return lambda lp, goal: parse_plan(text, goal)


# --- P1 (ADR-0023): adaptive driver -----------------------------------------

def test_run_planned_retries_a_failed_step_then_advances(monkeypatch, capsys):
    # Step "one" fails once (max_steps) then succeeds on retry; "two" succeeds.
    loop = _PlanLoop(script=["max_steps", "final", "final"])
    monkeypatch.setattr(cli, "_make_plan", _plan("one", "two"))
    rc = cli._run_planned(loop, "g", cli._color(False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "retrying step" in out and "plan complete: 2 step(s)" in out
    # "one" ran twice (fail + retry), then "two".
    assert loop.calls[0] == "one" and "one" in loop.calls[1] and loop.calls[2] == "two"


def test_run_planned_replans_remaining_when_retry_fails(monkeypatch, capsys):
    # "one" fails + retry fails -> re-plan returns ["recover"], which succeeds.
    loop = _PlanLoop(script=["max_steps", "max_steps", "final"])
    monkeypatch.setattr(cli, "_make_plan", _plan("one", "two"))
    monkeypatch.setattr(cli, "_plan_call", lambda lp, prompt: "1. recover")
    rc = cli._run_planned(loop, "g", cli._color(False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "re-planning remaining steps" in out
    assert "recover" in loop.calls   # the re-planned step ran


def test_run_planned_halts_when_budget_exhausted(monkeypatch, capsys):
    # Everything fails; retries + re-plans (which keep failing) eventually halt.
    loop = _PlanLoop(stopped="max_steps")
    monkeypatch.setattr(cli, "_make_plan", _plan("one", "two"))
    monkeypatch.setattr(cli, "_plan_call", lambda lp, prompt: "1. still-fails")
    rc = cli._run_planned(loop, "g", cli._color(False), max_step_retries=1, max_replans=2)
    assert rc == 3
    assert "budget spent" in capsys.readouterr().out


def test_run_planned_unparseable_replan_keeps_current_steps(monkeypatch, capsys):
    # Re-plan returns junk -> keep current step, but the re-plan attempt is counted
    # so a stuck plan still terminates.
    loop = _PlanLoop(stopped="max_steps")
    monkeypatch.setattr(cli, "_make_plan", _plan("one"))
    monkeypatch.setattr(cli, "_plan_call", lambda lp, prompt: "sorry, no")
    rc = cli._run_planned(loop, "g", cli._color(False), max_step_retries=0, max_replans=1)
    assert rc == 3
    assert "unparseable" in capsys.readouterr().out


# --- P2 (ADR-0023): phase-aware routing -------------------------------------

def test_planner_config_falls_back_to_loop_config():
    base = type("C", (), {"model": "qwen2.5:7b"})()
    loop = type("L", (), {"config": base})()
    assert cli._planner_config(loop).model == "qwen2.5:7b"


def test_planner_config_uses_routed_config_when_set():
    base = type("C", (), {"model": "qwen2.5:7b"})()
    strong = type("C", (), {"model": "qwen2.5:14b"})()
    loop = type("L", (), {"config": base, "_planner_config": strong})()
    assert cli._planner_config(loop).model == "qwen2.5:14b"


def test_make_plan_uses_routed_planner_config(monkeypatch):
    # _make_plan must call the model with the ROUTED planner config, not loop.config.
    base = type("C", (), {"model": "qwen2.5:7b"})()
    strong = type("C", (), {"model": "qwen2.5:14b"})()
    loop = type("L", (), {"config": base, "_planner_config": strong})()
    seen = {}
    monkeypatch.setattr("nerva_core.local_llm_writer.call_model",
                        lambda cfg, msgs: seen.setdefault("model", cfg.model) or "1. a\n2. b")
    cli._make_plan(loop, "goal")
    assert seen["model"] == "qwen2.5:14b"   # planned with the strong model


def test_run_planned_single_step_fallback(monkeypatch):
    loop = _PlanLoop(stopped="final")
    # Planner returns prose -> single-step plan (whole goal).
    monkeypatch.setattr(cli, "_make_plan",
        lambda lp, goal: __import__("nerva_agent.planner", fromlist=["parse_plan"])
                          .parse_plan("I'll just do it.", goal))
    rc = cli._run_planned(loop, "the whole goal", cli._color(False))
    assert rc == 0
    assert loop.calls == ["the whole goal"]  # ran the goal directly, no decomposition


def test_run_planned_threads_history(monkeypatch):
    loop = _PlanLoop(stopped="final")
    monkeypatch.setattr(cli, "_make_plan",
        lambda lp, goal: __import__("nerva_agent.planner", fromlist=["parse_plan"])
                          .parse_plan("1. a\n2. b", goal))
    cli._run_planned(loop, "g", cli._color(False))
    # Second step ran (history threading verified by both steps executing).
    assert loop.calls == ["a", "b"]


# --- doctor + models commands (U2, ADR-0016) --------------------------------

def _doctor_args(workspace, base_url="", model=""):
    return argparse.Namespace(workspace=str(workspace), base_url=base_url,
                              model=model, no_color=True)


def test_cmd_models_lists_pulled(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.preflight, "list_local_models",
                        lambda *a, **k: ["gemma:latest", "qwen2.5-coder:7b"])
    rc = cli.cmd_models(_doctor_args(tmp_path))
    assert rc == 0
    out = capsys.readouterr().out
    assert "gemma:latest" in out and "qwen2.5-coder:7b" in out


def test_cmd_models_unreachable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.preflight, "list_local_models", lambda *a, **k: None)
    rc = cli.cmd_models(_doctor_args(tmp_path))
    assert rc == 1
    assert "ollama serve" in capsys.readouterr().err


def test_cmd_doctor_healthy(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.preflight, "list_local_models",
                        lambda *a, **k: ["qwen2.5-coder:7b"])
    rc = cli.cmd_doctor(_doctor_args(tmp_path, model="qwen2.5-coder:7b"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "Ollama reachable" in out and "ready to run" in out


def test_cmd_doctor_model_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.preflight, "list_local_models", lambda *a, **k: ["gemma:latest"])
    rc = cli.cmd_doctor(_doctor_args(tmp_path, model="qwen2.5-coder:7b"))
    assert rc == 1
    assert "ollama pull qwen2.5-coder:7b" in capsys.readouterr().out


def test_cmd_doctor_unreachable(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.preflight, "list_local_models", lambda *a, **k: None)
    rc = cli.cmd_doctor(_doctor_args(tmp_path))
    assert rc == 1
    assert "ollama serve" in capsys.readouterr().out


def test_doctor_models_in_parser():
    p = cli.build_parser()
    assert p.parse_args(["doctor", "--no-color"]).command == "doctor"
    assert p.parse_args(["models"]).command == "models"


# --- config subcommand (show/set) -------------------------------------------

def test_config_set_parser():
    p = cli.build_parser()
    a = p.parse_args(cli._normalize_argv(["config", "set", "model=qwen2.5:7b", "--project"]))
    assert a.command == "config" and a.config_action == "set"
    assert a.assignment == "model=qwen2.5:7b" and a.project is True


def test_config_set_then_show_reflects_it(tmp_path, capsys):
    rc = cli.cmd_config(argparse.Namespace(
        workspace=str(tmp_path), no_color=True, config_action="set",
        assignment="model=qwen2.5:14b", project=True))
    assert rc == 0
    capsys.readouterr()
    rc = cli.cmd_config(argparse.Namespace(
        workspace=str(tmp_path), no_color=True, config_action="show"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "qwen2.5:14b" in out and "model" in out


def test_config_set_unknown_key_errors(tmp_path, capsys):
    rc = cli.cmd_config(argparse.Namespace(
        workspace=str(tmp_path), no_color=True, config_action="set",
        assignment="bogus=x", project=True))
    assert rc == 2
    assert "unknown key" in capsys.readouterr().err


def test_config_set_bad_assignment_errors(tmp_path, capsys):
    rc = cli.cmd_config(argparse.Namespace(
        workspace=str(tmp_path), no_color=True, config_action="set",
        assignment="no-equals-sign", project=True))
    assert rc == 2


def test_config_set_coerces_int(tmp_path):
    from revenant_cli.config import load_config
    cli.cmd_config(argparse.Namespace(
        workspace=str(tmp_path), no_color=True, config_action="set",
        assignment="max_steps=30", project=True))
    assert load_config(tmp_path)["max_steps"] == 30


def test_config_no_longer_a_stub(capsys):
    # `main(["config", "show"])` must not print the old "not implemented" message.
    import io, contextlib
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        cli.main(["config", "show", "--workspace", "."])
    assert "not implemented" not in err.getvalue()


# --- M2 (ADR-0022): auto-recall into the preamble ---------------------------

def _mem_loop(store, base="BASE PREAMBLE"):
    return type("L", (), {"_base_preamble": base, "_memory": store,
                          "system_preamble": base})()


def test_recall_block_formats_hits():
    from nerva_agent.memory_store import MemoryStore
    s = MemoryStore(":memory:")
    s.remember("this project uses pytest")
    block = cli._recall_block(s, "how to run tests with pytest", 5)
    assert "Project memory" in block and "pytest" in block


def test_recall_block_empty_when_no_hits_or_no_store():
    from nerva_agent.memory_store import MemoryStore
    assert cli._recall_block(None, "x", 5) == ""
    assert cli._recall_block(MemoryStore(":memory:"), "nothing here", 5) == ""


def test_apply_memory_recall_injects_and_is_byte_parity_when_empty():
    from nerva_agent.memory_store import MemoryStore
    s = MemoryStore(":memory:")
    s.remember("editing config.py breaks the loader — use write_scalar")
    loop = _mem_loop(s)
    cli._apply_memory_recall(loop, "change config.py", 5)
    assert "write_scalar" in loop.system_preamble and loop.system_preamble.startswith("BASE PREAMBLE")

    empty = _mem_loop(MemoryStore(":memory:"))
    cli._apply_memory_recall(empty, "anything", 5)
    assert empty.system_preamble == "BASE PREAMBLE"   # byte-identical


def test_apply_memory_recall_is_idempotent_across_turns():
    from nerva_agent.memory_store import MemoryStore
    s = MemoryStore(":memory:")
    s.remember("fact about pytest")
    loop = _mem_loop(s)
    cli._apply_memory_recall(loop, "pytest", 5)
    once = loop.system_preamble
    cli._apply_memory_recall(loop, "pytest", 5)   # again — must not stack
    assert loop.system_preamble == once


def test_apply_memory_recall_noop_without_store():
    loop = type("L", (), {"_base_preamble": "BASE", "system_preamble": "BASE"})()
    cli._apply_memory_recall(loop, "x", 5)   # no _memory attr -> no-op, no crash
    assert loop.system_preamble == "BASE"


# --- M3 (ADR-0022): gated end-of-run memory suggestions ---------------------

def _suggest_setup(monkeypatch, store, answer="did the thing", suggest_text="- uses pytest\n- API in packages/api"):
    """Wire a fake model (returns suggest_text) + a final result + a mem loop."""
    monkeypatch.setattr("nerva_core.local_llm_writer.call_model", lambda cfg, msgs: suggest_text)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    loop = type("L", (), {"_memory": store, "config": type("C", (), {})()})()
    result = type("R", (), {"stopped_reason": "final", "answer": answer})()
    args = argparse.Namespace(no_memory=False, no_memory_suggest=False, no_color=True)
    return loop, result, args


def test_suggest_saves_only_confirmed_facts(monkeypatch):
    from nerva_agent.memory_store import MemoryStore
    store = MemoryStore(":memory:")
    loop, result, args = _suggest_setup(monkeypatch, store)
    answers = iter(["y", "n"])   # confirm the first, decline the second
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    cli._maybe_suggest_memories(loop, args, "run the tests", result, None)
    saved = [m.content for m in store.list_all()]
    assert saved == ["uses pytest"]   # only the confirmed one


def test_suggest_skips_when_non_interactive(monkeypatch):
    from nerva_agent.memory_store import MemoryStore
    store = MemoryStore(":memory:")
    loop, result, args = _suggest_setup(monkeypatch, store)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)  # piped/CI
    cli._maybe_suggest_memories(loop, args, "g", result, None)
    assert store.count() == 0   # never writes unattended


def test_suggest_skips_when_flag_set(monkeypatch):
    from nerva_agent.memory_store import MemoryStore
    store = MemoryStore(":memory:")
    loop, result, args = _suggest_setup(monkeypatch, store)
    args.no_memory_suggest = True
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    cli._maybe_suggest_memories(loop, args, "g", result, None)
    assert store.count() == 0


def test_suggest_skips_when_run_not_final(monkeypatch):
    from nerva_agent.memory_store import MemoryStore
    store = MemoryStore(":memory:")
    loop, result, args = _suggest_setup(monkeypatch, store)
    result.stopped_reason = "max_steps"   # didn't finish
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    cli._maybe_suggest_memories(loop, args, "g", result, None)
    assert store.count() == 0


def test_suggest_handles_none_from_model(monkeypatch):
    from nerva_agent.memory_store import MemoryStore
    store = MemoryStore(":memory:")
    loop, result, args = _suggest_setup(monkeypatch, store, suggest_text="NONE")
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    cli._maybe_suggest_memories(loop, args, "g", result, None)
    assert store.count() == 0   # nothing proposed -> nothing to confirm


# --- M4 (ADR-0022): memory subcommand ---------------------------------------

def test_memory_parser():
    p = cli.build_parser()
    a = p.parse_args(cli._normalize_argv(["memory", "forget", "3"]))
    assert a.command == "memory" and a.memory_action == "forget" and a.id == 3


def _seed_memory(ws):
    from nerva_agent.memory_store import MemoryStore
    s = MemoryStore(f"{ws}/.aibot/memory.db")
    s.remember("uses pytest"); s.remember("API in packages/api")
    s.close()


def test_memory_list(tmp_path, capsys):
    _seed_memory(tmp_path)
    rc = cli.cmd_memory(argparse.Namespace(workspace=str(tmp_path), no_color=True, memory_action="list"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "uses pytest" in out and "packages/api" in out


def test_memory_forget(tmp_path, capsys):
    _seed_memory(tmp_path)
    rc = cli.cmd_memory(argparse.Namespace(workspace=str(tmp_path), no_color=True, memory_action="forget", id=1))
    assert rc == 0
    # #1 is gone, #2 remains
    capsys.readouterr()
    cli.cmd_memory(argparse.Namespace(workspace=str(tmp_path), no_color=True, memory_action="list"))
    assert "uses pytest" not in capsys.readouterr().out


def test_memory_forget_unknown_id(tmp_path, capsys):
    _seed_memory(tmp_path)
    rc = cli.cmd_memory(argparse.Namespace(workspace=str(tmp_path), no_color=True, memory_action="forget", id=999))
    assert rc == 2


def test_memory_clear(tmp_path, capsys):
    _seed_memory(tmp_path)
    cli.cmd_memory(argparse.Namespace(workspace=str(tmp_path), no_color=True, memory_action="clear"))
    capsys.readouterr()
    cli.cmd_memory(argparse.Namespace(workspace=str(tmp_path), no_color=True, memory_action="list"))
    assert "no project memories" in capsys.readouterr().out


def test_memory_empty_message(tmp_path, capsys):
    rc = cli.cmd_memory(argparse.Namespace(workspace=str(tmp_path), no_color=True, memory_action="list"))
    assert rc == 0 and "no project memories" in capsys.readouterr().out


# --- setup / first-run polish -----------------------------------------------

def test_best_pulled_model_prefers_coder():
    assert cli._best_pulled_model(["gemma:latest", "qwen2.5-coder:7b", "qwen2.5:14b"]) == "qwen2.5-coder:7b"
    assert cli._best_pulled_model(["gemma:latest", "qwen2.5:14b"]) == "qwen2.5:14b"  # qwen next
    assert cli._best_pulled_model(["llama3:8b", "mistral:7b"]) == "llama3:8b"        # else first


def test_picker_auto_selects_when_non_interactive(monkeypatch, tmp_path, capsys):
    from revenant_cli import preflight
    # Non-TTY + reachable + models pulled -> auto-pick (no hard-fail).
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    monkeypatch.setattr(cli, "write_model_choice", lambda m, scope="user": tmp_path / "cfg")
    pf = preflight.PreflightResult(ok=False, reachable=True, model_present=False,
                                   available=["gemma:latest", "qwen2.5-coder:7b"])
    config = type("C", (), {"model": "qwen2.5-coder:14b"})()
    chosen = cli._offer_model_picker(pf, config, cli._color(False))
    assert chosen == "qwen2.5-coder:7b"     # preferred coder model
    assert config.model == "qwen2.5-coder:7b"


def test_picker_returns_none_when_nothing_pulled(monkeypatch):
    from revenant_cli import preflight
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    pf = preflight.PreflightResult(ok=False, reachable=True, model_present=False, available=[])
    assert cli._offer_model_picker(pf, type("C", (), {"model": "x"})(), cli._color(False)) is None


def test_picker_returns_none_when_server_down(monkeypatch):
    from revenant_cli import preflight
    pf = preflight.PreflightResult(ok=False, reachable=False, model_present=False, available=[])
    assert cli._offer_model_picker(pf, type("C", (), {"model": "x"})(), cli._color(False)) is None


# --- V3 (ADR-0017): TUI enablement + REPL fallback ---------------------------

def _tui_args(**kw):
    ns = argparse.Namespace(tui=False, no_tui=False)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_tui_disabled_by_no_tui(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    monkeypatch.setattr(cli, "_tui_enabled", cli._tui_enabled)  # use real impl
    assert cli._tui_enabled(_tui_args(no_tui=True)) is False


def test_tui_disabled_when_not_a_tty(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False, raising=False)
    # Even with --tui forced, a non-TTY can't host the app.
    assert cli._tui_enabled(_tui_args(tui=True)) is False


def test_tui_disabled_when_textual_absent(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    monkeypatch.setattr("revenant_cli.tui.tui_available", lambda: False)
    assert cli._tui_enabled(_tui_args(tui=True)) is False


def test_tui_enabled_when_available_and_tty(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    monkeypatch.setattr("revenant_cli.tui.tui_available", lambda: True)
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert cli._tui_enabled(_tui_args()) is True


def test_no_color_env_disables_tui(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    monkeypatch.setattr("revenant_cli.tui.tui_available", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    # NO_COLOR turns off the auto path; without an explicit --tui it stays off.
    assert cli._tui_enabled(_tui_args()) is False


def test_tui_flags_in_parser():
    p = cli.build_parser()
    a = p.parse_args(["chat", "--tui"])
    assert a.tui is True and a.no_tui is False
    b = p.parse_args(["chat", "--no-tui"])
    assert b.no_tui is True
