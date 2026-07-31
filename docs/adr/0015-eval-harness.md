# ADR-0015 — Eval harness: measure the lift (H0)

- **Status:** Implemented
- **Phase:** H0 (cross-cutting; stand up early) · **F-slices:** H0.1 task suite, H0.2 scorer/runner, H0.3 baseline + regression gate
- **Date proposed:** 2026-07-31 · **Date implemented:** 2026-07-31
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
- [x] `python evals/run.py --model <m>` reports a pass-rate; skips cleanly (exit 0)
      when no `--model` is given or no server is reachable.
- [x] `--compare BASELINE.json CANDIDATE.json` quantifies the delta (improved /
      regressed lists) between two harness configs on the same model.
- [x] Eval logic unit-tested (31 tests) and offline; model runs are opt-in and NOT
      part of `pytest`; ADRs + README updated.

## Implementation notes (what actually shipped)
- **`evals/`** (new top-level dir): `tasks/base.py` (`Task`/`ScoreResult` +
  `run_pytest`), **5 tasks** targeting the ADR-0011 failure profile
  (fix-failing-test, add-function, make-file-exist, rename-across-callers,
  handle-edge-case), and `run.py` — an injectable `AgentRunner` Protocol
  (`RevenantAgentRunner` builds via `revenant_cli.cli._build_agent` in-process),
  temp-dir isolation, `Report`/`TaskOutcome` (JSON round-trippable via `--save`),
  `--compare`, and a `model_server_reachable()` probe so no-model runs skip.
- **`tests/test_evals.py`** (31, model-free): a `_FakeAgentRunner` drives the
  harness with no model; each task verified to fail pre-fix and pass post-fix
  (real fixes, not mocked scoring); setup/agent/scorer errors all degrade to a
  failed outcome; pass-rate aggregation + `--compare` delta + JSON round-trip.
- **Deviations:** built **5 tasks** (not the ADR's "10–20") per the smaller,
  real-first scope; the runner drives the agent **in-process** via `_build_agent`
  (not a subprocess) so it shares `cmd_run`'s construction path. Neither is
  touched by `pytest`, so both are low-risk internal choices. **Agent-built (H0);
  verified independently on the worktree: 31/31 + full suite green.**

## Progress log
- 2026-07-31 — Proposed. Recommended to stand up **before/alongside H1** so H1's
  impact is measured, not asserted.
- 2026-07-31 — **Implemented** (agent-built, verified). 31 tests; `evals/` with 5
  tasks + runner + `--compare`. Model-driven baseline is now runnable — the lift
  from H1/H2/H3 can be measured, not asserted.
