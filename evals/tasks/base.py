"""Task abstraction for the eval harness (H0.1, ADR-0015).

A `Task` is a tiny, self-contained fixture: a goal string the agent is given,
a `setup(workspace)` that materializes the starting repo state into a temp dir,
and a `score(workspace)` that deterministically decides pass/fail afterwards
(usually: a hidden test now passes, or a file now exists/matches).

Tasks are plain dataclasses with plain functions -- no framework, no network,
no model. `evals/run.py` drives them against a real agent; `tests/test_evals.py`
drives them directly (setup -> score) to prove the fixtures + scorers behave,
without ever touching a model.
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class ScoreResult:
    """The outcome of scoring one task run."""

    passed: bool
    detail: str = ""

    @staticmethod
    def ok(detail: str = "") -> "ScoreResult":
        return ScoreResult(True, detail)

    @staticmethod
    def fail(detail: str) -> "ScoreResult":
        return ScoreResult(False, detail)


# setup(workspace: Path) -> None: populate the fixture's starting files.
SetupFn = Callable[[Path], None]
# score(workspace: Path) -> ScoreResult: judge the workspace after the agent ran.
ScoreFn = Callable[[Path], ScoreResult]


@dataclass
class Task:
    """One eval task: a fixture + a goal + a deterministic scorer.

    `name` must be unique across the suite (used as the temp-dir prefix and the
    report key). `setup` and `score` never raise on the harness's behalf --
    `run.py` wraps `score` so a scorer bug counts as a task failure, not a
    crashed run (ADR-0015 "Failure & degradation").
    """

    name: str
    goal: str
    setup: SetupFn
    score: ScoreFn
    description: str = ""


def run_pytest(workspace: Path, target: str = ".", timeout: float = 60.0) -> subprocess.CompletedProcess:
    """Run pytest inside `workspace` against `target`, capturing output.

    Shared helper for "fix/add code so a hidden test passes" style scorers.
    Uses the current interpreter (`sys.executable -m pytest`) so it works
    offline with whatever pytest is already installed for this repo -- no new
    dependency, no network.
    """
    return subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q"],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
