# ADR-0008 — Code graph: repo-scale reasoning (Phase 7)

- **Status:** Implemented (F14.3 packing + F14.4 re-index deferred)
- **Phase:** P7 (horizon, the deep bet) · **F-slices:** F14.1 indexer, F14.2 retrieval tools, F14.3 structure-aware packing, F14.4 incremental re-index
- **Date proposed:** 2026-07-30 · **Date implemented:** 2026-07-30
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

### F14.1 — Indexer (`nerva_agent/code_graph/indexer.py`)
**Decision (2026-07-30): stdlib `ast`, Python-first — NOT tree-sitter.**
tree-sitter is not installed and adding it (plus per-language grammar wheels)
breaks the zero-dep offline invariant (ADR-0001/0002), the same tension resolved
for YAML in ADR-0005. Python's stdlib `ast` gives a fully accurate parse for
Python with zero dependencies; other languages get the ADR's documented
regex/import-only fallback. tree-sitter can be added later as an *optional*
backend behind the same indexer interface without changing the retrieval tools.

- Parse Python files with `ast`; extract exact defs, imports, and call sites.
  Non-Python files get a regex import/def pass (best-effort, may be empty).
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

## Test plan — DONE (23 tests, 2026-07-30)
`tests/test_code_graph_indexer.py` (11):
- [x] defs/imports/calls extracted for a Python fixture; kinds (class/method/function).
- [x] `callers_of` / `importers_of` / `resolve` (bare + qualified).
- [x] ignore globs exclude vendored dirs.
- [x] regex fallback indexes a non-Python file (imports + defs).
- [x] syntax error recorded, not raised; non-indexable files skipped; stats.
`tests/test_code_graph_tools.py` (10):
- [x] `defn_of`, `who_calls`, `neighbors`, `impact_of` return correct nodes;
      read-only flags; unknown-symbol/uncalled/leaf degrade paths.
`tests/test_cli.py` (2):
- [x] graph tools registered in read-only mode; `--no-graph` opts out.

## Acceptance criteria
- [x] "what calls `dispatch`?" is answered from the graph, not a grep guess
      (verified on THIS repo: `who_calls('dispatch')` → `AgentLoop.run`; 148
      symbols / 18 files / 0 parse errors).
- [x] Offline (stdlib `ast`, zero deps); ignore-aware; tests green (309 → 332);
      ADR + README updated; F14.1/F14.2 marked Implemented.
- [ ] *(F14.3)* Editing a function pulls its callers into context automatically — **deferred**.
- [ ] *(F14.4)* Index re-indexes incrementally on change — **deferred**.

## Implementation notes (what actually shipped)
- **Parser:** stdlib `ast`, Python-first (decision box above), regex fallback for
  JS/TS/Go/Rust/Java/Ruby. No tree-sitter, no new dependency.
- **`code_graph/indexer.py`:** `build_index(root)` → `CodeGraph` of `FileNode`s
  and `Symbol`s with `defines`/`imports`/`calls`; `resolve` / `callers_of` /
  `importers_of` lookups. Respects `agent_ignore`; per-file size cap; never
  raises (parse errors recorded).
- **`code_graph/tools.py`:** `defn_of`, `who_calls`, `neighbors`, `impact_of` as
  read-only registry Tools (bounded result sizes + impact depth).
- **CLI:** `_build_agent` indexes the workspace and adds the tools in **every**
  mode (read-only, so no approval); `--no-graph` opts out on large repos;
  indexing failure degrades to a warning, never blocks a run.
- **Deferred:** F14.3 structure-aware context packing (touches loop compaction)
  and F14.4 incremental re-index (pairs with the deferred P5 `--watch`). The
  index is rebuilt per invocation; not yet persisted to `.aibot/graph/` — a fast
  in-memory build (≈0.03s on this repo) made persistence unnecessary for v1.

## Progress log
- 2026-07-30 — Proposed. Substrate exists in nerva-core (Chroma + graph layer).
- 2026-07-30 — **Implemented** (F14.1 + F14.2 + CLI wiring; F14.3/F14.4 deferred).
  Chose stdlib `ast` over tree-sitter. 23 tests, suite 309 → 332. Verified on
  this repo. Next: P8 (ADR-0009), or the deferred F14.3/F14.4 + P5 triggers.
