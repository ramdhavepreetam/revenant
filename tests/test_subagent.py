"""Tests for the sub-agent spawn tool (F15.1, ADR-0009).

Drives build_spawn_tool with a fake loop_factory (no model): asserts it runs a
nested loop, returns a summary (not the transcript), passes the scoped tool list
and incremented depth to the factory, refuses at the depth cap, and turns
failures into observations rather than crashes.
"""
from __future__ import annotations

import pytest

from nerva_agent.subagent import (
    build_spawn_tool, summarize_result, DEFAULT_MAX_DEPTH,
)


class _FakeResult:
    def __init__(self, answer="done", steps=3, stopped_reason="final"):
        self.answer = answer
        self.steps = steps
        self.stopped_reason = stopped_reason
        self.messages = [{"role": "assistant", "content": answer}]


class _FakeLoop:
    def __init__(self, result):
        self._result = result
        self.ran_with = None

    def run(self, goal, history=None):
        self.ran_with = goal
        return self._result


def _factory(record, result=None):
    """A loop_factory that records (goal, tools, depth[, role]) and returns a loop."""
    def make(goal, tools, depth, role=""):
        record.append((goal, tools, depth, role))
        return _FakeLoop(result or _FakeResult())
    return make


# --- happy path --------------------------------------------------------------

def test_spawn_runs_nested_loop_and_summarizes():
    calls = []
    tool = build_spawn_tool(_factory(calls))
    out = tool.invoke({"goal": "refactor module X"})
    assert "Sub-agent completed" in out
    assert "done" in out
    # factory was called with the goal, no tool scope, depth+1, and no role.
    assert calls == [("refactor module X", None, 1, "")]


def test_spawn_passes_scoped_tools():
    calls = []
    tool = build_spawn_tool(_factory(calls))
    tool.invoke({"goal": "g", "tools": "read_file, run_bash"})
    assert calls[0][1] == ["read_file", "run_bash"]


# --- W5 (ADR-0021): role-routed sub-agents -----------------------------------

def test_spawn_threads_role_to_factory():
    calls = []
    tool = build_spawn_tool(_factory(calls))
    tool.invoke({"goal": "plan the work", "role": "summary"})
    assert calls[0][3] == "summary"   # role reached the factory


def test_spawn_default_role_is_empty():
    calls = []
    tool = build_spawn_tool(_factory(calls))
    tool.invoke({"goal": "g"})
    assert calls[0][3] == ""          # no role -> parent config (unchanged)


def test_spawn_role_whitespace_trimmed():
    calls = []
    tool = build_spawn_tool(_factory(calls))
    tool.invoke({"goal": "g", "role": "  code  "})
    assert calls[0][3] == "code"


def test_spawn_tool_declares_role_param():
    tool = build_spawn_tool(_factory([]))
    params = {p.name for p in tool.params}
    assert "role" in params
    assert not next(p for p in tool.params if p.name == "role").required


def test_summary_is_bounded():
    long_answer = "x" * 5000
    tool = build_spawn_tool(_factory([], _FakeResult(answer=long_answer)))
    out = tool.invoke({"goal": "g"})
    assert len(out) < 1400
    assert out.rstrip().endswith("[…]")


def test_summary_reflects_stopped_reason():
    r = _FakeResult(answer="", steps=15, stopped_reason="max_steps")
    assert "hit its step budget" in summarize_result(r)


# --- guardrails --------------------------------------------------------------

def test_depth_cap_refuses_recursion():
    calls = []
    # Already at the max depth -> must refuse and NOT call the factory.
    tool = build_spawn_tool(_factory(calls), depth=DEFAULT_MAX_DEPTH)
    out = tool.invoke({"goal": "g"})
    assert "depth limit" in out
    assert calls == []


def test_empty_goal_refused():
    calls = []
    tool = build_spawn_tool(_factory(calls))
    assert "non-empty goal" in tool.invoke({"goal": "  "})
    assert calls == []


def test_tool_is_mutating_and_approval_gated():
    tool = build_spawn_tool(_factory([]))
    assert tool.mutating is True
    assert tool.requires_approval is True


# --- failure degradation -----------------------------------------------------

def test_factory_error_becomes_observation():
    def bad_factory(goal, tools, depth, role=""):
        raise RuntimeError("boom")
    tool = build_spawn_tool(bad_factory)
    out = tool.invoke({"goal": "g"})
    assert out.startswith("ERROR:")
    assert "could not build" in out


def test_run_error_becomes_observation():
    class Boom:
        def run(self, goal, history=None):
            raise RuntimeError("kaboom")
    tool = build_spawn_tool(lambda g, t, d, r="": Boom())
    out = tool.invoke({"goal": "g"})
    assert out.startswith("ERROR:")
    assert "run failed" in out


# --- V2 (ADR-0017): sub-agent visibility -------------------------------------

def _emitting_loop_factory(child_events):
    """A factory whose loop emits `child_events` through its own on_event."""
    class _Emitter:
        on_event = None  # the relay is assigned here by build_spawn_tool

        def run(self, goal, history=None):
            for ev in child_events:
                if self.on_event is not None:
                    self.on_event(ev)
            return _FakeResult(answer="child done")

    return lambda goal, tools, depth, role="": _Emitter()


def test_child_events_relayed_stamped_with_label():
    from nerva_agent.agent_loop import AgentEvent

    seen = []
    child = [AgentEvent("action", tool="read_file", args={"path": "a.py"}, step=1)]
    tool = build_spawn_tool(
        _emitting_loop_factory(child), parent_sink=seen.append)
    tool.invoke({"goal": "fix the failing test"})

    kinds = [e.kind for e in seen]
    # lifecycle brackets the child's own events.
    assert kinds[0] == "agent_start" and kinds[-1] == "agent_end"
    # every relayed event carries the goal-derived label.
    label = seen[0].agent
    assert label and all(e.agent == label for e in seen)
    # the child's action was relayed through, stamped.
    assert any(e.kind == "action" and e.tool == "read_file" for e in seen)


def test_label_derived_from_goal():
    seen = []
    tool = build_spawn_tool(
        _emitting_loop_factory([]), parent_sink=seen.append)
    tool.invoke({"goal": "Refactor The Auth Module"})
    assert seen[0].agent == "refactor-the-auth-module"


def test_no_parent_sink_runs_silently():
    # Without a parent_sink, behavior is exactly as before (no crash, summary out).
    calls = []
    tool = build_spawn_tool(_factory(calls))  # no parent_sink
    out = tool.invoke({"goal": "g"})
    assert "Sub-agent completed" in out


def test_grandchild_keeps_own_label():
    # A relayed event that already has an `agent` set is NOT re-tagged by a parent.
    from nerva_agent.agent_loop import AgentEvent

    seen = []
    grandchild = [AgentEvent("action", tool="grep", agent="deep-task", step=1)]
    tool = build_spawn_tool(
        _emitting_loop_factory(grandchild), parent_sink=seen.append)
    tool.invoke({"goal": "outer goal"})
    relayed = [e for e in seen if e.kind == "action"][0]
    assert relayed.agent == "deep-task"  # preserved, not overwritten by "outer-goal"


def test_run_error_emits_agent_end():
    class Boom:
        on_event = None
        def run(self, goal, history=None):
            raise RuntimeError("kaboom")
    seen = []
    tool = build_spawn_tool(lambda g, t, d, r="": Boom(), parent_sink=seen.append)
    out = tool.invoke({"goal": "g"})
    assert out.startswith("ERROR:")
    assert seen[-1].kind == "agent_end" and "errored" in seen[-1].text
