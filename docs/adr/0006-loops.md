# ADR-0006 — Loops: autonomous & recurring runs (Phase 5)

- **Status:** Implemented (F13.3 triggers deferred)
- **Phase:** P5 · **F-slices:** F13.1 iterate-until-done, F13.2 safety substrate, F13.3 triggers, F13.4 run journal
- **Date proposed:** 2026-07-30 · **Date implemented:** 2026-07-30
- **Depends on:** ADR-0010 (undo hardened), ADR-0007 (run journal) · **Blocks:** —

## Context
The headline capability: run a goal **unattended** — iterate until a success
condition is met, or re-run on a schedule / file-change trigger. This is a thin
driver over the existing `loop.run(goal, history=…)` that the REPL already uses.
The risk is that it **edits files without a human watching**, so it is gated on
undo being bulletproof (ADR-0010) and on a run journal for auditability
(ADR-0007).

## Decision
Add an **iterate-until-done driver** with a hard **safety substrate** (budgets,
per-iteration checkpoint, dry-run) and optional **triggers** (schedule, watch).
Autonomy is opt-in and always bounded.

Rejected: an open-ended "keep going forever" loop. Every loop has an explicit
stop predicate and a wall-clock/step/token budget; there is no unbounded mode.

## Design detail

### F13.1 — Iterate-until-done driver (`revenant_cli` + a small driver in `nerva-agent`)
- `loop_until(goal, predicate, budget) `:
  - Run one `AgentLoop.run(goal, history=prev)`.
  - Evaluate a **success predicate** (see below). If satisfied → stop("done").
  - Else thread `result.messages` forward, append a "not yet: <reason>" nudge,
    and iterate until the budget is hit.
- **Success predicates** (composable, declared on the CLI or a skill):
  - `--until-tests` — run the configured test command; success on exit 0.
  - `--until-file <path>` — a path exists / matches.
  - `--until "<shell test>"` — arbitrary user command exits 0.
  - default: the model declares completion (FinalAnswer) — weakest, still bounded.

### F13.2 — Safety substrate (`nerva-agent` loop + driver)
- **Budgets:** `--max-iterations`, `--max-tokens`, `--max-wall <duration>`.
- **Per-iteration checkpoint:** force a `Checkpointer` snapshot boundary each
  iteration so `revenant undo` can step back one whole iteration (builds on
  ADR-0010). For shell side-effects, prefer git-native checkpoints once P8 lands.
- **Dry-run (`--dry-run`):** run the loop with edit/bash tools swapped for
  no-op recording tools that log intended actions without executing — lets a
  user preview an autonomous run.
- Autonomous runs imply `auto_approve` (no human at the prompt) **only** inside
  the declared budget and with checkpointing on; this is stated explicitly and
  requires an opt-in flag (e.g. `--autonomous`).

### F13.3 — Triggers (`cli.py`)
- `revenant loop --every <dur> "goal"` — re-run on a schedule.
- `revenant loop --watch '<glob>' "goal"` — re-run when matching files change
  (respecting ignore globs). Watch uses P7's incremental machinery if present,
  else a simple mtime poll.

### F13.4 — Run journal (`nerva-core` storage, shared with ADR-0007)
- Persist each iteration's transcript + resulting diff + predicate outcome under
  the data dir, so a loop is auditable and resumable. Reuses the session store
  from ADR-0007.

## Failure & degradation
- Predicate command errors → treated as "not satisfied", loop continues within
  budget, error surfaced in the journal.
- Budget exhausted → stop with a clear summary + how to resume.
- Any tool failure → normal recoverable observation; never a silent partial edit
  without a checkpoint.

## Test plan — DONE (19 tests, 2026-07-30)
`tests/test_loop_driver.py` (12, fake run_fn):
- [x] stops when the predicate passes; threads history between iterations; nudges.
- [x] respects `--max-iterations` (incl. the 0→run-once floor), wall, and token budgets.
- [x] `on_iteration` callback fires each round.
- [x] built-in predicates: model-final, command exit-0 / non-zero / bad-command,
      file-exists.
`tests/test_cli.py` (7):
- [x] loop parser flags; default predicate stops on model-final.
- [x] `--until-file` iterates until the file is created; `--max-iterations` bound
      returns exit 3.
- [x] `--autonomous` forces yolo; `--dry-run` forces read-only + no yolo.
- [x] each loop journals a resumable session (F13.4).

## Acceptance criteria
- [x] `revenant loop --until-tests "…"` (and `--until`, `--until-file`) iterates
      and stops on success within budget (verified end-to-end with a fake agent
      that satisfies a file predicate on iteration 2).
- [x] `--dry-run` previews with zero disk writes (forced read-only).
- [x] A per-iteration checkpoint boundary is taken so `revenant undo` can step
      back a whole iteration (builds on ADR-0010).
- [x] Each loop is journaled as a resumable session (F13.4, via session_store).
- [x] Tests green (290 → 309); ADR + README updated; F13 marked Implemented
      (F13.3 triggers deferred).

## Implementation notes (what actually shipped)
- **Driver** `nerva_agent/loop_driver.py`: `loop_until(goal, run_fn, predicate,
  budget, on_iteration)`. `run_fn` is injected (the CLI passes `loop.run`), so the
  driver has no ChatConfig/registry dependency and is trivially testable.
  Predicates are plain callables; built-ins: `model_final_predicate`,
  `command_predicate`, `file_exists_predicate`.
- **Bounded always** (ADR-0006): `Budget{max_iterations, max_wall_seconds,
  max_tokens}`; `max_iterations=0` still runs exactly once — there is no unbounded
  mode. A predicate that errors counts as not-done.
- **`revenant loop`** wires flags → predicate; `--autonomous` sets yolo (within
  budget) and takes a per-iteration checkpoint boundary; `--dry-run` forces
  read-only (preview, no writes); every iteration is journaled via `session_store`
  so a stopped loop prints `revenant resume <id>`.
- **Deviation — F13.3 triggers deferred:** `--every` (schedule) and `--watch`
  (re-run on change) are NOT built. `--watch` is best done on P7's incremental
  index; both are a clean follow-up. `--max-tokens` is a driver-level estimate
  (chars/4), not exact model accounting.

## Progress log
- 2026-07-30 — Proposed. Gated on ADR-0010 (undo tests) and ADR-0007 (journal).
- 2026-07-30 — **Implemented** (minus F13.3 triggers). Driver + budgets + dry-run
  + run journal + `revenant loop` subcommand. 19 tests, suite 290 → 309. Verified
  end-to-end. Both gates (undo P2.5, journal P6) were in place. Next: P7 Code
  graph (ADR-0008) or P8 (ADR-0009); triggers (F13.3) a small follow-up.
- 2026-07-31 — **F13.3 `--watch` trigger added** (deferred follow-up): `revenant
  loop --watch '<glob>'` re-runs the whole loop on a matching file change
  (mtime poll, ignore-aware; `--watch-interval` configurable). The `--every`
  schedule trigger remains deferred (better served by an external cron/OS timer).
