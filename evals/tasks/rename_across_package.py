"""Task: rename a function across a whole package (W0, ADR-0019).

Harder than `rename_across_callers` (2 files): `compute_total` must become
`calculate_total` in its definition (`billing/core.py`) AND at every call site
spread across THREE other modules (`billing/api.py`, `billing/report.py`,
`cli.py`). This is the project-wide-rename profile the graph-driven refactor
slices (W4) are scored on -- a weak model without repo-wide context reliably
misses at least one of the four files. The hidden test imports the NEW name and
exercises every caller, so a partial rename fails (old name -> NameError) and a
no-op fails (test imports `calculate_total` directly).
"""
from __future__ import annotations

from pathlib import Path

from evals.tasks.base import ScoreResult, Task, run_pytest

_CORE = '''\
def compute_total(items):
    """Sum the price of every item."""
    return sum(i["price"] for i in items)
'''

_API = '''\
from billing.core import compute_total


def invoice_total(items):
    return compute_total(items)
'''

_REPORT = '''\
from billing.core import compute_total


def summary(items):
    return f"Total: {compute_total(items)}"
'''

_CLI = '''\
from billing.core import compute_total


def main(items):
    print(compute_total(items))
    return compute_total(items)
'''

_TEST = '''\
from billing.core import calculate_total
from billing.api import invoice_total
from billing.report import summary
from cli import main

ITEMS = [{"price": 2}, {"price": 3}]


def test_definition_renamed():
    assert calculate_total(ITEMS) == 5


def test_all_callers_updated():
    assert invoice_total(ITEMS) == 5
    assert summary(ITEMS) == "Total: 5"
    assert main(ITEMS) == 5
'''


def setup(workspace: Path) -> None:
    pkg = workspace / "billing"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text(_CORE)
    (pkg / "api.py").write_text(_API)
    (pkg / "report.py").write_text(_REPORT)
    (workspace / "cli.py").write_text(_CLI)
    (workspace / "test_rename_package.py").write_text(_TEST)


def score(workspace: Path) -> ScoreResult:
    try:
        proc = run_pytest(workspace, "test_rename_package.py")
    except Exception as exc:  # noqa: BLE001
        return ScoreResult.fail(f"scorer error: {exc!r}")
    if proc.returncode == 0:
        return ScoreResult.ok("calculate_total renamed across the whole package")
    return ScoreResult.fail(
        "rename incomplete or absent:\n" + (proc.stdout or proc.stderr)[-1500:]
    )


TASK = Task(
    name="rename_across_package",
    goal=(
        "Rename the function `compute_total` to `calculate_total` everywhere it "
        "appears in this project -- its definition in billing/core.py and every "
        "place that imports or calls it (billing/api.py, billing/report.py, and "
        "cli.py). Do not change any behavior; only the name changes."
    ),
    setup=setup,
    score=score,
    description="Project-wide function rename across 4 files (W0/W4 profile).",
)
