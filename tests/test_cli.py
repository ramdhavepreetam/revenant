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
        return object()

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
