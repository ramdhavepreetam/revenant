# ADR-0014 — Deterministic decompose + per-step verify (H3)

- **Status:** Proposed
- **Phase:** H3 · **F-slices:** H3.1 plan/decompose, H3.2 per-step driver, H3.3 tighter tool schemas
- **Date proposed:** 2026-07-31 · **Date implemented:** —
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

## Test plan
`tests/test_planner.py`:
- [ ] a well-formed plan parses into ordered steps; malformed → single-step fallback.
`tests/test_cli.py` (per-step driver, fake sub-agent + fake verifier):
- [ ] steps run in order; each advances only after its verify predicate passes.
- [ ] a step that fails its budget stops the run with the plan state shown.
- [ ] parent context holds only step summaries, not sub-transcripts.

## Acceptance criteria
- [ ] A multi-step goal is decomposed and driven step-by-step, each verified (H1)
      before the next — the model never juggles the whole plan.
- [ ] Tighter schemas reduce free-form generation on at least the edit path.
- [ ] Fallback + revert paths safe; tests green; ADRs + README updated.

## Progress log
- 2026-07-31 — Proposed. Composes P8 sub-agents + H1 verify into a per-step,
  self-correcting driver; closes failure ③.
