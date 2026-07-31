"""Task: fix a failing test (ADR-0011 failure profile #1).

The fixture ships a small `add` function with an off-by-one bug and a test that
pins the correct behavior. The goal asks the agent to make the test pass without
naming the bug -- it has to run the test, read the failure, and fix `add`.
"""
from __future__ import annotations

from pathlib import Path

from evals.tasks.base import ScoreResult, Task, run_pytest

_LIB = '''\
def add(a, b):
    """Return a + b."""
    return a + b + 1  # bug: off by one
'''

_TEST = '''\
from mathlib import add


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0
'''


def setup(workspace: Path) -> None:
    (workspace / "mathlib.py").write_text(_LIB)
    (workspace / "test_mathlib.py").write_text(_TEST)


def score(workspace: Path) -> ScoreResult:
    try:
        proc = run_pytest(workspace, "test_mathlib.py")
    except Exception as exc:  # noqa: BLE001 - a scorer error is a fail, not a crash
        return ScoreResult.fail(f"scorer error: {exc!r}")
    if proc.returncode == 0:
        return ScoreResult.ok("test_mathlib.py passed")
    return ScoreResult.fail(f"pytest exit {proc.returncode}:\n{proc.stdout}\n{proc.stderr}")


TASK = Task(
    name="fix_failing_test",
    goal=(
        "The test in test_mathlib.py is failing. Find out why and fix the code "
        "in mathlib.py so the test passes. Do not change the test."
    ),
    setup=setup,
    score=score,
    description="Fix an off-by-one bug in add() so test_mathlib.py passes.",
)
