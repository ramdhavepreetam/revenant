"""The eval task suite (H0.1, ADR-0015).

5 self-contained tasks targeting the ADR-0011 failure profile: fix a failing
test, add a function used by an existing caller, make a file exist with
required content, rename a symbol across all call sites, and handle an edge
case without regressing the normal path. Each module exports a single `TASK`;
`ALL_TASKS` collects them in a stable order for the runner and the tests.
"""
from __future__ import annotations

from evals.tasks.base import ScoreResult, Task
from evals.tasks import (
    fix_failing_test,
    add_function,
    make_file_exist,
    rename_across_callers,
    handle_edge_case,
)

ALL_TASKS: list[Task] = [
    fix_failing_test.TASK,
    add_function.TASK,
    make_file_exist.TASK,
    rename_across_callers.TASK,
    handle_edge_case.TASK,
]

_BY_NAME = {t.name: t for t in ALL_TASKS}
assert len(_BY_NAME) == len(ALL_TASKS), "duplicate task name in evals/tasks"


def get_task(name: str) -> Task:
    """Look up a task by name, raising KeyError with the known names on miss."""
    try:
        return _BY_NAME[name]
    except KeyError:
        known = ", ".join(sorted(_BY_NAME)) or "(none)"
        raise KeyError(f"no eval task named {name!r}. Known tasks: {known}") from None


__all__ = ["Task", "ScoreResult", "ALL_TASKS", "get_task"]
