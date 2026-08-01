"""V3–V5 (ADR-0017): the Textual TUI app, driven by Textual's test pilot.

Skipped entirely when `textual` isn't installed (it's an optional dependency), so
CI without `[tui]` stays green. Each test wraps the async pilot in `asyncio.run`
so it needs no pytest-asyncio configuration.
"""
from __future__ import annotations

import asyncio
import time

import pytest

pytest.importorskip("textual")

from nerva_agent.agent_loop import AgentEvent, ContextInfo  # noqa: E402
from revenant_cli.tui.app import RevenantApp  # noqa: E402
from revenant_cli.tui.screens import ApprovalScreen  # noqa: E402
from revenant_cli.tui.widgets import (  # noqa: E402
    ActivityLog, ContextGauge, StatusBar, StreamLine,
)


class _FakeLoop:
    """A loop stand-in: emits scripted events through on_event, returns a result.

    `on_event`/`approve` are set by the app before each run (as the real loop's
    attributes are). `emit` is the list of events to replay per run."""

    def __init__(self, emit=None, *, approvals=None):
        self._skills = {}
        self.on_event = None
        self.approve = None
        self._emit = emit or []
        self._approvals = approvals  # if set, call self.approve and record results
        self.runs = 0
        self.approved = []

    def run(self, goal, history=None, should_stop=None):
        self.runs += 1
        for ev in self._emit:
            if self.on_event:
                self.on_event(ev)
        if self._approvals is not None:
            for tool, args in self._approvals:
                self.approved.append(self.approve(tool, args))
        return type("R", (), {"messages": [{"role": "user", "content": goal}]})()


def _run(coro):
    return asyncio.run(coro)


def _app(loop, **kw):
    return RevenantApp(loop=loop, workspace="/tmp/ws", model="qwen2.5-coder:7b",
                       mode=kw.pop("mode", "yolo"), **kw)


# --- V3: mounts + streams --------------------------------------------------

def test_app_mounts_with_core_widgets():
    async def go():
        app = _app(_FakeLoop())
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one(StatusBar) is not None
            assert app.query_one(ContextGauge) is not None
            assert app.query_one(ActivityLog) is not None
            assert app.query_one("#prompt") is not None
    _run(go())


def test_submitting_a_goal_runs_the_loop_and_streams():
    events = [
        AgentEvent("action", tool="read_file", args={"path": "a.py"}, step=1),
        AgentEvent("final", text="done", step=2),
    ]
    loop = _FakeLoop(events)

    async def go():
        app = _app(loop)
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "do the thing"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert loop.runs == 1
            assert app.rv_running is False
            assert app.rv_history and app.rv_history[-1]["content"] == "do the thing"
    _run(go())


def test_context_event_updates_the_gauge():
    events = [AgentEvent("context", context=ContextInfo(1500, 6000), step=1),
              AgentEvent("final", text="ok", step=2)]

    async def go():
        app = _app(_FakeLoop(events))
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "go"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            g = app.query_one(ContextGauge)
            assert g.used == 1500 and g.budget == 6000
    _run(go())


def test_token_events_feed_the_live_stream_line_then_clear():
    # W2 (ADR-0019): token deltas render on the StreamLine live; the completing
    # "final" event clears it (its text lands in the log instead).
    events = [
        AgentEvent("token", text="Analy", step=1),
        AgentEvent("token", text="zing…", step=1),
        AgentEvent("final", text="Analyzing…", step=2),
    ]

    async def go():
        app = _app(_FakeLoop(events))
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "go"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            # After the turn completes, the live line is cleared.
            assert app.query_one(StreamLine).streaming is False
    _run(go())


def test_stream_line_holds_partial_text_mid_turn():
    # Feeding tokens without a closing event leaves the partial text on the line.
    async def go():
        app = _app(_FakeLoop([]))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._apply_event(AgentEvent("token", text="hello "))
            app._apply_event(AgentEvent("token", text="world"))
            await pilot.pause()
            sl = app.query_one(StreamLine)
            assert sl.streaming is True
            # A non-token event closes the line.
            app._apply_event(AgentEvent("assistant", text="hello world"))
            await pilot.pause()
            assert sl.streaming is False
    _run(go())


def test_subagent_token_does_not_feed_root_stream_line():
    # A sub-agent's token (agent != "") must NOT hijack the root live line.
    async def go():
        app = _app(_FakeLoop([]))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._apply_event(AgentEvent("token", text="child text", agent="sub"))
            await pilot.pause()
            assert app.query_one(StreamLine).streaming is False
    _run(go())


def test_agent_start_increments_subagent_count():
    events = [AgentEvent("agent_start", text="sub goal", agent="fix-tests", step=1),
              AgentEvent("agent_end", text="Sub-agent completed", agent="fix-tests"),
              AgentEvent("final", text="ok", step=2)]

    async def go():
        app = _app(_FakeLoop(events))
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "go"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "fix-tests" in app.rv_agents_seen
            assert app.query_one(StatusBar).agents == 1
    _run(go())


# --- V4: slash palette -----------------------------------------------------

def test_typing_slash_opens_palette():
    from revenant_cli.tui.screens import PaletteScreen

    async def go():
        app = _app(_FakeLoop())
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "/"
            await pilot.pause()
            # on_input_changed fires the palette when value == "/"
            app.on_input_changed(type("E", (), {"value": "/"})())
            await pilot.pause()
            assert isinstance(app.screen, PaletteScreen)
    _run(go())


def test_help_command_lists_commands_in_log():
    loop = _FakeLoop()

    async def go():
        app = _app(loop)
        async with app.run_test() as pilot:
            await pilot.pause()
            before = len(app.query_one(ActivityLog).lines)
            app._handle_slash("/help")
            await pilot.pause()
            after = len(app.query_one(ActivityLog).lines)
            assert after > before  # /help wrote the command list
    _run(go())


def test_unknown_command_logged():
    async def go():
        app = _app(_FakeLoop())
        async with app.run_test() as pilot:
            await pilot.pause()
            app._handle_slash("/nope")
            await pilot.pause()
            # rendered into the log as an error line (no crash).
            assert app.query_one(ActivityLog).lines
    _run(go())


# --- V5: interrupt + approval ---------------------------------------------

def test_ctrl_c_interrupts_running_goal_without_quitting():
    class SpinLoop(_FakeLoop):
        def run(self, goal, history=None, should_stop=None):
            self.runs += 1
            for _ in range(2000):
                if should_stop and should_stop():
                    if self.on_event:
                        self.on_event(AgentEvent("interrupted", text="stopped by user"))
                    return type("R", (), {"messages": []})()
                time.sleep(0.002)
            return type("R", (), {"messages": []})()

    async def go():
        app = _app(SpinLoop())
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "spin"
            await pilot.press("enter")
            await pilot.pause()
            assert app.rv_running is True
            await pilot.press("ctrl+c")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.rv_running is False   # cancelled, but the app is still alive
            assert app.is_running
    _run(go())


def test_approval_modal_bridges_worker_thread():
    loop = _FakeLoop(
        emit=[AgentEvent("final", text="ok")],
        approvals=[("edit_file", {"path": "a.py", "old": "x=1", "new": "x=2"})],
    )

    async def go():
        app = _app(loop, mode="approval-gated")
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "edit it"
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause()
                if isinstance(app.screen, ApprovalScreen):
                    break
            assert isinstance(app.screen, ApprovalScreen)
            await pilot.press("y")   # approve
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert loop.approved == [True]
    _run(go())


def test_approval_denied_on_n():
    loop = _FakeLoop(
        emit=[AgentEvent("final", text="ok")],
        approvals=[("run_bash", {"command": "rm -rf /"})],
    )

    async def go():
        app = _app(loop, mode="approval-gated")
        async with app.run_test() as pilot:
            app.query_one("#prompt").value = "danger"
            await pilot.press("enter")
            for _ in range(40):
                await pilot.pause()
                if isinstance(app.screen, ApprovalScreen):
                    break
            assert isinstance(app.screen, ApprovalScreen)
            await pilot.press("n")   # deny
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert loop.approved == [False]
    _run(go())
