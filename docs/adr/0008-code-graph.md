# ADR-0008 — Code graph: repo-scale reasoning (Phase 7)

- **Status:** Proposed
- **Phase:** P7 (horizon, the deep bet) · **F-slices:** F14.1 indexer, F14.2 retrieval tools, F14.3 structure-aware packing, F14.4 incremental re-index
- **Date proposed:** 2026-07-30 · **Date implemented:** —
- **Depends on:** ADR-0003 (registry, compaction), `agent_ignore` · **Blocks:** —

## Context
Today the agent finds code by **text** (grep/search). Grep finds strings; it
can't answer "what calls this function" or "what breaks if I change this
signature" without guesswork. A **symbol/dependency graph** of the workspace
lets the agent retrieve the *right* files by structure. This is the largest
change and the biggest capability jump — hence it anchors the horizon. Local
graph/vector infra already exists in `nerva-core` (the companion memory used
ChromaDB + a graph layer), so the substrate isn't from scratch.

## Decision
Build a local **symbol/dependency indexer** (tree-sitter / language AST) into a
graph of defs, refs, imports, and call edges; expose **read-only graph
retrieval tools**; make context packing **follow graph edges**; keep the graph
live with **incremental re-index**. Everything stays offline.

Rejected: sending code to a hosted indexing service (violates ADR-0001);
grep-only retrieval (the status quo we're improving on).

## Design detail

### F14.1 — Indexer (`nerva_agent/code_graph/indexer.py`; heavy store in `nerva-core`)
- Parse the repo with **tree-sitter** (per-language grammars; degrade to a
  regex/import-only pass for unsupported languages).
- Node types: `file`, `symbol` (function/class/method), `module`.
- Edge types: `defines`, `references`, `imports`, `calls`.
- Respect `agent_ignore` globs so vendored/generated code isn't indexed.
- Persist to a local store under `.aibot/graph/` (SQLite adjacency + FTS, or the
  existing nerva-core graph layer). Store is offline and rebuildable.

### F14.2 — Retrieval tools (`nerva_agent/code_graph/tools.py`, registered read-only)
- `defn_of(symbol) -> file:line + snippet`.
- `who_calls(symbol) -> list of call sites`.
- `neighbors(path) -> imported-by / imports / same-module symbols`.
- `impact_of(symbol) -> transitive callers` (bounded depth).
- All `parallel_safe=True`, `mutating=False` — they only read the index.

### F14.3 — Structure-aware context packing (`agent_loop` compaction/selection)
- When editing a symbol, proactively fetch its **definition + immediate callers**
  into context instead of relying on recency alone.
- Compaction prefers keeping graph-adjacent material over merely-recent material
  when both compete for the budget.
- Strictly additive to F5 compaction; falls back to recency if no graph.

### F14.4 — Incremental re-index (`code_graph/watch.py`)
- Watch the tree (reuse P5 `--watch` machinery); on change, re-parse only the
  changed files and patch affected nodes/edges so the graph stays live during a
  session or loop.

## Failure & degradation
- No tree-sitter / unsupported language → import-and-regex fallback index.
- Corrupt/missing index → transparent rebuild; tools degrade to "unknown, use
  search" observations, never crash.
- Large repos → bounded traversal depth + budgeted result sizes.

## Test plan
`tests/test_code_graph_indexer.py` (on a small fixture repo):
- [ ] defs/refs/imports/calls extracted for a known Python fixture.
- [ ] ignore globs exclude vendored dirs.
- [ ] fallback path indexes an unsupported-language file by imports only.
`tests/test_code_graph_tools.py`:
- [ ] `who_calls`, `defn_of`, `neighbors`, `impact_of` return correct nodes.
`tests/test_code_graph_incremental.py`:
- [ ] editing one file updates only its nodes; stale edges removed.

## Acceptance criteria
- [ ] "what calls `dispatch`?" is answered from the graph, not a grep guess.
- [ ] Editing a function pulls its callers into context automatically.
- [ ] Index survives restart and re-indexes incrementally on change.
- [ ] Offline; ignore-aware; tests green; ADR + README updated; F14 Implemented.

## Progress log
- 2026-07-30 — Proposed. Substrate exists in nerva-core (Chroma + graph layer).
