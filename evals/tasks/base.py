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


@dataclass
class RunMetrics:
    """Per-run cost/quality metrics (W0, ADR-0019) -- beyond binary pass/fail.

    The real runner reports these from a finished `AgentLoop`; the fake runner
    in tests supplies them directly. All fields default to 0/unset so a runner
    that reports nothing (or an older saved report) still round-trips: a
    `TaskOutcome` with no metrics behaves exactly as it did before W0.

    - `steps`      : agent loop steps taken (fewer = cheaper for the same result).
    - `tokens`     : total prompt+completion tokens spent on the task.
    - `edits`      : edits the agent applied (write_file / edit_file / apply_edits).
    - `edits_kept` : of those, how many survived to the final workspace (not later
                     overwritten or reverted). `edit_precision` is the ratio --
                     "did the agent thrash, or land its edits" -- the signal the
                     graph-driven refactor slices (W4) are scored on.
    """

    steps: int = 0
    tokens: int = 0
    edits: int = 0
    edits_kept: int = 0

    @property
    def edit_precision(self) -> float:
        """Fraction of applied edits that survived to the final workspace.

        Defined as 1.0 when the agent made no edits (nothing to get wrong),
        so a zero-edit task never drags the average down.
        """
        return self.edits_kept / self.edits if self.edits else 1.0

    def to_dict(self) -> dict:
        d = {"steps": self.steps, "tokens": self.tokens,
             "edits": self.edits, "edits_kept": self.edits_kept}
        d["edit_precision"] = self.edit_precision
        return d

    @staticmethod
    def from_dict(d: "dict | None") -> "RunMetrics | None":
        if not d:
            return None
        return RunMetrics(
            steps=int(d.get("steps", 0)), tokens=int(d.get("tokens", 0)),
            edits=int(d.get("edits", 0)), edits_kept=int(d.get("edits_kept", 0)),
        )


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
