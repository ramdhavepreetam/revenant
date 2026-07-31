"""Tests for the iterate-until-done loop driver (F13.1/F13.2, ADR-0006).

Drives loop_until with a fake run_fn (no model): asserts it stops on the
predicate, threads history forward, nudges on not-yet, and honors every budget
axis. Also covers the built-in predicates. No network, no real agent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nerva_agent.loop_driver import (
    loop_until, Budget, PredicateResult,
    model_final_predicate, command_predicate, file_exists_predicate,
)


class _Result:
    def __init__(self, messages, stopped_reason="final", answer=""):
        self.messages = messages
        self.stopped_reason = stopped_reason
        self.answer = answer


class _FakeRunner:
    """Records each (goal, history); returns a growing transcript."""

    def __init__(self, stopped_reason="final"):
        self.calls = []
        self._stopped = stopped_reason

    def __call__(self, goal, history=None):
        self.calls.append((goal, history))
        msgs = list(history or [])
        msgs += [{"role": "user", "content": goal},
                 {"role": "assistant", "content": f"did: {goal}"}]
        return _Result(msgs, self._stopped)


# --- stopping & threading ----------------------------------------------------

def test_stops_immediately_when_predicate_passes():
    runner = _FakeRunner()
    done = lambda r: PredicateResult(True, "ok")
    outcome = loop_until("goal", runner, done, Budget(max_iterations=5))
    assert outcome.stopped_reason == "done"
    assert outcome.iterations == 1
    assert len(runner.calls) == 1


def test_threads_history_and_nudges_until_done():
    runner = _FakeRunner()
    # Pass only on the 3rd iteration.
    calls = {"n": 0}
    def pred(r):
        calls["n"] += 1
        return PredicateResult(calls["n"] >= 3, "not yet" if calls["n"] < 3 else "done")
    outcome = loop_until("build X", runner, pred, Budget(max_iterations=5))
    assert outcome.stopped_reason == "done"
    assert outcome.iterations == 3
    # First call used the real goal; later calls are nudges carrying history.
    assert runner.calls[0][0] == "build X"
    assert runner.calls[0][1] is None
    assert "not done yet" in runner.calls[1][0].lower()
    assert runner.calls[1][1] is not None  # history threaded forward


# --- budgets -----------------------------------------------------------------

def test_max_iterations_bound():
    runner = _FakeRunner()
    never = lambda r: PredicateResult(False, "nope")
    outcome = loop_until("g", runner, never, Budget(max_iterations=4))
    assert outcome.stopped_reason == "max_iterations"
    assert outcome.iterations == 4
    assert len(runner.calls) == 4


def test_zero_iterations_still_runs_once():
    runner = _FakeRunner()
    never = lambda r: PredicateResult(False, "nope")
    outcome = loop_until("g", runner, never, Budget(max_iterations=0))
    assert outcome.iterations == 1  # never unbounded; min one, then stop


def test_max_wall_bound(monkeypatch):
    import nerva_agent.loop_driver as ld
    # Fake a clock that jumps past the wall budget after the first iteration.
    ticks = iter([100.0, 100.0, 200.0, 200.0, 300.0])
    monkeypatch.setattr(ld.time, "monotonic", lambda: next(ticks))
    runner = _FakeRunner()
    never = lambda r: PredicateResult(False, "nope")
    outcome = loop_until("g", runner, never, Budget(max_iterations=10,
                                                    max_wall_seconds=50))
    assert outcome.stopped_reason == "max_wall"


def test_max_tokens_bound():
    runner = _FakeRunner()
    never = lambda r: PredicateResult(False, "nope")
    # Tiny token budget -> stops after the first iteration's transcript.
    outcome = loop_until("a longer goal here", runner, never,
                         Budget(max_iterations=10, max_tokens=1))
    assert outcome.stopped_reason == "max_tokens"


# --- on_iteration callback ---------------------------------------------------

def test_on_iteration_fires_each_round():
    runner = _FakeRunner()
    seen = []
    never = lambda r: PredicateResult(False, "keep going")
    loop_until("g", runner, never, Budget(max_iterations=3),
               on_iteration=lambda info: seen.append(info.index))
    assert seen == [1, 2, 3]


# --- built-in predicates -----------------------------------------------------

def test_model_final_predicate():
    pred = model_final_predicate()
    assert pred(_Result([], "final")).done is True
    assert pred(_Result([], "max_steps")).done is False


def test_command_predicate_passes_on_exit_zero(tmp_path):
    pred = command_predicate("true")
    assert pred(_Result([])).done is True


def test_command_predicate_fails_on_nonzero():
    pred = command_predicate("false")
    res = pred(_Result([]))
    assert res.done is False
    assert "exited" in res.reason


def test_command_predicate_handles_bad_command():
    pred = command_predicate("this-binary-does-not-exist-xyz --nope")
    # shell reports non-zero (127); treated as not-done, never raises.
    assert pred(_Result([])).done is False


def test_file_exists_predicate(tmp_path):
    target = tmp_path / "out.txt"
    pred = file_exists_predicate(target)
    assert pred(_Result([])).done is False
    target.write_text("x")
    assert pred(_Result([])).done is True
