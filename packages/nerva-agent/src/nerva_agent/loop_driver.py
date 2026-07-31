"""Iterate-until-done driver for autonomous runs (F13, ADR-0006).

The agent loop runs one goal to completion. This driver runs it *repeatedly*,
unattended, until a success predicate passes or a budget is exhausted — the
substrate for `revenant loop`. It is a thin layer over `AgentLoop.run(goal,
history=...)`: it re-feeds the transcript forward and nudges the model with why
it isn't done yet, so each iteration builds on the last.

Autonomy is always bounded (ADR-0006): there is no "run forever" mode. Every
call takes a `Budget` (max iterations, wall-clock, tokens) and a `Predicate`
that decides success. A predicate that errors counts as "not satisfied" so a
flaky check never masquerades as done.

Design notes:
- `run_fn(goal, history) -> RunResult`-like: injected so the driver needs no
  ChatConfig/registry and is trivially testable. In the CLI it is `loop.run`.
- `on_iteration(info)`: optional callback fired once per iteration (used by the
  CLI to print progress and by the run journal to persist each step).
- Predicates are plain callables; built-ins cover the common cases (a shell
  command exiting 0, a file existing, or the model simply declaring completion).
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol


class _RunLike(Protocol):
    stopped_reason: str
    messages: list[dict]
    answer: str


# run_fn(goal, history) -> a result with .messages / .stopped_reason / .answer
RunFn = Callable[[str, "list[dict] | None"], _RunLike]


@dataclass
class Budget:
    """Hard limits on an autonomous run. 0/None means "no limit for this axis"
    but at least one bound is always effectively present (max_iterations
    defaults to a finite value), so a loop can never run unbounded."""

    max_iterations: int = 10
    max_wall_seconds: float = 0.0     # 0 => unlimited wall clock
    max_tokens: int = 0               # 0 => untracked (driver-level estimate)


@dataclass
class PredicateResult:
    """Outcome of checking whether the goal is met."""

    done: bool
    reason: str = ""


# A predicate inspects the latest run result and decides success.
Predicate = Callable[[_RunLike], PredicateResult]


@dataclass
class IterationInfo:
    """What happened in one iteration (passed to on_iteration)."""

    index: int                 # 1-based
    goal: str
    result: Any                # the run result
    predicate: PredicateResult
    messages: list[dict]


@dataclass
class LoopOutcome:
    """The result of a whole autonomous run."""

    stopped_reason: str        # "done" | "max_iterations" | "max_wall" | "error"
    iterations: int
    messages: list[dict] = field(default_factory=list)
    last_reason: str = ""


# --- built-in predicates -----------------------------------------------------

def model_final_predicate() -> Predicate:
    """Success when the agent itself declares completion (weakest bound)."""
    def check(result: _RunLike) -> PredicateResult:
        if getattr(result, "stopped_reason", "") == "final":
            return PredicateResult(True, "agent reported completion")
        return PredicateResult(False, "agent has not reported completion")
    return check


def command_predicate(command: str, cwd: Path | None = None,
                      timeout: float = 120.0) -> Predicate:
    """Success when `command` exits 0 (e.g. a test command). Any error/non-zero
    exit / timeout counts as not-yet, with the reason captured for the nudge."""
    def check(_result: _RunLike) -> PredicateResult:
        try:
            proc = subprocess.run(
                command, shell=True, cwd=str(cwd) if cwd else None,
                capture_output=True, text=True, timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return PredicateResult(False, f"check command failed to run: {exc}")
        if proc.returncode == 0:
            return PredicateResult(True, f"`{command}` passed")
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        return PredicateResult(False, f"`{command}` exited {proc.returncode}: "
                                      + " / ".join(tail))
    return check


def file_exists_predicate(path: Path) -> Predicate:
    """Success when `path` exists."""
    path = Path(path)
    def check(_result: _RunLike) -> PredicateResult:
        if path.exists():
            return PredicateResult(True, f"{path} exists")
        return PredicateResult(False, f"{path} does not exist yet")
    return check


# --- the driver --------------------------------------------------------------

_NUDGE = (
    "You are not done yet: {reason}. Continue working toward the goal. "
    "Make concrete changes; do not just describe what you would do."
)


def loop_until(
    goal: str,
    run_fn: RunFn,
    predicate: Predicate,
    budget: Budget,
    *,
    on_iteration: "Callable[[IterationInfo], None] | None" = None,
) -> LoopOutcome:
    """Run `run_fn` repeatedly until `predicate` passes or `budget` is spent.

    History is threaded forward between iterations; when the predicate is not yet
    satisfied, the next goal is a nudge that includes the predicate's reason so
    the model knows what still needs doing.
    """
    start = time.monotonic()
    history: list[dict] | None = None
    current_goal = goal
    last_reason = ""
    approx_tokens = 0

    max_iters = budget.max_iterations if budget.max_iterations > 0 else 1
    for i in range(1, max_iters + 1):
        result = run_fn(current_goal, history)
        history = list(getattr(result, "messages", []) or [])
        approx_tokens += _estimate_tokens(history)

        pred = predicate(result)
        last_reason = pred.reason
        if on_iteration is not None:
            on_iteration(IterationInfo(
                index=i, goal=current_goal, result=result,
                predicate=pred, messages=history,
            ))

        if pred.done:
            return LoopOutcome("done", i, history, pred.reason)

        # Budget checks AFTER recording the iteration, so the journal is complete.
        if budget.max_wall_seconds and (time.monotonic() - start) >= budget.max_wall_seconds:
            return LoopOutcome("max_wall", i, history, last_reason)
        if budget.max_tokens and approx_tokens >= budget.max_tokens:
            return LoopOutcome("max_tokens", i, history, last_reason)

        current_goal = _NUDGE.format(reason=pred.reason or "the goal is not met")

    return LoopOutcome("max_iterations", max_iters, history or [], last_reason)


def _estimate_tokens(messages: list[dict]) -> int:
    """Very rough per-iteration token estimate (chars/4) for the token budget.
    Deliberately dependency-free; exactness isn't needed for a safety bound."""
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    return chars // 4
