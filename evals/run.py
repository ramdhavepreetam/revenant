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
from evals.tasks.base import RunMetrics  # noqa: E402


# --- pluggable agent runner ---------------------------------------------------

class AgentRunner(Protocol):
    """Drives an agent over a goal inside a workspace. Mutates files in place.

    `run` may optionally return a `RunMetrics` (W0, ADR-0019) describing the
    cost/quality of the run (steps/tokens/edit-precision). Returning `None`
    (the historical contract) is still valid -- the task then carries no metrics.
    """

    def run(self, workspace: Path, goal: str) -> "RunMetrics | None":
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

    def run(self, workspace: Path, goal: str) -> "RunMetrics | None":
        import argparse as _argparse
        from revenant_cli.cli import _build_agent

        # When verify is on, enable the harness's correctness features for this
        # task: write a [verify] config into the workspace and turn the code graph
        # ON (so H2 context injection is active too). This is what makes the eval
        # an honest "harness on vs. off" comparison (ADR-0015 --compare).
        if self.verify:
            # Rely on the built-in per-file pycompile (on by default) for syntax,
            # and pytest for behavior. NO {paths}-scoped py_compile command — that
            # runs with an empty arg after a run_bash step and would false-fail.
            (workspace / ".revenant.toml").write_text(
                "[verify]\nenabled = true\nmax_repair_attempts = 3\n"
                "pycompile = true\ncommands = [\"pytest -q\"]\n"
            )

        args = _argparse.Namespace(
            workspace=str(workspace), base_url=self.base_url, model=self.model,
            max_steps=self.max_steps, max_context_tokens=0,
            no_native_tools=False, read_only=False, yolo=True, no_color=True,
            no_graph=not self.verify, skill=None,   # graph (H2) on when verifying
        )
        built = _build_agent(args)
        if built is None:
            raise RuntimeError(f"could not build agent for workspace {workspace}")
        _ws, _config, _rec, loop, _color = built
        try:
            result = loop.run(goal)
        finally:
            for client in getattr(loop, "_mcp_clients", ()) or ():
                try:
                    client.close()
                except Exception:  # noqa: BLE001 - cleanup is best-effort
                    pass
        return _metrics_from_result(result)


# Tool names that mutate files -- used to count "edits" from the event stream.
_EDIT_TOOLS = frozenset({"write_file", "edit_file", "apply_edits"})


def _metrics_from_result(result) -> "RunMetrics | None":
    """Derive RunMetrics from a finished AgentResult. Never raises.

    steps/tokens come straight off the result + transcript; `edits` counts the
    mutating tool calls in the event stream, and `edits_kept` counts those whose
    target file still exists at the end (a coarse but honest "did the edit
    survive" proxy -- the graph-refactor slices refine it). Best-effort: any
    attribute the loop doesn't expose degrades to 0 rather than crashing a run.
    """
    try:
        steps = int(getattr(result, "steps", 0) or 0)
        events = list(getattr(result, "events", ()) or ())
        edits = sum(1 for e in events
                    if getattr(e, "kind", "") == "action"
                    and getattr(e, "tool", "") in _EDIT_TOOLS)
        # edits_kept: an edit "survived" if its target path still exists at the
        # end. Missing path arg -> treat as kept so we never under-report.
        kept = 0
        for e in events:
            if getattr(e, "kind", "") != "action" or getattr(e, "tool", "") not in _EDIT_TOOLS:
                continue
            args = getattr(e, "args", {}) or {}
            path = args.get("path") or args.get("file")
            kept += 1 if path is None else (1 if Path(path).exists() else 0)
        tokens = _estimate_tokens(getattr(result, "messages", ()) or ())
        return RunMetrics(steps=steps, tokens=tokens, edits=edits, edits_kept=kept)
    except Exception:  # noqa: BLE001 - metrics are best-effort, never fail a run
        return None


def _estimate_tokens(messages) -> int:
    """Best-effort token count of the final transcript. Reuses the model layer's
    counter if importable; else a cheap ~4-chars/token estimate; else 0."""
    try:
        text = "".join(str(m.get("content", "")) for m in messages if isinstance(m, dict))
    except Exception:  # noqa: BLE001
        return 0
    try:
        from nerva_core.local_llm_writer import estimate_tokens
        return int(estimate_tokens(text))
    except Exception:  # noqa: BLE001
        return len(text) // 4


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
    # With --repeat N, a task is run N times to average out model non-determinism.
    # `runs`/`passes` record the tally; `passed` is the majority verdict. Default
    # -1 means "not set" -> normalized in __post_init__ to a single run matching
    # `passed`, so single-run code paths and older saved JSON stay valid.
    runs: int = 1
    passes: int = -1
    # W0 (ADR-0019): optional cost/quality metrics for this task's run(s).
    # None when the runner reported nothing (older reports / metric-free fakes).
    metrics: "RunMetrics | None" = None

    def __post_init__(self) -> None:
        if self.passes < 0:
            self.passes = 1 if self.passed else 0
        # Accept a plain dict for `metrics` (from JSON round-trips) transparently.
        if isinstance(self.metrics, dict):
            self.metrics = RunMetrics.from_dict(self.metrics)

    @property
    def task_pass_rate(self) -> float:
        return self.passes / self.runs if self.runs else 0.0


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

    # --- W0 (ADR-0019) metric aggregates over the tasks that reported metrics ---
    @property
    def _metric_outcomes(self) -> "list[TaskOutcome]":
        return [o for o in self.outcomes if isinstance(o.metrics, RunMetrics)]

    @property
    def total_steps(self) -> int:
        return sum(o.metrics.steps for o in self._metric_outcomes)

    @property
    def total_tokens(self) -> int:
        return sum(o.metrics.tokens for o in self._metric_outcomes)

    @property
    def mean_edit_precision(self) -> "float | None":
        ms = self._metric_outcomes
        if not ms:
            return None
        return sum(o.metrics.edit_precision for o in ms) / len(ms)

    def to_dict(self) -> dict:
        d = asdict(self)
        # asdict() drops @property-only fields; re-attach the metric properties
        # onto each serialized outcome so a saved report carries edit_precision.
        for od, o in zip(d.get("outcomes", []), self.outcomes):
            if isinstance(o.metrics, RunMetrics):
                od["metrics"] = o.metrics.to_dict()
        d["pass_rate"] = self.pass_rate
        d["passed_count"] = self.passed_count
        d["total"] = self.total
        d["total_steps"] = self.total_steps
        d["total_tokens"] = self.total_tokens
        d["mean_edit_precision"] = self.mean_edit_precision
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

def _run_task_once(task: Task, agent_runner: AgentRunner,
                   tmp_root: "Path | None") -> TaskOutcome:
    """One attempt: set up a fresh fixture, drive the agent, score. Never raises."""
    started = time.monotonic()
    workspace = Path(tempfile.mkdtemp(prefix=f"revenant-eval-{task.name}-", dir=tmp_root))
    try:
        try:
            task.setup(workspace)
        except Exception as exc:  # noqa: BLE001
            return TaskOutcome(task.name, False, f"setup error: {exc!r}", time.monotonic() - started)
        try:
            metrics = agent_runner.run(workspace, task.goal)
        except Exception as exc:  # noqa: BLE001
            return TaskOutcome(task.name, False, f"agent error: {exc!r}", time.monotonic() - started)
        try:
            result = task.score(workspace)
        except Exception as exc:  # noqa: BLE001
            return TaskOutcome(task.name, False, f"scorer error: {exc!r}", time.monotonic() - started)
        return TaskOutcome(task.name, result.passed, result.detail,
                           time.monotonic() - started,
                           metrics=metrics if isinstance(metrics, RunMetrics) else None)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def run_task(task: Task, agent_runner: AgentRunner, *,
             tmp_root: "Path | None" = None, repeat: int = 1) -> TaskOutcome:
    """Run `task` `repeat` times and aggregate (ADR-0015 + --repeat).

    With repeat>1, the task runs in `repeat` fresh fixtures; the returned outcome
    records `passes`/`runs`, marks `passed` by majority (>50%), reports the MEDIAN
    time, and keeps a failure detail from a failing run (the useful diagnostic).
    A single run (repeat=1) behaves exactly as before.
    """
    repeat = max(1, repeat)
    attempts = [_run_task_once(task, agent_runner, tmp_root) for _ in range(repeat)]
    passes = sum(1 for a in attempts if a.passed)
    times = sorted(a.seconds for a in attempts)
    median = times[len(times) // 2]
    majority = passes * 2 > repeat  # strictly more than half
    # Prefer a failing detail (diagnostic) when not all passed, else the last one.
    fail_detail = next((a.detail for a in attempts if not a.passed), "")
    detail = (fail_detail if passes < repeat else attempts[-1].detail)
    if repeat > 1:
        detail = f"{passes}/{repeat} passed" + (f"; e.g. {detail}" if detail else "")
    return TaskOutcome(task.name, majority, detail, median, runs=repeat, passes=passes,
                       metrics=_mean_metrics([a.metrics for a in attempts]))


def _mean_metrics(items: "list[RunMetrics | None]") -> "RunMetrics | None":
    """Average the metrics across a task's attempts (None if none reported)."""
    present = [m for m in items if isinstance(m, RunMetrics)]
    if not present:
        return None
    n = len(present)
    return RunMetrics(
        steps=round(sum(m.steps for m in present) / n),
        tokens=round(sum(m.tokens for m in present) / n),
        edits=round(sum(m.edits for m in present) / n),
        edits_kept=round(sum(m.edits_kept for m in present) / n),
    )


def run_suite(
    tasks: "list[Task]",
    agent_runner: "AgentRunner | None",
    *,
    model: str = "",
    base_url: str = "",
    harness_flags: "dict | None" = None,
    tmp_root: "Path | None" = None,
    repeat: int = 1,
) -> Report:
    """Run every task in `tasks` and aggregate into a Report.

    `agent_runner=None` means "no model available": the run is skipped cleanly
    (empty outcomes, `skipped=True`) rather than attempting a model call. With
    `repeat>1` each task is run that many times (see run_task) to average out
    model non-determinism.
    """
    if agent_runner is None:
        return Report(
            model=model, base_url=base_url, harness_flags=harness_flags or {},
            skipped=True,
            skip_reason="no local model available -- model-driven eval run skipped",
        )

    started = time.monotonic()
    outcomes = [run_task(t, agent_runner, tmp_root=tmp_root, repeat=repeat) for t in tasks]
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
        tally = f" [{o.passes}/{o.runs}]" if o.runs > 1 else ""
        met = ""
        if isinstance(o.metrics, RunMetrics):
            m = o.metrics
            met = f" · {m.steps} steps, {m.tokens} tok, prec {m.edit_precision:.0%}"
        lines.append(f"  [{mark}]{tally} {o.name} (~{o.seconds:.1f}s){met} {o.detail}".rstrip())
    # Two aggregate views: tasks passed (majority) and total attempts passed.
    total_runs = sum(o.runs for o in report.outcomes)
    total_passes = sum(o.passes for o in report.outcomes)
    lines.append(
        f"tasks passed (majority): {report.passed_count}/{report.total} "
        f"({report.pass_rate * 100:.0f}%)"
    )
    if total_runs > report.total:  # repeats were used
        lines.append(
            f"attempts passed: {total_passes}/{total_runs} "
            f"({total_passes / total_runs * 100:.0f}%)  <- the real signal"
        )
    if report.mean_edit_precision is not None:  # metrics were reported
        lines.append(
            f"cost: {report.total_steps} steps, {report.total_tokens} tokens · "
            f"edit-precision {report.mean_edit_precision:.0%}"
        )
    lines.append(f"wall: {report.wall_seconds:.0f}s")
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

    # W0 (ADR-0019): metric deltas (candidate - baseline). Fewer steps/tokens and
    # higher edit-precision are improvements. None when a side lacks metrics.
    @property
    def delta_steps(self) -> int:
        return self.candidate.total_steps - self.baseline.total_steps

    @property
    def delta_tokens(self) -> int:
        return self.candidate.total_tokens - self.baseline.total_tokens

    @property
    def delta_edit_precision(self) -> "float | None":
        b, c = self.baseline.mean_edit_precision, self.candidate.mean_edit_precision
        if b is None or c is None:
            return None
        return c - b


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
    def _attempts(r):
        tr = sum(o.runs for o in r.outcomes)
        tp = sum(o.passes for o in r.outcomes)
        return tp, tr

    b_tp, b_tr = _attempts(cmp.baseline)
    c_tp, c_tr = _attempts(cmp.candidate)
    lines = [
        f"baseline  tasks {cmp.baseline.passed_count}/{cmp.baseline.total} "
        f"({cmp.baseline.pass_rate * 100:.0f}%)",
        f"candidate tasks {cmp.candidate.passed_count}/{cmp.candidate.total} "
        f"({cmp.candidate.pass_rate * 100:.0f}%)",
        f"delta (tasks): {cmp.delta_pass_rate * 100:+.0f} pts",
    ]
    if b_tr > cmp.baseline.total or c_tr > cmp.candidate.total:  # repeats used
        b_rate = b_tp / b_tr * 100 if b_tr else 0
        c_rate = c_tp / c_tr * 100 if c_tr else 0
        lines.append(
            f"attempts: baseline {b_tp}/{b_tr} ({b_rate:.0f}%) -> "
            f"candidate {c_tp}/{c_tr} ({c_rate:.0f}%)  delta {c_rate - b_rate:+.0f} pts"
            f"  <- the real signal"
        )
    if cmp.delta_edit_precision is not None:  # both sides carry metrics
        lines.append(
            f"cost: steps {cmp.delta_steps:+d}, tokens {cmp.delta_tokens:+d}, "
            f"edit-precision {cmp.delta_edit_precision * 100:+.0f} pts"
        )
    if cmp.improved:
        lines.append(f"improved (task-level): {', '.join(cmp.improved)}")
    if cmp.regressed:
        lines.append(f"regressed (task-level): {', '.join(cmp.regressed)}")
    return "\n".join(lines)


# --- regression gate (W0, ADR-0019) ------------------------------------------

def gate_regressions(baseline: Report, candidate: Report) -> "list[str]":
    """Return human-readable reasons the candidate regressed vs. the baseline.

    A regression is: the overall task pass-rate dropped, OR any task that passed
    in the baseline now fails. Cost metrics (steps/tokens) are reported by
    --compare but do NOT gate CI -- they're informational, not pass/fail. An
    empty list means "no regression": the gate passes.
    """
    reasons: list[str] = []
    cmp = compare_reports(baseline, candidate)
    if candidate.pass_rate < baseline.pass_rate:
        reasons.append(
            f"pass-rate dropped {baseline.pass_rate * 100:.0f}% -> "
            f"{candidate.pass_rate * 100:.0f}%"
        )
    if cmp.regressed:
        reasons.append(f"tasks now failing: {', '.join(cmp.regressed)}")
    return reasons


# --- CLI ---------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Revenant eval harness (H0, ADR-0015)")
    p.add_argument("--model", default="", help="Local model to evaluate (e.g. qwen2.5:14b)")
    p.add_argument("--base-url", default="http://localhost:11434", help="Local model server URL")
    p.add_argument("--max-steps", type=int, default=0, help="Per-task step cap (0 = harness default)")
    p.add_argument("--save", default="", help="Write the report as JSON to this path")
    p.add_argument("--compare", nargs=2, metavar=("BASELINE_JSON", "CANDIDATE_JSON"),
                    help="Diff two saved reports instead of running the suite")
    p.add_argument("--gate", metavar="BASELINE_JSON", default="",
                    help="Fail (exit 1) if this run regresses below the saved "
                         "baseline report (pass-rate drop or a task that passed "
                         "before now failing). For CI.")
    p.add_argument("--task", action="append", default=[], help="Run only this task (repeatable)")
    p.add_argument("--verify", action="store_true",
                   help="Enable the harness's verify→repair + code graph for the "
                        "run (for an on-vs-off --compare against a plain baseline).")
    p.add_argument("--repeat", type=int, default=1,
                   help="Run each task N times and report pass-rate (averages out "
                        "model non-determinism; default 1).")
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
        agent_runner = RevenantAgentRunner(args.model, args.base_url, args.max_steps,
                                           verify=args.verify)

    report = run_suite(
        tasks, agent_runner,
        model=args.model, base_url=args.base_url,
        harness_flags={"max_steps": args.max_steps, "verify": args.verify,
                       "repeat": args.repeat},
        repeat=args.repeat,
    )
    print(format_report(report))

    if args.save:
        Path(args.save).write_text(json.dumps(report.to_dict(), indent=2))
        print(f"saved report to {args.save}")

    if report.skipped:
        return 0

    # --gate: fail the run if it regressed below a saved baseline (CI use).
    if args.gate:
        baseline = Report.from_dict(json.loads(Path(args.gate).read_text()))
        reasons = gate_regressions(baseline, report)
        if reasons:
            print("REGRESSION GATE FAILED vs " + args.gate + ":", file=sys.stderr)
            for r in reasons:
                print(f"  - {r}", file=sys.stderr)
            return 1
        print(f"regression gate passed vs {args.gate}")
        return 0

    return 0 if report.pass_rate == 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
