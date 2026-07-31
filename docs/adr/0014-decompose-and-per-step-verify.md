# ADR-0014 — Deterministic decompose + per-step verify (H3)

- **Status:** Implemented (H3.3 tighter-schemas deferred)
- **Phase:** H3 · **F-slices:** H3.1 plan/decompose, H3.2 per-step driver, H3.3 tighter tool schemas
- **Date proposed:** 2026-07-31 · **Date implemented:** 2026-07-31
- **Depends on:** ADR-0011 (strategy), ADR-0012 (H1 per-step verify), ADR-0009 (sub-agents), ADR-0006 (loop-driver)

## Context
ADR-0011 failure ③: a 14B **can't hold a long plan** — it loses the thread over a
multi-step task, forgets earlier steps, and doesn't self-correct across them. The
harness answer is to stop asking the model to hold the whole task: **decompose the
goal into small, independently-verifiable steps**, and drive them one at a time so
the model only ever reasons about one small thing.

## Decision
Add a **deterministic decomposition** pass that turns a goal into an explicit
step checklist, then a **per-step driver** that executes each step in a scoped,
budgeted sub-loop (reusing sub-agents, P8) and **verifies it (H1)** before moving
on. Tighten tool schemas so each call is a *small* decision.

Rejected: relying on the model to plan-and-track in-context (the failure we're
fixing); a rigid hard-coded pipeline (too brittle for open-ended coding) — the
plan is model-produced but harness-*tracked*.

## Design detail

### H3.1 — Plan / decompose (`nerva_agent/planner.py`)
- A planning call asks the model for an explicit, ordered checklist of small steps
  (each a one-line, independently-checkable sub-goal). Output is parsed into a
  `Plan = list[Step]`; a malformed plan degrades to a single step = the whole goal
  (never worse than today).
- The plan is **harness-owned state**, not something the model must re-derive each
  turn — it is shown compactly and advanced deterministically.

### H3.2 — Per-step driver (`revenant-cli`, reuses P8 + P5)
- For each step: `spawn_subagent(step.goal, scoped_tools)` runs it in isolation;
  the step's **success predicate is H1 verification** (compile/tests pass for the
  step's changes). A failed step repairs (H1) within budget, then either advances
  or stops with a clear summary.
- The parent keeps only step summaries (not sub-transcripts), so context stays
  small across a long task.

### H3.3 — Tighter tool schemas (`agent_tools` / tool defs)
- Prefer narrow, structured tool params over free-form generation where possible
  (e.g. an `apply_patch`-style structured edit over "rewrite the file"), shrinking
  the model's output surface per call — fewer degrees of freedom, fewer mistakes.
- This is incremental hardening of existing tool definitions, not a new protocol.

### CLI
- `revenant run --plan "<goal>"` (and a `loop`/autonomous variant) opts into the
  decompose-and-verify path; without it, behavior is unchanged.

## Failure & degradation
- Unparseable plan → single-step fallback (the whole goal), still H1-verified.
- A step that can't be made green after its budget → stop with the plan state and
  the failing step surfaced; revert that step via undo.

## Test plan — DONE (13 tests, 2026-07-31)
`tests/test_planner.py` (8):
- [x] well-formed numbered/paren/bullet lists parse into ordered steps;
      prose-tolerant; step-count capped; blank/prose → single-step fallback; render.
`tests/test_cli.py` (5, fake loop + patched planner):
- [x] `--plan` parses; steps run in order; history threads between them.
- [x] a step that doesn't finish (`stopped_reason != final`) halts the plan (rc 3).
- [x] single-step fallback runs the whole goal directly.

## Acceptance criteria
- [x] A multi-step goal is decomposed and driven step-by-step (verified end-to-end:
      a 2-step plan ran `[step 1/2]` then `[step 2/2]` to completion). With
      `[verify]` on (H1), each step's edits are checked via the loop's `after_tool`.
- [x] Fallback + halt-on-non-final paths safe; tests green (467 → 472); ADRs +
      README updated.
- [ ] *(H3.3)* tighter/structured tool schemas — **deferred** (incremental tool
      hardening; the decompose+drive core is the value).

## Implementation notes (what actually shipped)
- **H3.1** `nerva_agent/planner.py`: `parse_plan` (numbered/paren/bullet,
  prose-tolerant, capped at `MAX_STEPS`), `PLANNING_PROMPT`, `render_plan`;
  single-step fallback when nothing parses.
- **H3.2** `cli.py`: `revenant run --plan` → `_make_plan` (one constrained model
  call, degrades to single-step on any error) + `_run_planned` (drives each step
  through the **same loop** with threaded history, so H1 verify + H2 context hooks
  apply per step; halts if a step doesn't reach a final answer).
- **Deviation from the ADR:** steps run through `loop.run(step, history=…)` rather
  than a fresh `spawn_subagent` per step. This reuses the already-wired verify +
  context hooks and threads context forward simply; it's lower-risk than per-step
  sub-agent budgets and achieves the same "one small step at a time" goal. Per-step
  sub-agent isolation remains a future option (the P8 seam is still there).
  **H3.3** (tighter tool schemas) deferred.

## Progress log
- 2026-07-31 — Proposed. Composes P8 sub-agents + H1 verify into a per-step,
  self-correcting driver; closes failure ③.
- 2026-07-31 — **Implemented** (H3.1 planner + H3.2 `run --plan` driver; H3.3
  deferred). Steps run through the shared loop (verify+context hooks apply);
  13 tests, suite 467 → 472. Verified end-to-end: a 2-step plan drove to
  completion, one small step at a time.
