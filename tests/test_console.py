"""Tests for the Console abstraction (U3, ADR-0016).

The critical test is byte-parity: PlainConsole must reproduce the legacy
make_printer output EXACTLY, so switching to the console layer is invisible when
rich isn't used. Also: backend selection (rich optional), NO_COLOR, and that the
rich path renders without raising.
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout, redirect_stderr

import pytest

from nerva_agent.agent_loop import AgentEvent
from revenant_cli.console import PlainConsole, make_console, unified_diff


# --- the legacy renderer, inlined, to assert byte-parity against -------------
_C = {
    "dim": "\033[2m", "cyan": "\033[36m", "green": "\033[32m",
    "yellow": "\033[33m", "red": "\033[31m", "bold": "\033[1m", "reset": "\033[0m",
}


def _legacy(ev: AgentEvent, color: dict) -> tuple[str, str]:
    """Reproduces the pre-console make_printer for one event → (stdout, stderr)."""
    c = color
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        if ev.kind == "assistant" and ev.text:
            print(f"{c['dim']}{ev.text}{c['reset']}")
        elif ev.kind == "action":
            args = ", ".join(f"{k}={v!r}" for k, v in ev.args.items())
            print(f"{c['cyan']}→ {ev.tool}({args}){c['reset']}")
        elif ev.kind == "observation":
            body = ev.text if len(ev.text) <= 800 else ev.text[:800] + " …"
            indented = "\n".join("  " + line for line in body.splitlines())
            print(f"{c['dim']}{indented}{c['reset']}")
        elif ev.kind == "final":
            print(f"\n{c['green']}{c['bold']}{ev.text}{c['reset']}")
        elif ev.kind == "error":
            import sys
            print(f"{c['red']}error: {ev.text}{c['reset']}", file=sys.stderr)
        elif ev.kind == "limit":
            import sys
            print(f"{c['yellow']}[{ev.text}]{c['reset']}", file=sys.stderr)
        elif ev.kind == "compact":
            import sys
            print(f"{c['dim']}[context: {ev.text}]{c['reset']}", file=sys.stderr)
    return out.getvalue(), err.getvalue()


def _plain(ev: AgentEvent, *, color: bool) -> tuple[str, str]:
    out, err = io.StringIO(), io.StringIO()
    con = PlainConsole(color=color)
    with redirect_stdout(out), redirect_stderr(err):
        con.event(ev)
    return out.getvalue(), err.getvalue()


_EVENTS = [
    AgentEvent("assistant", text="I will read the file."),
    AgentEvent("action", tool="read_file", args={"path": "a.py"}),
    AgentEvent("observation", text="line1\nline2\nline3"),
    AgentEvent("observation", text="x" * 1000),  # exercises the 800-char clip
    AgentEvent("final", text="Done."),
    AgentEvent("error", text="boom"),
    AgentEvent("limit", text="hit max_steps=15"),
    AgentEvent("compact", text="folded 3 turns"),
    AgentEvent("approval", tool="write_file", args={"path": "x"}),  # ignored
]


@pytest.mark.parametrize("ev", _EVENTS, ids=lambda e: e.kind + (f"-{len(e.text)}" if e.text else ""))
@pytest.mark.parametrize("color", [True, False])
def test_plain_console_byte_parity_with_legacy(ev, color):
    palette = _C if color else {k: "" for k in _C}
    assert _plain(ev, color=color) == _legacy(ev, palette)


# --- backend selection -------------------------------------------------------

def test_make_console_plain_when_no_tty(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False, raising=False)
    con = make_console(color=True, no_color_env=False)
    assert isinstance(con, PlainConsole)


def test_make_console_plain_when_no_color_env(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    con = make_console(color=True, no_color_env=True)
    assert isinstance(con, PlainConsole)


def test_make_console_plain_when_rich_absent(monkeypatch):
    # Force the guarded import to fail → PlainConsole even on a TTY.
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name.startswith("revenant_cli._rich_console") or name == "rich":
            raise ImportError("no rich")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    con = make_console(color=True, no_color_env=False)
    assert isinstance(con, PlainConsole)


# --- diff helper -------------------------------------------------------------

def test_unified_diff():
    d = unified_diff("f.py", "a = 1\n", "a = 2\n")
    assert "-a = 1" in d and "+a = 2" in d
    assert "f.py (before)" in d and "f.py (after)" in d


# --- V2 (ADR-0017): sub-agent + new event kinds ------------------------------

def test_root_events_unprefixed_byte_parity_preserved():
    # An event with agent="" must still match the legacy renderer exactly.
    ev = AgentEvent("action", tool="read_file", args={"path": "a.py"})
    assert _plain(ev, color=True) == _legacy(ev, _C)


def test_subagent_action_is_prefixed():
    ev = AgentEvent("action", tool="grep", args={"q": "x"}, agent="fix-tests")
    out, _err = _plain(ev, color=False)
    assert out.startswith("[sub:fix-tests] ")
    assert "→ grep" in out


def test_subagent_final_carries_label():
    ev = AgentEvent("final", text="done", agent="fix-tests")
    out, _err = _plain(ev, color=False)
    assert "[sub:fix-tests]" in out and "done" in out


def test_agent_start_and_end_render():
    con = PlainConsole(color=False)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        con.event(AgentEvent("agent_start", text="refactor auth", agent="refactor-auth"))
        con.event(AgentEvent("agent_end", text="Sub-agent completed", agent="refactor-auth"))
    text = err.getvalue()
    assert "sub-agent [refactor-auth]" in text
    assert "refactor auth" in text and "done" in text


def test_context_event_is_silent_in_plain():
    from nerva_agent.agent_loop import ContextInfo
    ev = AgentEvent("context", context=ContextInfo(100, 6000, folded=False))
    out, err = _plain(ev, color=False)
    assert out == "" and err == ""  # feeds the TUI gauge, not the scroll


# --- W1 (ADR-0019): token streaming render -----------------------------------

def _drive(events):
    """Feed a sequence of events to one PlainConsole; return (stdout, stderr)."""
    con = PlainConsole(color=False)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        for ev in events:
            con.event(ev)
    return out.getvalue(), err.getvalue()


def test_token_events_render_deltas_inline():
    out, _err = _drive([
        AgentEvent("token", text="Hello, "),
        AgentEvent("token", text="world."),
        AgentEvent("final", text="Hello, world."),
    ])
    # The streamed deltas appear inline...
    assert "Hello, world." in out
    # ...and the final answer is NOT printed twice (streamed once + final line).
    assert out.count("Hello, world.") == 1


def test_final_without_streaming_prints_the_answer():
    # No token events -> the final event prints the whole answer (byte-parity).
    out, _err = _drive([AgentEvent("final", text="Just the answer.")])
    assert "Just the answer." in out


def test_streamed_assistant_turn_not_duplicated():
    # A tool turn: content streamed as tokens, then an "assistant" echo of the
    # same text must NOT reprint it (the deltas already showed it).
    out, _err = _drive([
        AgentEvent("token", text="calling the tool"),
        AgentEvent("assistant", text="calling the tool"),
        AgentEvent("action", tool="read_file", args={"path": "a.py"}),
    ])
    assert out.count("calling the tool") == 1


def test_token_event_is_silent_when_not_streaming_elsewhere():
    # A lone token still writes its delta (no crash, no stderr noise).
    out, err = _plain(AgentEvent("token", text="x"), color=False)
    assert "x" in out and err == ""


# --- approval confirm (plain) ------------------------------------------------

def test_plain_confirm_yes(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_a: "y")
    assert PlainConsole(color=False).confirm("Run this?") is True


def test_plain_confirm_declines_on_non_yes(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda *_a: "maybe")
    assert PlainConsole(color=False).confirm("Run this?") is False


# --- rich path (skipped if rich not installed) -------------------------------

def test_rich_console_selected_and_renders(monkeypatch):
    pytest.importorskip("rich")
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    con = make_console(color=True, no_color_env=False)
    assert getattr(con, "is_rich", False) is True
    # Each method should render without raising (output goes to rich's console).
    con.event(AgentEvent("action", tool="edit_file", args={"path": "a.py"}))
    con.event(AgentEvent("final", text="ok"))
    con.session_header(model="m", workspace="/w", mode="approval-gated", extras=["graph: 5"])
    con.approval("edit_file", {"path": "a.py", "old": "x=1", "new": "x=2"})
