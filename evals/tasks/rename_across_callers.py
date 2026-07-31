"""Task: rename a symbol across all its callers (ADR-0011 failure profile).

`greet` must become `greeting` everywhere -- its definition in `greetings.py`
AND every call site in `app.py`. A weak model without repo-wide context tends to
rename the definition but miss a caller (or vice versa). The scorer runs the
hidden test, which fails both if the rename is incomplete (old name still
referenced -> NameError) and if it was a no-op (old name still defined and
called -- test imports `greeting` directly, so that fails at import time too).
"""
from __future__ import annotations

from pathlib import Path

from evals.tasks.base import ScoreResult, Task, run_pytest

_GREETINGS = '''\
def greet(name):
    """Return a friendly greeting for name."""
    return f"Hello, {name}!"
'''

_APP = '''\
from greetings import greet


def welcome_message(name):
    return greet(name).upper()


def farewell_message(name):
    # second call site -- both must be updated for the rename to be complete
    return greet(name) + " Goodbye!"
'''

_TEST = '''\
from greetings import greeting
from app import welcome_message, farewell_message


def test_greeting_renamed():
    assert greeting("Ada") == "Hello, Ada!"


def test_callers_use_new_name():
    assert welcome_message("Ada") == "HELLO, ADA!"
    assert farewell_message("Ada") == "Hello, Ada! Goodbye!"
'''


def setup(workspace: Path) -> None:
    (workspace / "greetings.py").write_text(_GREETINGS)
    (workspace / "app.py").write_text(_APP)
    (workspace / "test_rename.py").write_text(_TEST)


def score(workspace: Path) -> ScoreResult:
    try:
        proc = run_pytest(workspace, "test_rename.py")
    except Exception as exc:  # noqa: BLE001
        return ScoreResult.fail(f"scorer error: {exc!r}")
    if proc.returncode == 0:
        return ScoreResult.ok("test_rename.py passed")
    return ScoreResult.fail(f"pytest exit {proc.returncode}:\n{proc.stdout}\n{proc.stderr}")


TASK = Task(
    name="rename_across_callers",
    goal=(
        "Rename the function `greet` to `greeting` in greetings.py, and update "
        "every place in app.py that calls it, so all call sites use the new name."
    ),
    setup=setup,
    score=score,
    description="Rename greet -> greeting in its definition and both call sites in app.py.",
)
