"""Task: add a function used by an existing caller (ADR-0011 failure profile #2).

`stats.py` imports `median` from `mathlib.py`, but `median` doesn't exist yet --
importing `stats` raises ImportError. A hidden test drives `stats.summarize`,
so the agent must add a correctly-behaving `median` (not just any function of
that name) for the test to pass.
"""
from __future__ import annotations

from pathlib import Path

from evals.tasks.base import ScoreResult, Task, run_pytest

_LIB = '''\
def mean(values):
    """Return the arithmetic mean of values."""
    return sum(values) / len(values)


# TODO: median(values) is used by stats.py but not implemented yet.
'''

_STATS = '''\
from mathlib import mean, median


def summarize(values):
    return {"mean": mean(values), "median": median(values)}
'''

_TEST = '''\
from stats import summarize


def test_summarize_odd():
    assert summarize([3, 1, 2]) == {"mean": 2.0, "median": 2}


def test_summarize_even():
    result = summarize([1, 2, 3, 4])
    assert result["mean"] == 2.5
    assert result["median"] == 2.5
'''


def setup(workspace: Path) -> None:
    (workspace / "mathlib.py").write_text(_LIB)
    (workspace / "stats.py").write_text(_STATS)
    (workspace / "test_stats.py").write_text(_TEST)


def score(workspace: Path) -> ScoreResult:
    try:
        proc = run_pytest(workspace, "test_stats.py")
    except Exception as exc:  # noqa: BLE001
        return ScoreResult.fail(f"scorer error: {exc!r}")
    if proc.returncode == 0:
        return ScoreResult.ok("test_stats.py passed")
    return ScoreResult.fail(f"pytest exit {proc.returncode}:\n{proc.stdout}\n{proc.stderr}")


TASK = Task(
    name="add_function",
    goal=(
        "stats.py fails to import because it needs a `median` function from "
        "mathlib.py that doesn't exist yet. Add `median(values)` to mathlib.py "
        "so that test_stats.py passes."
    ),
    setup=setup,
    score=score,
    description="Add median() to mathlib.py so stats.py's tests pass.",
)
