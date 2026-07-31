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
    """A loop_factory that records (goal, tools, depth) and returns a fake loop."""
    def make(goal, tools, depth):
        record.append((goal, tools, depth))
        return _FakeLoop(result or _FakeResult())
    return make


# --- happy path --------------------------------------------------------------

def test_spawn_runs_nested_loop_and_summarizes():
    calls = []
    tool = build_spawn_tool(_factory(calls))
    out = tool.invoke({"goal": "refactor module X"})
    assert "Sub-agent completed" in out
    assert "done" in out
    # factory was called with the goal, no tool scope, and depth+1.
    assert calls == [("refactor module X", None, 1)]


def test_spawn_passes_scoped_tools():
    calls = []
    tool = build_spawn_tool(_factory(calls))
    tool.invoke({"goal": "g", "tools": "read_file, run_bash"})
    assert calls[0][1] == ["read_file", "run_bash"]


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
    def bad_factory(goal, tools, depth):
        raise RuntimeError("boom")
    tool = build_spawn_tool(bad_factory)
    out = tool.invoke({"goal": "g"})
    assert out.startswith("ERROR:")
    assert "could not build" in out


def test_run_error_becomes_observation():
    class Boom:
        def run(self, goal, history=None):
            raise RuntimeError("kaboom")
    tool = build_spawn_tool(lambda g, t, d: Boom())
    out = tool.invoke({"goal": "g"})
    assert out.startswith("ERROR:")
    assert "run failed" in out
