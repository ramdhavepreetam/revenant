# ADR-0013 — Proactive context injection (H2)

- **Status:** Proposed
- **Phase:** H2 · **F-slices:** H2.1 pre-edit context, H2.2 error-symbol resolution
- **Date proposed:** 2026-07-31 · **Date implemented:** —
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

## Test plan
`tests/test_context_injection.py`:
- [ ] pre-edit injection includes the target symbol's def + callers, capped at
      `max_callers`; empty when the symbol is unknown.
- [ ] error-symbol resolution attaches `defn_of` for names in a fake traceback;
      dedups; caps per turn.
- [ ] both are no-ops when the graph is absent or the feature is disabled.

## Acceptance criteria
- [ ] Editing a function surfaces its callers automatically, without the model
      calling a tool (verified: edit `X` → context shows `who_calls(X)`).
- [ ] A stack trace auto-resolves its symbols to definitions.
- [ ] Additive; disabled/no-graph paths unchanged; tests green; ADRs + README updated.

## Progress log
- 2026-07-31 — Proposed. Turns the pull-only `pack_symbol_context` (F14.3) into a
  push, closing failure ②.
