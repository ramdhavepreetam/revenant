# ADR-0023 — Adaptive planning + phase-aware routing (P-series)

- **Status:** Implemented — P0/P1/P2 all done
- **Phase:** P-series (0.9.0) · **P-slices:** P0 adaptive planner primitives ·
  P1 adaptive driver (retry → re-plan) · P2 phase-aware model routing
- **Date proposed:** 2026-08-03 · **Date implemented:** —
- **Depends on:** ADR-0014 (H3 decompose + per-step verify — this makes it
  adaptive), ADR-0011 (harness-carries-the-model — retry/re-plan is the harness
  recovering for a weak model), ADR-0009/W5 (sub-agent role routing — reuses
  `config_for_role` + the temporary-config-swap pattern) · **Relates to:**
  ADR-0001 (offline — all routing is local Ollama model selection), ADR-0008
  (capacity signal informs when a 2nd model can stay resident)

## Context
Phase 3 (last of the post-W plan: polish→0.7.0, memory→0.8.0, this) closes two
limits in the multi-step driver, both verified in code:

1. **Plans hard-halt on the first stumble.** `_run_planned` (`cli.py:902-929`)
   runs each planned step and, at line 924 (`if result.stopped_reason != "final":`),
   **aborts the whole plan** — a weak local model that trips on one recoverable
   step loses the entire run. `planner.py` is a flat one-shot decomposition with
   no notion of revising.
2. **One model does everything.** `_make_plan` (`cli.py:885`) uses `loop.config`
   (the `code` 7B) to *plan*, then the same 7B *executes* every step. The role
   router (`config_for_role`, `agent_router.py:69`) already maps `language`→a
   stronger 14B and `code`→7B — a strong-planner/cheap-executor split sits unused
   for planning.

## Decision
Make the plan driver **adaptive** and let a **stronger model shape the plan**,
without changing behavior for small machines, offline use, or non-`--plan` runs.

### Adaptive planning: retry, then re-plan (not hard-halt)
On a step that doesn't reach `final`:
1. **Retry** the step **once**, feeding the failure back as context (`RETRY_NUDGE`).
2. If it still fails, **re-plan the remaining steps** — one model call
   (`REPLAN_PROMPT`) given the goal, what's done, and the failure — replace the
   plan's tail, and continue.
3. **Bounded**: `max_step_retries` (default 1) and `max_replans` (default 2), plus
   the existing `MAX_STEPS`, guarantee termination. Exhausting the budget → the
   current graceful halt (exit 3) with a clear message.
This is the harness recovering for the model (ADR-0011), applied to the plan level.

### Phase-aware routing: auto when RAM allows
- The **planning** call (and re-plans) route to a stronger model via
  `config_for_role("language", …)`; **execution** stays on `loop.config` (the
  `code` model). Reuses the W5 temporary-config pattern (`cli.py:753`).
- **Auto-enabled only when `agent_capacity.keep_resident` is true** (RAM for two
  models loaded, `agent_capacity.py:137`). When false, or the role is unmapped, or
  `--no-route-roles` → today's single-model behavior (byte-identical).
- **Rejected:** always-on routing (would thrash two models on low-RAM machines);
  opt-in-only (leaves the win off for most users). Auto-on-when-affordable is the
  balance that respects the offline/small-machine invariant.

## Design detail (P-slices)

### P0 — adaptive planner primitives (`planner.py`, pure)
Keep `planner.py` free of loop/model calls (the CLI orchestrates). Add:
- `RETRY_NUDGE` — the feedback string prepended when re-running a failed step.
- `REPLAN_PROMPT` — asks for a fresh checklist for the REMAINING work, given the
  original goal + completed steps + the failure.
- a `replan(text, goal)` helper that reuses `parse_plan` to turn a re-plan reply
  into a `Plan` (degrading to keep-going on unparseable output).

### P1 — adaptive driver (`cli.py` `_run_planned`)
Replace the line-924 abort with the bounded retry→re-plan loop above. Progress
still prints per step (`[step i/N]`, plus "retrying…" / "re-planning remaining…").
Each step/retry is the same `loop.run(...)` (so H1 verify + H2 context still apply).

### P2 — phase-aware routing (`cli.py`)
`_make_plan` (and the re-plan call) resolve the planner config from
`config_for_role(plan_role, base_url, profiles)` when routing is enabled, else
`loop.config`. Enabled auto when `rec.keep_resident`; `[routing]` config +
`--no-route-roles` to control. Execution path unchanged.

### Config / flags
`[routing]`: `enabled` ("auto"|true|false, default auto), `plan_role` (default
"language"), `max_replans` (2), `max_step_retries` (1). Flags `--no-route-roles`,
`--max-replans`.

### Failure & degradation
Unmapped role → `loop.config`. `keep_resident` false → no routing. A failed
re-plan `call_model` → keep the existing remaining steps (never lose the plan).
Budgets guarantee termination. A run without `--plan` is completely untouched.

## Test plan (model-free / offline; CI bare `pytest`)
- **P0** `test_planner.py`: `replan` parses a fresh checklist; degrades on junk;
  `RETRY_NUDGE`/`REPLAN_PROMPT` format with the failure/goal.
- **P1** `test_cli.py` (fake `loop.run` scripted results): fail→final proves retry
  advances; fail→fail then a fake re-plan (fake `call_model`) proves the plan
  continues with new steps; a budget cap proves termination → exit 3.
- **P2** `test_cli.py`: `_make_plan` uses `config_for_role("language")` when
  `keep_resident` (assert the config handed to `call_model`), and `loop.config`
  when not / `--no-route-roles`. `routing_config` reader (defaults + override).

## Acceptance criteria
- [ ] A multi-step `--plan` run **retries** a failed step once, then **re-plans**
      the remainder rather than hard-halting; bounded so it always terminates.
- [ ] Planning routes to a stronger model when RAM allows (`keep_resident`);
      single-model + byte-identical when off / low-RAM / unmapped.
- [ ] `[routing]` config + `--no-route-roles`/`--max-replans` work.
- [ ] A run **without** `--plan` is untouched. Suite green; no new dependency.
- [ ] ADR-0023 + README + CHANGELOG updated.

## Open questions
- **Re-plan scope**: replace only the *remaining* tail (chosen) vs. the whole plan.
  Tail-only keeps completed work; simpler and safer. Revisit if it proves brittle.
- **Retry count**: 1 by default (a weak model often just needs a second look);
  configurable via `max_step_retries`.

## Progress log
- 2026-08-03 — Proposed + Accepted. Strategy written before code (durable record
  first). Decisions confirmed with the user: retry-then-re-plan on step failure;
  strong-planner/cheap-executor routing auto-on only when `keep_resident`.
  Implementation begins with P0.
- 2026-08-03 — **P0/P1/P2 Implemented — the P-series is complete.**
  - **P0** (`planner.py`, pure): `RETRY_NUDGE`/`retry_goal`, `REPLAN_PROMPT`/
    `build_replan_prompt`, and `replan()` (reuses `parse_plan`; degrades to an
    EMPTY plan on junk → the driver keeps its current steps). 7 tests.
  - **P1** (`cli.py` `_run_planned`): replaced the for-loop hard-halt with an
    adaptive work-queue — on a non-final step, retry once (`max_step_retries`) with
    the nudge, then re-plan the remaining work (`max_replans`) and continue;
    budgets + `MAX_STEPS` guarantee termination, exhaustion halts (exit 3).
    Refactored the plan call into `_plan_call`/`_planner_config` (shared, the P2
    seam). 4 driver tests; old hard-halt test updated.
  - **P2** (`cli.py`, `config.py`): `routing_config` reader (`[routing]`:
    enabled=auto/plan_role/max_replans/max_step_retries); `_build_agent` sets
    `loop._planner_config` from `config_for_role(plan_role, …)` when routing is on
    (auto ⇒ only when `rec.keep_resident`) — planning uses the stronger model,
    execution keeps the code model. `--no-route-roles`/`--max-replans`. Byte-
    identical when off/low-RAM/unmapped. 6 tests.
  - **No new dependency.** Suite **722 → 738**. Verified end-to-end (model-free):
    a plan whose step stumbles retries, then re-plans the remainder, and completes
    (exit 0) — a run that pre-P1 would have aborted. **Ships in 0.9.0.**
