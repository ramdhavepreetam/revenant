# ADR-0006 — Loops: autonomous & recurring runs (Phase 5)

- **Status:** Proposed
- **Phase:** P5 · **F-slices:** F13.1 iterate-until-done, F13.2 safety substrate, F13.3 triggers, F13.4 run journal
- **Date proposed:** 2026-07-30 · **Date implemented:** —
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

## Test plan
`tests/test_loop_driver.py` (with a fake AgentLoop):
- [ ] stops when the predicate passes; threads history between iterations.
- [ ] respects `--max-iterations` / token / wall budgets.
- [ ] `--dry-run` records intended edits without touching disk.
- [ ] each iteration creates a checkpoint boundary.
`tests/test_cli.py`:
- [ ] `--until "<cmd>"` maps to a predicate; `--until-tests` uses the test cmd.
- [ ] `--watch` re-triggers on a simulated file change (mtime poll path).

## Acceptance criteria
- [ ] `revenant loop --autonomous --until-tests "make tests pass"` iterates,
      checkpoints each round, and stops on green within budget.
- [ ] `--dry-run` previews an autonomous run with zero disk writes.
- [ ] Undo can step back a whole iteration.
- [ ] Tests green; ADR + README updated; F13 marked Implemented.

## Progress log
- 2026-07-30 — Proposed. Gated on ADR-0010 (undo tests) and ADR-0007 (journal).
