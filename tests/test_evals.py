"""Tests for the eval harness's OWN logic (H0, ADR-0015) -- model-free.

Covers: a task fixture is set up (and torn down) in a temp dir; a scorer maps a
passing/failing fixture to pass/fail correctly; the runner aggregates per-task
results into a pass-rate; `--compare` quantifies the delta between two runs;
degradation when no model is available and when a scorer errors. A FAKE
AgentRunner is injected throughout -- no real model or network is ever touched,
per the ADR's hard offline constraint.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from evals.tasks import ALL_TASKS, get_task
from evals.tasks.base import RunMetrics, ScoreResult, Task
from evals.run import (
    Report, TaskOutcome, compare_reports, format_compare, format_report,
    gate_regressions, model_server_reachable, run_suite, run_task,
)


# --- fakes ---------------------------------------------------------------

class _FakeAgentRunner:
    """Applies a caller-supplied edit function instead of calling a model."""

    def __init__(self, apply):
        self.apply = apply
        self.calls: list[tuple[Path, str]] = []

    def run(self, workspace: Path, goal: str) -> None:
        self.calls.append((workspace, goal))
        self.apply(workspace)


class _ExplodingAgentRunner:
    def run(self, workspace: Path, goal: str) -> None:
        raise RuntimeError("model call blew up")


class _MetricAgentRunner:
    """A fake runner that applies an edit AND reports RunMetrics (W0)."""

    def __init__(self, apply, metrics: RunMetrics):
        self.apply = apply
        self.metrics = metrics

    def run(self, workspace: Path, goal: str) -> RunMetrics:
        self.apply(workspace)
        return self.metrics


def _noop(_workspace: Path) -> None:
    pass


# --- task fixture setup/teardown in a temp dir --------------------------------

@pytest.mark.parametrize("task", ALL_TASKS, ids=[t.name for t in ALL_TASKS])
def test_every_task_setup_populates_a_temp_dir(tmp_path, task: Task):
    ws = tmp_path / task.name
    ws.mkdir()
    task.setup(ws)
    # Every fixture must actually write something -- an empty fixture is a bug.
    assert any(ws.iterdir())


@pytest.mark.parametrize("task", ALL_TASKS, ids=[t.name for t in ALL_TASKS])
def test_every_task_fails_before_any_fix_is_applied(tmp_path, task: Task):
    """The unmodified fixture must score as a FAIL -- otherwise the task is
    trivially "solved" by doing nothing, and measures nothing."""
    ws = tmp_path / task.name
    ws.mkdir()
    task.setup(ws)
    result = task.score(ws)
    assert result.passed is False


def test_run_task_uses_an_isolated_temp_dir_and_cleans_up(tmp_path):
    task = get_task("make_file_exist")
    seen_workspace = {}

    def apply(ws: Path) -> None:
        seen_workspace["path"] = ws
        (ws / "CHANGELOG.md").write_text("# Changelog\n\n## 0.1.0\n- Initial release.\n")

    outcome = run_task(task, _FakeAgentRunner(apply), tmp_root=tmp_path)
    assert outcome.passed is True
    # The workspace was a fresh temp dir under tmp_root, not the repo itself.
    ws = seen_workspace["path"]
    assert str(ws).startswith(str(tmp_path))
    # And it was cleaned up after scoring (ADR-0015: fixtures run in a temp dir).
    assert not ws.exists()


# --- scorer correctness: passing fixture vs failing fixture -------------------

def test_scorer_passes_a_correctly_fixed_fixture(tmp_path):
    task = get_task("fix_failing_test")
    ws = tmp_path / "ws"
    ws.mkdir()
    task.setup(ws)
    # Apply the real fix: remove the off-by-one.
    (ws / "mathlib.py").write_text('def add(a, b):\n    return a + b\n')
    result = task.score(ws)
    assert result.passed is True


def test_scorer_fails_an_unfixed_fixture(tmp_path):
    task = get_task("fix_failing_test")
    ws = tmp_path / "ws"
    ws.mkdir()
    task.setup(ws)
    result = task.score(ws)
    assert result.passed is False
    assert result.detail  # a useful failure detail is captured, not silently empty


def test_scorer_rejects_a_partial_rename():
    """rename_across_callers: renaming the def but missing one caller must fail."""
    import tempfile
    task = get_task("rename_across_callers")
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        task.setup(ws)
        # Rename the definition and ONE call site, but miss farewell_message.
        (ws / "greetings.py").write_text(
            'def greeting(name):\n    return f"Hello, {name}!"\n'
        )
        (ws / "app.py").write_text(
            'from greetings import greeting\n\n\n'
            'def welcome_message(name):\n    return greeting(name).upper()\n\n\n'
            'def farewell_message(name):\n    from greetings import greet\n'
            '    return greet(name) + " Goodbye!"\n'
        )
        result = task.score(ws)
        assert result.passed is False


def test_scorer_accepts_a_complete_rename():
    import tempfile
    task = get_task("rename_across_callers")
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        task.setup(ws)
        (ws / "greetings.py").write_text(
            'def greeting(name):\n    return f"Hello, {name}!"\n'
        )
        (ws / "app.py").write_text(
            'from greetings import greeting\n\n\n'
            'def welcome_message(name):\n    return greeting(name).upper()\n\n\n'
            'def farewell_message(name):\n    return greeting(name) + " Goodbye!"\n'
        )
        result = task.score(ws)
        assert result.passed is True


def test_make_file_exist_scorer_rejects_empty_stub(tmp_path):
    task = get_task("make_file_exist")
    ws = tmp_path / "ws"
    ws.mkdir()
    task.setup(ws)
    (ws / "CHANGELOG.md").write_text("")  # exists, but no required content
    result = task.score(ws)
    assert result.passed is False
    assert "CHANGELOG.md" in result.detail


# --- scorer error handling: never crashes the run ------------------------------

def test_a_raising_scorer_counts_as_fail_not_a_crash(tmp_path):
    def bad_setup(ws: Path) -> None:
        pass

    def bad_score(ws: Path) -> ScoreResult:
        raise ValueError("scorer bug")

    task = Task(name="broken", goal="whatever", setup=bad_setup, score=bad_score)
    outcome = run_task(task, _FakeAgentRunner(_noop), tmp_root=tmp_path)
    assert outcome.passed is False
    assert "scorer error" in outcome.detail
    assert "scorer bug" in outcome.detail


def test_a_raising_agent_counts_as_fail_not_a_crash(tmp_path):
    task = get_task("make_file_exist")
    outcome = run_task(task, _ExplodingAgentRunner(), tmp_root=tmp_path)
    assert outcome.passed is False
    assert "agent error" in outcome.detail


def test_a_raising_setup_counts_as_fail_not_a_crash(tmp_path):
    def bad_setup(ws: Path) -> None:
        raise OSError("disk full")

    task = Task(name="broken_setup", goal="g", setup=bad_setup, score=lambda ws: ScoreResult.ok())
    outcome = run_task(task, _FakeAgentRunner(_noop), tmp_root=tmp_path)
    assert outcome.passed is False
    assert "setup error" in outcome.detail


# --- runner aggregation: pass-rate ---------------------------------------------

def test_run_suite_aggregates_pass_rate(tmp_path):
    fixes = {
        "fix_failing_test": lambda ws: (ws / "mathlib.py").write_text(
            "def add(a, b):\n    return a + b\n"
        ),
        "make_file_exist": lambda ws: (ws / "CHANGELOG.md").write_text(
            "# Changelog\n\n## 0.1.0\n- init\n"
        ),
    }

    def apply(ws: Path) -> None:
        # Only fix the tasks we know how to fix; leave the rest broken.
        for key, fn in fixes.items():
            if key in ws.name:
                fn(ws)

    tasks = [get_task("fix_failing_test"), get_task("make_file_exist"), get_task("add_function")]
    report = run_suite(
        tasks, _FakeAgentRunner(apply),
        model="fake-model", base_url="http://fake", tmp_root=tmp_path,
    )
    assert report.total == 3
    assert report.passed_count == 2
    assert report.pass_rate == pytest.approx(2 / 3)
    assert report.skipped is False
    assert report.model == "fake-model"


def test_run_suite_empty_task_list_has_zero_pass_rate(tmp_path):
    report = run_suite([], _FakeAgentRunner(_noop), tmp_root=tmp_path)
    assert report.total == 0
    assert report.pass_rate == 0.0


def test_run_suite_skips_cleanly_when_no_agent_runner(tmp_path):
    report = run_suite(ALL_TASKS, None, model="qwen2.5:14b", tmp_root=tmp_path)
    assert report.skipped is True
    assert report.total == 0
    assert "skip" in report.skip_reason.lower()


def test_format_report_on_skipped_run_mentions_skip():
    report = Report(skipped=True, skip_reason="no local model available")
    text = format_report(report)
    assert "skipped" in text
    assert "no local model available" in text


def test_format_report_shows_pass_and_fail_marks():
    report = Report(
        model="m", outcomes=[
            TaskOutcome("a", True, "ok", 0.1),
            TaskOutcome("b", False, "boom", 0.2),
        ],
        wall_seconds=0.3,
    )
    text = format_report(report)
    assert "PASS" in text and "FAIL" in text
    assert "a" in text and "b" in text
    assert "1/2" in text


# --- --compare mode -------------------------------------------------------

def test_compare_reports_computes_delta_and_per_task_changes():
    baseline = Report(model="m", outcomes=[
        TaskOutcome("t1", False), TaskOutcome("t2", True), TaskOutcome("t3", True),
    ])
    candidate = Report(model="m", outcomes=[
        TaskOutcome("t1", True), TaskOutcome("t2", True), TaskOutcome("t3", False),
    ])
    cmp = compare_reports(baseline, candidate)
    assert cmp.delta_pass_rate == pytest.approx(0.0)  # 2/3 both -> net delta 0
    assert cmp.improved == ["t1"]
    assert cmp.regressed == ["t3"]


def test_compare_reports_all_improved():
    baseline = Report(model="m", outcomes=[TaskOutcome("t1", False), TaskOutcome("t2", False)])
    candidate = Report(model="m", outcomes=[TaskOutcome("t1", True), TaskOutcome("t2", True)])
    cmp = compare_reports(baseline, candidate)
    assert cmp.delta_pass_rate == pytest.approx(1.0)
    assert sorted(cmp.improved) == ["t1", "t2"]
    assert cmp.regressed == []


def test_format_compare_reports_delta_and_lists():
    baseline = Report(model="m", outcomes=[TaskOutcome("t1", False), TaskOutcome("t2", True)])
    candidate = Report(model="m", outcomes=[TaskOutcome("t1", True), TaskOutcome("t2", True)])
    text = format_compare(compare_reports(baseline, candidate))
    assert "delta" in text
    assert "t1" in text  # improved list mentions it


def test_report_round_trips_through_dict():
    report = Report(
        model="qwen2.5:14b", base_url="http://localhost:11434",
        harness_flags={"verify": True},
        outcomes=[TaskOutcome("t1", True, "ok", 1.5)],
        wall_seconds=1.5,
    )
    restored = Report.from_dict(report.to_dict())
    assert restored.model == report.model
    assert restored.outcomes[0].name == "t1"
    assert restored.outcomes[0].passed is True
    assert restored.pass_rate == 1.0


# --- degrade gracefully: no model reachable ------------------------------------

def test_model_server_reachable_returns_false_for_a_bogus_url():
    # Nothing is listening on this port; must return False, not raise.
    assert model_server_reachable("http://127.0.0.1:1", timeout=0.2) is False


# --- W0 (ADR-0019): metrics, aggregation, compare deltas, regression gate -------

def test_run_metrics_edit_precision_ratio_and_empty_default():
    assert RunMetrics(edits=4, edits_kept=3).edit_precision == pytest.approx(0.75)
    # No edits -> precision is 1.0 (nothing to get wrong), never a divide-by-zero.
    assert RunMetrics(edits=0, edits_kept=0).edit_precision == 1.0


def test_run_task_carries_reported_metrics(tmp_path):
    task = get_task("make_file_exist")

    def apply(ws: Path) -> None:
        (ws / "CHANGELOG.md").write_text("# Changelog\n\n## 0.1.0\n- init\n")

    runner = _MetricAgentRunner(apply, RunMetrics(steps=5, tokens=1200, edits=2, edits_kept=2))
    outcome = run_task(task, runner, tmp_root=tmp_path)
    assert outcome.passed is True
    assert isinstance(outcome.metrics, RunMetrics)
    assert outcome.metrics.steps == 5
    assert outcome.metrics.edit_precision == 1.0


def test_metric_free_runner_leaves_outcome_metrics_none(tmp_path):
    # The historical fake returns None -> the outcome simply has no metrics,
    # and metric aggregates degrade to "no metrics" rather than crashing.
    task = get_task("make_file_exist")
    outcome = run_task(task, _FakeAgentRunner(_noop), tmp_root=tmp_path)
    assert outcome.metrics is None


def test_report_aggregates_metrics_across_tasks():
    report = Report(model="m", outcomes=[
        TaskOutcome("a", True, metrics=RunMetrics(steps=3, tokens=100, edits=2, edits_kept=2)),
        TaskOutcome("b", True, metrics=RunMetrics(steps=5, tokens=200, edits=4, edits_kept=2)),
    ])
    assert report.total_steps == 8
    assert report.total_tokens == 300
    # mean of precision(1.0) and precision(0.5) = 0.75
    assert report.mean_edit_precision == pytest.approx(0.75)


def test_report_mean_edit_precision_none_when_no_metrics():
    report = Report(model="m", outcomes=[TaskOutcome("a", True), TaskOutcome("b", False)])
    assert report.mean_edit_precision is None
    assert report.total_steps == 0


def test_report_with_metrics_round_trips_through_dict():
    report = Report(model="m", outcomes=[
        TaskOutcome("t1", True, "ok", 1.5, metrics=RunMetrics(steps=4, tokens=500, edits=3, edits_kept=2)),
    ])
    restored = Report.from_dict(report.to_dict())
    m = restored.outcomes[0].metrics
    assert isinstance(m, RunMetrics)
    assert (m.steps, m.tokens, m.edits, m.edits_kept) == (4, 500, 3, 2)
    assert m.edit_precision == pytest.approx(2 / 3)
    assert restored.total_steps == 4


def test_format_report_shows_metrics_line_when_present():
    report = Report(model="m", outcomes=[
        TaskOutcome("a", True, metrics=RunMetrics(steps=3, tokens=90, edits=2, edits_kept=1)),
    ])
    text = format_report(report)
    assert "steps" in text and "tokens" in text and "edit-precision" in text


def test_compare_reports_metric_deltas():
    baseline = Report(model="m", outcomes=[
        TaskOutcome("t1", True, metrics=RunMetrics(steps=8, tokens=400, edits=4, edits_kept=2)),
    ])
    candidate = Report(model="m", outcomes=[
        TaskOutcome("t1", True, metrics=RunMetrics(steps=5, tokens=300, edits=4, edits_kept=4)),
    ])
    cmp = compare_reports(baseline, candidate)
    assert cmp.delta_steps == -3          # fewer steps = cheaper
    assert cmp.delta_tokens == -100
    assert cmp.delta_edit_precision == pytest.approx(0.5)  # 0.5 -> 1.0
    assert "edit-precision" in format_compare(cmp)


def test_compare_metric_deltas_none_when_a_side_lacks_metrics():
    baseline = Report(model="m", outcomes=[TaskOutcome("t1", True)])  # no metrics
    candidate = Report(model="m", outcomes=[
        TaskOutcome("t1", True, metrics=RunMetrics(steps=5, tokens=300, edits=1, edits_kept=1)),
    ])
    assert compare_reports(baseline, candidate).delta_edit_precision is None


def test_gate_passes_when_no_regression():
    baseline = Report(model="m", outcomes=[TaskOutcome("t1", True), TaskOutcome("t2", False)])
    candidate = Report(model="m", outcomes=[TaskOutcome("t1", True), TaskOutcome("t2", True)])
    assert gate_regressions(baseline, candidate) == []  # improved -> no regression


def test_gate_fails_on_a_dropped_task():
    baseline = Report(model="m", outcomes=[TaskOutcome("t1", True), TaskOutcome("t2", True)])
    candidate = Report(model="m", outcomes=[TaskOutcome("t1", True), TaskOutcome("t2", False)])
    reasons = gate_regressions(baseline, candidate)
    assert reasons  # non-empty -> gate fails
    assert any("t2" in r for r in reasons)
    assert any("pass-rate" in r for r in reasons)


def test_the_three_w0_rename_tasks_are_registered():
    names = {t.name for t in ALL_TASKS}
    assert {"rename_across_package", "rename_class_across_modules",
            "rename_with_shadow"} <= names


def test_all_tasks_have_unique_names():
    names = [t.name for t in ALL_TASKS]
    assert len(names) == len(set(names))


def test_get_task_unknown_name_raises_with_known_list():
    with pytest.raises(KeyError, match="fix_failing_test"):
        get_task("does-not-exist")


# --- --repeat aggregation (methodology fix for model non-determinism) --------

from evals.run import run_task, TaskOutcome, Report


class _FlakyRunner:
    """An AgentRunner whose fixes succeed on a scripted subset of attempts.

    `succeed_on` is a set of 0-based attempt indices where it 'fixes' the file so
    the scorer passes; other attempts leave it broken.
    """
    def __init__(self, succeed_on):
        self.succeed_on = set(succeed_on)
        self.n = 0
    def run(self, workspace, goal):
        from pathlib import Path
        idx = self.n; self.n += 1
        # Each task's scorer runs pytest on a fixture; we just make a marker file
        # the fake task below checks. (Real tasks are model-driven; unit tests use
        # a synthetic task.)
        (Path(workspace) / "result").write_text("pass" if idx in self.succeed_on else "fail")


def _synthetic_task():
    from evals.tasks.base import Task, ScoreResult
    from pathlib import Path
    return Task(
        name="synthetic",
        goal="make result say pass",
        setup=lambda ws: (Path(ws) / "seed").write_text("x"),
        score=lambda ws: (ScoreResult.ok("ok") if (Path(ws) / "result").exists()
                          and (Path(ws) / "result").read_text() == "pass"
                          else ScoreResult.fail("not passing")),
        description="synthetic",
    )


def test_repeat_records_pass_tally():
    task = _synthetic_task()
    out = run_task(task, _FlakyRunner(succeed_on={0, 2, 3}), repeat=5)
    assert out.runs == 5
    assert out.passes == 3
    assert out.task_pass_rate == 0.6


def test_repeat_majority_verdict_pass():
    out = run_task(_synthetic_task(), _FlakyRunner(succeed_on={0, 1, 2}), repeat=5)  # 3/5
    assert out.passed is True   # majority (>50%)


def test_repeat_majority_verdict_fail():
    out = run_task(_synthetic_task(), _FlakyRunner(succeed_on={0, 1}), repeat=5)  # 2/5
    assert out.passed is False  # not a majority


def test_repeat_tie_is_not_majority():
    out = run_task(_synthetic_task(), _FlakyRunner(succeed_on={0, 1}), repeat=4)  # 2/4 tie
    assert out.passed is False  # strict majority required


def test_repeat_one_matches_single_run():
    out = run_task(_synthetic_task(), _FlakyRunner(succeed_on={0}), repeat=1)
    assert out.runs == 1 and out.passes == 1 and out.passed is True


def test_outcome_defaults_normalize_for_failed_single_run():
    # A plain failed single-run outcome must show passes=0 (not the default).
    o = TaskOutcome("t", False, "nope", 1.0)
    assert o.runs == 1 and o.passes == 0


def test_report_json_roundtrip_with_repeat_fields():
    o = TaskOutcome("t", True, "3/5 passed", 2.0, runs=5, passes=3)
    r = Report(model="m", outcomes=[o])
    r2 = Report.from_dict(r.to_dict())
    assert r2.outcomes[0].runs == 5 and r2.outcomes[0].passes == 3
