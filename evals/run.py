#!/usr/bin/env python3
"""Eval harness runner (H0.2, ADR-0015).

For each task in `evals/tasks`: materialize its fixture into a fresh temp dir,
run Revenant against a configured local model to pursue the task's goal, then
run the task's scorer against the resulting workspace -> pass/fail. Emits a
report: per-task result + overall pass-rate + wall-time. `--compare` diffs two
saved reports (e.g. verify on vs. off) so a harness change's lift is one number.

The actual agent invocation is pluggable: `AgentRunner` is a `Protocol` with one
method, `run(workspace, goal) -> None` (it mutates files in `workspace`, mirroring
how the real CLI works). `RevenantAgentRunner` is the real implementation, built
lazily so importing this module never requires a model or network. Tests inject
a fake runner instead -- see `tests/test_evals.py` -- so the harness's own logic
(fixture setup/teardown, scoring, aggregation, --compare) is fully testable
offline, per the ADR's hard constraint.

Model-driven runs are opt-in / manual, NOT part of `pytest`:

    python3 evals/run.py --model qwen2.5:14b
    python3 evals/run.py --model qwen2.5:14b --save baseline.json
    python3 evals/run.py --model qwen2.5:14b --save after.json
    python3 evals/run.py --compare baseline.json after.json

Degrades gracefully (ADR-0015 "Failure & degradation"):
- no local model reachable -> the runner SKIPS model-driven runs with a clear
  message and a non-crashing (empty) report, rather than hanging or erroring.
- a task scorer raising -> that task counts as a fail with the error captured;
  it never aborts the rest of the run.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

# Make the sibling `packages/*/src` trees importable when this file is run
# directly (`python3 evals/run.py`), same convention the CLI entry points use.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _pkg_src in sorted((_REPO_ROOT / "packages").glob("*/src")):
    sys.path.insert(0, str(_pkg_src))
sys.path.insert(0, str(_REPO_ROOT))

from evals.tasks import ALL_TASKS, Task  # noqa: E402


# --- pluggable agent runner ---------------------------------------------------

class AgentRunner(Protocol):
    """Drives an agent over a goal inside a workspace. Mutates files in place."""

    def run(self, workspace: Path, goal: str) -> None:
        ...


class RevenantAgentRunner:
    """The real agent runner: builds a Revenant AgentLoop and runs the goal.

    Imports the agent stack lazily (inside `run`, not at module load) so this
    module stays importable -- and the harness's own tests stay collectible --
    even in an environment where the agent packages have import-time side
    effects we'd rather not pay for the fake-runner test path.
    """

    def __init__(self, model: str, base_url: str = "http://localhost:11434",
                 max_steps: int = 0, verify: bool = False) -> None:
        self.model = model
        self.base_url = base_url
        self.max_steps = max_steps
        self.verify = verify

    def run(self, workspace: Path, goal: str) -> None:
        import argparse as _argparse
        from revenant_cli.cli import _build_agent

        args = _argparse.Namespace(
            workspace=str(workspace), base_url=self.base_url, model=self.model,
            max_steps=self.max_steps, max_context_tokens=0,
            no_native_tools=False, read_only=False, yolo=True, no_color=True,
            no_graph=True, skill=None,
        )
        built = _build_agent(args)
        if built is None:
            raise RuntimeError(f"could not build agent for workspace {workspace}")
        _ws, _config, _rec, loop, _color = built
        try:
            loop.run(goal)
        finally:
            for client in getattr(loop, "_mcp_clients", ()) or ():
                try:
                    client.close()
                except Exception:  # noqa: BLE001 - cleanup is best-effort
                    pass


def model_server_reachable(base_url: str, timeout: float = 2.0) -> bool:
    """Best-effort check that a local model server is up. Never raises."""
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/api/tags", timeout=timeout):
            return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


# --- report data model ---------------------------------------------------------

@dataclass
class TaskOutcome:
    name: str
    passed: bool
    detail: str = ""
    seconds: float = 0.0
    skipped: bool = False


@dataclass
class Report:
    model: str = ""
    base_url: str = ""
    harness_flags: dict = field(default_factory=dict)
    outcomes: list[TaskOutcome] = field(default_factory=list)
    wall_seconds: float = 0.0
    skipped: bool = False
    skip_reason: str = ""

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def passed_count(self) -> int:
        return sum(1 for o in self.outcomes if o.passed)

    @property
    def pass_rate(self) -> float:
        if not self.outcomes:
            return 0.0
        return self.passed_count / self.total

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pass_rate"] = self.pass_rate
        d["passed_count"] = self.passed_count
        d["total"] = self.total
        return d

    @staticmethod
    def from_dict(d: dict) -> "Report":
        outcomes = [TaskOutcome(**o) for o in d.get("outcomes", [])]
        return Report(
            model=d.get("model", ""),
            base_url=d.get("base_url", ""),
            harness_flags=d.get("harness_flags", {}),
            outcomes=outcomes,
            wall_seconds=d.get("wall_seconds", 0.0),
            skipped=d.get("skipped", False),
            skip_reason=d.get("skip_reason", ""),
        )


# --- runner core ---------------------------------------------------------------

def run_task(task: Task, agent_runner: AgentRunner, *, tmp_root: "Path | None" = None) -> TaskOutcome:
    """Set up `task`'s fixture in a fresh temp dir, drive the agent, then score.

    A scorer (or agent) exception never propagates -- it's captured as a failed
    outcome so one broken task can't crash the whole run (ADR-0015).
    """
    started = time.monotonic()
    workspace = Path(tempfile.mkdtemp(prefix=f"revenant-eval-{task.name}-", dir=tmp_root))
    try:
        try:
            task.setup(workspace)
        except Exception as exc:  # noqa: BLE001
            return TaskOutcome(task.name, False, f"setup error: {exc!r}", time.monotonic() - started)

        try:
            agent_runner.run(workspace, task.goal)
        except Exception as exc:  # noqa: BLE001
            return TaskOutcome(task.name, False, f"agent error: {exc!r}", time.monotonic() - started)

        try:
            result = task.score(workspace)
        except Exception as exc:  # noqa: BLE001
            return TaskOutcome(task.name, False, f"scorer error: {exc!r}", time.monotonic() - started)

        return TaskOutcome(task.name, result.passed, result.detail, time.monotonic() - started)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def run_suite(
    tasks: "list[Task]",
    agent_runner: "AgentRunner | None",
    *,
    model: str = "",
    base_url: str = "",
    harness_flags: "dict | None" = None,
    tmp_root: "Path | None" = None,
) -> Report:
    """Run every task in `tasks` and aggregate into a Report.

    `agent_runner=None` means "no model available": the run is skipped cleanly
    (empty outcomes, `skipped=True`) rather than attempting a model call.
    """
    if agent_runner is None:
        return Report(
            model=model, base_url=base_url, harness_flags=harness_flags or {},
            skipped=True,
            skip_reason="no local model available -- model-driven eval run skipped",
        )

    started = time.monotonic()
    outcomes = [run_task(t, agent_runner, tmp_root=tmp_root) for t in tasks]
    return Report(
        model=model, base_url=base_url, harness_flags=harness_flags or {},
        outcomes=outcomes, wall_seconds=time.monotonic() - started,
    )


# --- reporting -------------------------------------------------------------

def format_report(report: Report) -> str:
    if report.skipped:
        return f"eval run skipped: {report.skip_reason}"
    lines = [f"model={report.model!r} base_url={report.base_url!r} flags={report.harness_flags}"]
    for o in report.outcomes:
        mark = "PASS" if o.passed else "FAIL"
        lines.append(f"  [{mark}] {o.name} ({o.seconds:.2f}s) {o.detail}".rstrip())
    lines.append(
        f"pass-rate: {report.passed_count}/{report.total} "
        f"({report.pass_rate * 100:.0f}%) in {report.wall_seconds:.2f}s"
    )
    return "\n".join(lines)


@dataclass
class CompareResult:
    """The delta between a baseline report and a candidate report."""

    baseline: Report
    candidate: Report
    delta_pass_rate: float
    per_task: "dict[str, tuple[bool, bool]]" = field(default_factory=dict)  # name -> (base_passed, cand_passed)

    @property
    def improved(self) -> "list[str]":
        return [n for n, (b, c) in self.per_task.items() if not b and c]

    @property
    def regressed(self) -> "list[str]":
        return [n for n, (b, c) in self.per_task.items() if b and not c]


def compare_reports(baseline: Report, candidate: Report) -> CompareResult:
    """Diff two reports task-by-task, and as an overall pass-rate delta."""
    base_by_name = {o.name: o.passed for o in baseline.outcomes}
    cand_by_name = {o.name: o.passed for o in candidate.outcomes}
    names = set(base_by_name) | set(cand_by_name)
    per_task = {n: (base_by_name.get(n, False), cand_by_name.get(n, False)) for n in sorted(names)}
    return CompareResult(
        baseline=baseline,
        candidate=candidate,
        delta_pass_rate=candidate.pass_rate - baseline.pass_rate,
        per_task=per_task,
    )


def format_compare(cmp: CompareResult) -> str:
    lines = [
        f"baseline pass-rate: {cmp.baseline.passed_count}/{cmp.baseline.total} "
        f"({cmp.baseline.pass_rate * 100:.0f}%)",
        f"candidate pass-rate: {cmp.candidate.passed_count}/{cmp.candidate.total} "
        f"({cmp.candidate.pass_rate * 100:.0f}%)",
        f"delta: {cmp.delta_pass_rate * 100:+.0f} pts",
    ]
    if cmp.improved:
        lines.append(f"improved: {', '.join(cmp.improved)}")
    if cmp.regressed:
        lines.append(f"regressed: {', '.join(cmp.regressed)}")
    return "\n".join(lines)


# --- CLI ---------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Revenant eval harness (H0, ADR-0015)")
    p.add_argument("--model", default="", help="Local model to evaluate (e.g. qwen2.5:14b)")
    p.add_argument("--base-url", default="http://localhost:11434", help="Local model server URL")
    p.add_argument("--max-steps", type=int, default=0, help="Per-task step cap (0 = harness default)")
    p.add_argument("--save", default="", help="Write the report as JSON to this path")
    p.add_argument("--compare", nargs=2, metavar=("BASELINE_JSON", "CANDIDATE_JSON"),
                    help="Diff two saved reports instead of running the suite")
    p.add_argument("--task", action="append", default=[], help="Run only this task (repeatable)")
    return p


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(argv)

    if args.compare:
        baseline_path, candidate_path = args.compare
        baseline = Report.from_dict(json.loads(Path(baseline_path).read_text()))
        candidate = Report.from_dict(json.loads(Path(candidate_path).read_text()))
        print(format_compare(compare_reports(baseline, candidate)))
        return 0

    tasks = [t for t in ALL_TASKS if not args.task or t.name in args.task]

    agent_runner = None
    if not args.model:
        print("no --model given -- skipping model-driven run (offline default).", file=sys.stderr)
    elif not model_server_reachable(args.base_url):
        print(f"no local model server reachable at {args.base_url} -- skipping.", file=sys.stderr)
    else:
        agent_runner = RevenantAgentRunner(args.model, args.base_url, args.max_steps)

    report = run_suite(
        tasks, agent_runner,
        model=args.model, base_url=args.base_url,
        harness_flags={"max_steps": args.max_steps},
    )
    print(format_report(report))

    if args.save:
        Path(args.save).write_text(json.dumps(report.to_dict(), indent=2))
        print(f"saved report to {args.save}")

    if report.skipped:
        return 0
    return 0 if report.pass_rate == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
