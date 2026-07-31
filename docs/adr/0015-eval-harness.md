# ADR-0015 — Eval harness: measure the lift (H0)

- **Status:** Proposed
- **Phase:** H0 (cross-cutting; stand up early) · **F-slices:** H0.1 task suite, H0.2 scorer/runner, H0.3 baseline + regression gate
- **Date proposed:** 2026-07-31 · **Date implemented:** —
- **Depends on:** ADR-0011 (strategy) · **Supports:** ADR-0012/0013/0014 (proves their lift)

## Context
The entire H-series thesis is **harness lift on a fixed model**. Without a
benchmark run against the *same* local model, "did H1 help?" is a vibe. For a
weak-model project this matters more than for a frontier one: the gains are
incremental and easy to fool yourself about. We need a way to hold the model
constant and measure whether each harness change moves the pass-rate.

## Decision
Build a small, offline **eval harness**: a suite of real coding tasks each with a
deterministic pass/fail scorer, plus a runner that drives Revenant end-to-end and
reports the pass-rate. Establish a **baseline** on the current harness, then
re-run after each H-phase to quantify the gain — with the model held constant.

Rejected: a large public benchmark (heavy, online, and not representative of this
tool's local-coding use); subjective "seems better" evaluation (the thing we're
replacing).

## Design detail

### H0.1 — Task suite (`evals/tasks/`)
- 10–20 self-contained tasks, each a tiny fixture repo + a goal + a **scorer**
  (usually: a hidden test that must pass, or a file that must exist/match).
- Tasks target the failure profile (ADR-0011): "fix this failing test", "add a
  function used by X", "rename across callers" — things a bare 7B/14B gets wrong
  without the harness.
- Each task is offline and deterministic (no network, fixed inputs).

### H0.2 — Runner + scorer (`evals/run.py`)
- For each task: set up the fixture in a temp dir, run Revenant (`run`/`loop`)
  against a **configured local model**, then execute the task's scorer → pass/fail.
- Emits a report: per-task result + overall pass-rate + wall-time. A `--compare`
  mode diffs two runs (e.g. verify on vs. off) so a change's lift is one number.
- Model and harness flags are recorded with each run so results are attributable.

### H0.3 — Baseline + regression gate
- Record the current-harness baseline pass-rate. After each H-phase, re-run and
  log the delta in that phase's ADR progress log (the lift becomes evidence, not
  a claim).
- Optionally a CI-lite gate: the eval must not *regress* below baseline (run
  locally / opt-in, since it needs a model — not part of the offline unit suite).

## Failure & degradation
- No local model available → the runner skips with a clear message (evals are
  opt-in; they are NOT part of `pytest` — that stays model-free and offline).
- A task scorer error → that task counts as fail with the error captured, never
  crashes the whole run.

## Test plan
The eval harness's *own* logic is unit-tested (model-free):
`tests/test_evals.py`:
- [ ] a task fixture is set up and torn down in a temp dir.
- [ ] a scorer maps a passing/failing fixture to pass/fail correctly.
- [ ] the runner aggregates per-task results into a pass-rate + `--compare` delta.
(The actual model-driven runs are invoked manually, not in `pytest`.)

## Acceptance criteria
- [ ] `python evals/run.py --model qwen2.5:14b` reports a baseline pass-rate over
      the task suite.
- [ ] `--compare` quantifies the delta between two harness configs on the same model.
- [ ] Eval logic unit-tested and offline; model runs are opt-in; ADRs + README updated.

## Progress log
- 2026-07-31 — Proposed. Recommended to stand up **before/alongside H1** so H1's
  impact is measured, not asserted.
