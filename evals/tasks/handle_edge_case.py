"""Task: handle an edge case without regressing existing behavior.

`safe_divide` works for the normal case but doesn't guard against division by
zero -- it should return None instead of raising. The hidden test suite checks
BOTH the new edge case AND the pre-existing normal-case behavior, so a fix that
special-cases zero by breaking the normal path (e.g. always returning None)
still fails.
"""
from __future__ import annotations

from pathlib import Path

from evals.tasks.base import ScoreResult, Task, run_pytest

_LIB = '''\
def safe_divide(a, b):
    """Return a / b, or None if b is zero."""
    return a / b  # missing: guard against b == 0
'''

_TEST = '''\
from calc import safe_divide


def test_normal_division():
    assert safe_divide(10, 2) == 5


def test_division_by_zero_returns_none():
    assert safe_divide(10, 0) is None


def test_negative_division():
    assert safe_divide(-9, 3) == -3
'''


def setup(workspace: Path) -> None:
    (workspace / "calc.py").write_text(_LIB)
    (workspace / "test_calc.py").write_text(_TEST)


def score(workspace: Path) -> ScoreResult:
    try:
        proc = run_pytest(workspace, "test_calc.py")
    except Exception as exc:  # noqa: BLE001
        return ScoreResult.fail(f"scorer error: {exc!r}")
    if proc.returncode == 0:
        return ScoreResult.ok("test_calc.py passed")
    return ScoreResult.fail(f"pytest exit {proc.returncode}:\n{proc.stdout}\n{proc.stderr}")


TASK = Task(
    name="handle_edge_case",
    goal=(
        "safe_divide in calc.py raises ZeroDivisionError when dividing by zero. "
        "Fix it to return None in that case, without changing its behavior for "
        "normal division."
    ),
    setup=setup,
    score=score,
    description="Guard safe_divide() against division by zero while keeping normal division correct.",
)
