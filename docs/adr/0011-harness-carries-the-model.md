# ADR-0011 — The harness carries the model (H-series strategy)

- **Status:** Accepted
- **Phase:** strategy for the H-series (0.3.0) · **F-slices:** H0–H3 (see child ADRs)
- **Date proposed:** 2026-07-31 · **Date implemented:** — (strategy; children track work)
- **Depends on:** ADR-0003 (loop), ADR-0006 (loop-driver), ADR-0008 (code graph), ADR-0010 (undo)
- **Children:** ADR-0012 (H1), ADR-0013 (H2), ADR-0014 (H3), ADR-0015 (H0)

## Context
Revenant runs a **fixed, non-frontier local model** — targeted at a 14B
(e.g. `qwen2.5:14b`) on capable hardware, and smaller where it must. We cannot
improve the weights; we can only improve the machinery around them. Through
0.2.0 (P0–P8) we built a broad, correct feature set — MCP, skills, loops, code
graph, sub-agents, undo — but it is tuned like a *frontier-model* harness: it
**trusts the model's output** more than a local model earns.

Two concrete gaps (found by reading `agent_loop.py`, not assumed):
1. **No verification.** After the model edits a file, nothing checks it — no
   compile, typecheck, test, or lint. The edit is accepted on faith.
2. **Context is pull-only.** `pack_symbol_context` (F14.3) exists, but the model
   must *decide* to call the graph tools. A weaker model frequently won't, so it
   edits code without seeing the callers or real signatures.

## Decision
Adopt one governing rule for 0.3.0 and beyond:

> **The model proposes; the harness verifies and repairs.** A model mistake that
> reaches the user is a *harness* failure — the harness must prevent it, or
> catch-and-repair it invisibly within a budget.

With a frontier model the harness is a convenience layer; with a local model the
inversion is the whole point: **the harness is where the reliability lives.**

## Failure profile we design against (a 14B, specifically)
A 14B rarely fumbles syntax, so we do **not** over-invest in low-level output
repair. Its real failures are subtler, and each maps to a countermeasure:

| # | Failure | Countermeasure | Phase |
|---|---------|----------------|-------|
| ① | Plausible-but-broken edits (compiles, but wrong / fails tests) | **verify → repair loop** | H1 |
| ② | Edits in the dark (doesn't see callers / signature) | **proactive context injection** | H2 |
| ③ | Can't hold a long plan; loses the thread | **deterministic decompose + per-step verify** | H3 |
| ④ | Context bloat / drift over long transcripts | structure-aware packing over recency (F14.3) | H2/H3 |

## Design principles (apply to every H-phase)
1. **The model proposes; the harness disposes.** No edit is trusted until a
   deterministic check passes.
2. **Catch, don't ship.** Repair invisibly within a budget; only surface a
   failure the harness genuinely can't fix (and then revert via undo).
3. **Right context beats a smarter model.** Push what the model needs; never rely
   on a weak model to pull it.
4. **Shrink every decision.** Fewer degrees of freedom per step ⇒ fewer places to
   be wrong. Decompose and constrain tool schemas.
5. **Measure the lift.** Hold the model constant; prove each change moves the
   pass-rate (H0).
6. **Hold the invariants.** Every countermeasure stays offline and dependency-light
   (ADR-0001/0002), like P0–P8.

## Phase overview (H-series, ordered by leverage × reuse)

| Phase | Name | Leads with | Reuses |
|-------|------|-----------|--------|
| **H1** | Verify → repair loop | auto-check every edit; feed failures back and repair | `before_tool` pattern, loop-driver predicates, undo, code-graph test selection |
| **H2** | Proactive context injection | auto-inject def+callers before an edit | `pack_symbol_context` (F14.3), code-graph retrieval |
| **H3** | Decompose + per-step verify | small verified steps a 14B can hold | `spawn_subagent` (P8), loop-driver, tighter `ToolParam` |
| **H0** | Eval harness (cross-cutting) | baseline + per-change lift, model held constant | new; small; stand up early |

**Sequencing:** H1 is the lead (highest leverage, mostly wiring existing seams).
Stand up **H0 early** — even before H1 lands — so H1's impact is a number, not a
vibe. H2 and H3 follow.

## Consequences
- New engine surface lands in `nerva-agent` (verifier, after_tool hook,
  context-injection, decomposition), with config + CLI in `revenant-cli`
  (ADR-0002 placement).
- The loop gains an `after_tool` hook symmetric to `before_tool`; this is the
  central new seam (see ADR-0012).
- Autonomy (P5) and sub-agents (P8) get materially more reliable, because their
  edits are now verified — the safety story from P2.5/P8 undo is what makes
  auto-repair safe to attempt.

## Progress log
- 2026-07-31 — Accepted. Strategy for 0.3.0; child ADRs 0012–0015 track the work.
