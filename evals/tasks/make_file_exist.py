"""Task: make a file exist with expected content (ADR-0011 failure profile #3).

A README references a CHANGELOG.md that doesn't exist yet. The goal asks the
agent to create it with a specific first entry. The scorer checks existence AND
that the required heading + entry text are present -- so a stub empty file
doesn't count as a pass.
"""
from __future__ import annotations

from pathlib import Path

from evals.tasks.base import ScoreResult, Task

_README = '''\
# demo project

See CHANGELOG.md for release notes.
'''

_REQUIRED_HEADING = "# Changelog"
_REQUIRED_ENTRY = "## 0.1.0"


def setup(workspace: Path) -> None:
    (workspace / "README.md").write_text(_README)


def score(workspace: Path) -> ScoreResult:
    target = workspace / "CHANGELOG.md"
    if not target.exists():
        return ScoreResult.fail("CHANGELOG.md does not exist")
    try:
        text = target.read_text()
    except Exception as exc:  # noqa: BLE001
        return ScoreResult.fail(f"scorer error reading CHANGELOG.md: {exc!r}")
    if _REQUIRED_HEADING not in text:
        return ScoreResult.fail(f"CHANGELOG.md missing heading {_REQUIRED_HEADING!r}")
    if _REQUIRED_ENTRY not in text:
        return ScoreResult.fail(f"CHANGELOG.md missing entry {_REQUIRED_ENTRY!r}")
    return ScoreResult.ok("CHANGELOG.md exists with required heading + entry")


TASK = Task(
    name="make_file_exist",
    goal=(
        "README.md refers to a CHANGELOG.md that doesn't exist yet. Create "
        "CHANGELOG.md with a '# Changelog' heading and a '## 0.1.0' section "
        "describing the initial release."
    ),
    setup=setup,
    score=score,
    description="Create CHANGELOG.md with the heading and version entry the README promises.",
)
