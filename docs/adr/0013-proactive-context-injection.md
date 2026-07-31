# ADR-0013 — Proactive context injection (H2)

- **Status:** Implemented
- **Phase:** H2 · **F-slices:** H2.1 pre-edit context, H2.2 error-symbol resolution
- **Date proposed:** 2026-07-31 · **Date implemented:** 2026-07-31
- **Depends on:** ADR-0011 (strategy), ADR-0008 (code graph / `pack_symbol_context`)
- **Relates to:** ADR-0012 (H1 — verified edits benefit from better context)

## Context
ADR-0011 failure ②: the model **edits in the dark** — it changes a function
without seeing its callers or true signature, and breaks things off-screen. We
built the primitive to fix this in F14.3 (`pack_symbol_context` → definition +
immediate callers), but it is **pull-only**: the model must decide to call the
graph tools, and a weaker model frequently won't. The fix is to **push** the
context the model obviously needs instead of hoping it pulls it.

## Decision
Before the model edits a symbol, the harness **automatically injects** that
symbol's definition, signature, and immediate callers into context. On an error
observation (a stack trace or failed verify from H1), the harness auto-resolves
the named symbols to their definitions and surfaces them. Right context beats a
smarter model.

Rejected: leaving retrieval entirely to the model (the status quo we're
improving); dumping the whole graph in (defeats the compaction budget — inject
only the neighborhood of what's being touched).

## Design detail

### H2.1 — Pre-edit context (`agent_loop` + code_graph)
- When the model targets a symbol/file for an edit (detected from the `edit_file`
  / `write_file` args, or from an explicit "about to edit X" signal), the harness
  runs `pack_symbol_context(graph, symbol)` and injects the result as a system/
  observation note **before** the edit tool runs.
- Bounded: definition + up to N immediate callers (config `[context] max_callers`),
  so the injection cost is fixed, not proportional to popularity.
- Strictly additive to F5 compaction and independent of H1 — falls back to
  nothing when the graph has no entry (never worse than today).

### H2.2 — Error-symbol resolution (`agent_loop`)
- On an error observation (tool error, or an H1 verify failure), extract candidate
  symbol/file names (a light regex over the traceback / message) and auto-attach
  their `defn_of` results, so the model gets the right file:line without a
  scavenger hunt across turns.
- Deduplicated and capped per turn so a noisy trace can't flood the window.

### Config (`.revenant.toml`)
```toml
[context]
inject_on_edit = true      # H2.1
resolve_errors = true      # H2.2
max_callers = 5
```

## Failure & degradation
- Graph disabled (`--no-graph`) or symbol unknown → inject nothing; the run is
  identical to today.
- A malformed trace → resolve what parses, skip the rest; never crash.

## Test plan — DONE (44 tests, 2026-07-31)
`tests/test_context_inject.py` (20) + `tests/test_context_hook.py` (13):
- [x] pre-edit injection recovers the target symbol from the edit's `def`/`class`
      line and includes its def + callers, capped at `max_callers`; empty when the
      symbol is unknown.
- [x] error-symbol resolution attaches defns for names in a fake traceback; dedups
      by qualname; caps per turn.
- [x] both no-op when the graph is absent or the feature is disabled; never raise.
- [x] `compose_after_tool_hooks` chains H2 with H1's verify hook; one hook's error
      doesn't suppress the other's contribution.
`tests/test_config.py` (4):
- [x] `context_config` defaults (both flags on), parsing, project-overrides-user,
      malformed-section.

## Acceptance criteria
- [x] Editing a function surfaces its callers automatically, without the model
      calling a tool (verified end-to-end: editing `foo` yields
      `[code-graph context for 'foo'] Definition: … Called by …` in the observation).
- [x] A stack trace auto-resolves its symbols to definitions.
- [x] Additive; disabled/no-graph paths unchanged (`after_tool=None`, identical to
      pre-H2); tests green (393 → 428 standalone); ADRs + README updated.

## Implementation notes (what actually shipped)
- **`nerva_agent/context_inject.py`** (pure): `pre_edit_context` (recover symbol
  from the edit → `pack_symbol_context`), `extract_candidate_symbols` +
  `resolve_error_symbols` (regex over traceback/quoted names → `defn_of` lines).
- **`revenant_cli/context_hook.py`**: `make_context_hook` returns an
  `after_tool`-shaped callable; `compose_after_tool_hooks` chains it with H1's
  verify hook (per-hook error isolation). `config.context_config` reads `[context]`.
- **Deviation from the ADR's literal wording — wired via `after_tool`, not
  `before_tool`:** the loop **discards `before_tool`'s return value** (agent_loop
  line ~338) and it fires *after* the model has already chosen the edit, so it
  can't carry context in. Wiring through `after_tool` (same seam as H1) surfaces
  the def+callers in the **next** observation — which meets the ADR's actual goal
  (surface callers without the model calling a graph tool) with **zero
  `agent_loop.py` changes**. I verified this against the loop code and accept it.
  Literal pre-dispatch injection would need a new loop hook + an extra model
  round-trip — a future option if wanted, not worth it now.
- **Agent-built (H2); verified independently on the worktree: 53 phase tests + full
  suite 428 green.**

## Progress log
- 2026-07-31 — Proposed. Turns the pull-only `pack_symbol_context` (F14.3) into a
  push, closing failure ②.
- 2026-07-31 — **Implemented** (agent-built, verified). Wired via `after_tool`
  (deviation documented above); composes with H1. 44 tests. Closes failure ②.
