# ADR-0020 — W-series Phase B: deeper capability, safe-first (W3/W4)

- **Status:** Accepted — implementation starting with W3
- **Phase:** W-series (0.6.0) Phase B · **W-slices:** W3 `loop --every` + persisted/
  incremental code-graph cache · W4a `edit_file replace_all` + single-file
  graph-guided rename · W4b relax `ToolParam` to one array-of-objects param
  (engine slice; gates W4c) · W4c multi-file atomic `apply_edits`
- **Date proposed:** 2026-08-01 · **Date implemented:** —
- **Depends on:** ADR-0018 (W-series strategy), ADR-0019 (Phase A — W0 rename
  tasks + edit-precision metric score W4), ADR-0008 (code graph — W3 persists it,
  W4 acts on it), ADR-0006 (loops — W3 `--every` parallels `--watch`), ADR-0009
  (git-undo — W4c reverts atomically), ADR-0012 (H1 verify — W4c verifies)
  · **Relates to:** ADR-0001 (offline), ADR-0002 (placement: engine in
  `nerva-agent`, CLI in `revenant-cli`)

## Context
Phase A made the agent live to watch and measurable. Phase B makes it **deeper in
what it can safely do**, ordered safe-first so the highest-risk slice (multi-file
atomic rename) lands last with the schema gate paid down first. Verified in code:

- **`loop --watch` shipped; `--every` missing.** `_cmd_loop_watch` (`cli.py:769-811`)
  mtime-polls via `_tree_signature` (`cli.py:748-766`) and re-runs `_cmd_loop_once`.
  There is no time-interval trigger.
- **The code graph is rebuilt from scratch every run.** `build_index` (`indexer.py:
  251-272`) walks the whole tree into a fresh in-memory `CodeGraph` (`cli.py:472`).
  The dataclasses (`Symbol`/`FileNode`/`CodeGraph`, `indexer.py:39-98`) are
  JSON-friendly (`root` is a `Path` needing str-conversion). Incremental primitives
  `reindex_file`/`remove_file` (`indexer.py:110-138`) exist and are tested but have
  **no caller and no persistence**.
- **`edit_file` replaces exactly one occurrence.** `_edit_file` (`agent_edit_tools.
  py:33-54`) errors on 0 or >1 matches; there is no `replace_all`, no multi-file
  edit. The code graph (`code_graph/tools.py`) knows every call site but is
  read-only — disconnected from the edit tools.
- **`ToolParam` is scalar-only by design.** `type` is a JSON-schema scalar; nested
  objects are "intentionally out of scope for v1" (`agent_tools.py:35-37`).
  `native_schema` renders `{"type": p.type}` (`agent_tools.py:88`). A multi-file
  `apply_edits` wants a **list of `{path, old, new}` objects** the schema can't
  express → this is the gate.

## Decision
Ship W3 (two low-risk wins) → W4a (scalar-safe capability) → W4b (the schema
relaxation as its own tested engine slice) → W4c (the multi-file tool that
consumes it). Each mutating tool stays approval-gated + undo-covered + verify-checked.

### W3 — `loop --every` + persisted/incremental code-graph cache
- **`--every <interval>`.** A new arg parallel to `--watch` (`cli.py:~264`) + a
  `_cmd_loop_every` branch in `cmd_loop` that re-runs `_cmd_loop_once` on a fixed
  time interval within the existing budget, reusing the same injectable tick source
  as `_cmd_loop_watch`. No `loop_driver.py` change.
- **Graph cache.** Add `CodeGraph.to_dict`/`from_dict` (JSON-friendly; `root` ↔ str)
  and a small cache module storing it under `<ws>/.aibot/code_graph.json` (mirror
  `session_store.py`/`checkpoint.py`). At `cli.py:472`, load-if-fresh instead of
  always `build_index`: an mtime **staleness check** (modeled on `_tree_signature`)
  reindexes only changed files via `reindex_file` and drops deleted ones via
  `remove_file`; a corrupt/missing cache falls back to a full `build_index`
  (degrade gracefully). Optionally wire `reindex_file` into the loop's `after_tool`
  hook (`agent_loop.py:400-406`) so edits keep the graph live mid-run.

### W4a — `edit_file replace_all` + single-file graph-guided rename
- **`replace_all`.** Add an optional scalar `all: boolean` param to `edit_file`
  (`agent_edit_tools.py:33-54,74-85`): when true, replace **every** occurrence in
  the file instead of erroring on >1; default false = today's exactly-one behavior
  (byte-identical). Still errors on 0 matches. No new param shape.
- **Single-file rename.** A `rename_symbol(path, old, new)` helper (or `edit_file`
  usage) that uses the graph's read-only call-site knowledge (`code_graph/tools.py`)
  to confirm the symbol, but edits **within one file per call**. Scalar-safe.

### W4b — relax `ToolParam` to one array-of-objects param (the gate)
- Extend `ToolParam` (`agent_tools.py:31-43`) with an optional `items` field
  describing **one** array-of-objects shape (a list of scalar-keyed objects, e.g.
  `[{path, old, new}]`). Render it in `native_schema` (`agent_tools.py:85`) as
  `{"type": "array", "items": {...}}` and in `doc_line` for the prompt path;
  `parse_action`/`validate_args` accept a list value. Exactly one new shape —
  arbitrary nesting stays out of scope. Scalar tools render byte-identically.

### W4c — multi-file atomic `apply_edits`
- A mutating `apply_edits(edits=[{path, old, new}, …])` tool that applies a
  graph-computed project-wide rename **atomically**: snapshot (via `before_tool`),
  apply every edit, verify (via `after_tool`), and **revert all on any failure**
  (all-or-nothing). Consumes the W4b array param. Routes through the sacred
  approval gate. Scored by the W0 rename tasks + edit-precision metric.

### Failure & degradation
- Corrupt/absent graph cache → full `build_index` (never crash). `--every` honors
  the loop budget (never runs unbounded, ADR-0006). `replace_all=false` is the
  default so `edit_file`'s contract is unchanged. `apply_edits` mid-set failure
  reverts the whole set; approval decline blocks it entirely.

## Test plan (model-free / offline; CI bare `pytest`)
- **W3** `--every`: fake agent + monkeypatched clock → N ticks at the interval,
  stops on budget. Cache: build → `to_dict`/`from_dict` round-trips to an equal
  graph; touch one file → staleness reindexes exactly that file (assert via
  `stats()`); delete a file → `remove_file` path; corrupt cache → full rebuild.
- **W4a** `edit_file(all=True)` replaces N occurrences; `all=False` byte-identical
  to today (still errors on >1 without the flag); single-file rename rewrites every
  in-file call site; approval + undo still fire.
- **W4b** a tool declaring the array param renders a correct native JSON schema
  (`type: array, items: …`) and a correct prompt doc line; `parse_action`/
  `validate_args` round-trip a list-of-objects arg; existing scalar tools render
  byte-identically (no regression).
- **W4c** a 3-file edit set applies fully; a mid-set failure reverts **all** (assert
  against the checkpointer); approval decline blocks the whole set; the W0
  `rename_across_package`/`rename_class_across_modules`/`rename_with_shadow` tasks
  pass under this tool.

## Acceptance criteria
- [x] `revenant loop --every <interval>` re-runs the goal on a fixed interval within
      the budget (parallel to `--watch`). **(W3.)**
- [x] The code graph persists under `.aibot/`, loads incrementally (only changed
      files reindexed), and falls back to a full rebuild on a bad cache. **(W3 —
      verified end-to-end: reindex-changed, drop-deleted, corrupt→rebuild.)**
- [ ] `edit_file` gains `replace_all` (default off = byte-identical); a single-file
      graph-guided rename rewrites every in-file call site.
- [ ] `ToolParam` can express one array-of-objects param; scalar tools unchanged.
- [ ] `apply_edits` performs an atomic multi-file rename (all-or-nothing revert),
      approval-gated + undo-covered + verify-checked; the W0 rename tasks pass.
- [ ] Suite green (bare `pytest`); ADR-0020 + README updated per slice.

## Open questions
- **Rename tool shape:** expose W4a's single-file rename as a distinct
  `rename_symbol` tool, or as `edit_file(all=True)` guided by the graph? Lean
  `edit_file(all=True)` first (smallest surface), add a dedicated rename in W4c if
  the atomic multi-file tool wants one entry point.
- **`apply_edits` matching:** exact-string `{old,new}` per file (like `edit_file`),
  or graph-computed spans? Start exact-string per file (composes with `replace_all`
  semantics, deterministic, testable); graph computes the edit *set*.

## Progress log
- 2026-08-01 — Proposed + Accepted. Phase-B spec written before code (per ADR-0018
  / the series workflow). Seams re-verified in code (`--watch`/`_tree_signature`,
  `CodeGraph` dataclasses + `reindex_file`/`remove_file`, `edit_file` single-match,
  `ToolParam` scalar-only + `native_schema`). Implementation begins with W3.
- 2026-08-01 — **W3 Implemented** — `loop --every` + persisted/incremental graph.
  - **`--every <interval>`.** New arg + `_cmd_loop_every` branch in `cmd_loop`
    (`cli.py`), parallel to `--watch`: sleeps the interval, re-runs `_cmd_loop_once`,
    repeats until Ctrl-C, within the existing per-iteration budget/journal/undo.
    Injectable tick source for tests.
  - **Graph cache.** `CodeGraph.to_dict`/`from_dict` (JSON-friendly; rebuilds the
    name index on load) + `index_signature` (mtime map of indexable files) +
    `load_or_build_index(root, cache_path)` in `indexer.py`: loads the cache and
    reindexes only changed files (via `reindex_file`) / drops deleted
    (`remove_file`); missing/corrupt/version-mismatch cache → full `build_index`
    (never raises). Wired at the `_build_agent` graph seam (`cli.py:472`) to load
    from `<ws>/.aibot/code_graph.json`; `--no-graph-cache` forces a fresh build.
  - **Tests.** `test_code_graph_indexer.py` +7 (round-trip, version-mismatch,
    cache-create, reindex-only-changed, drop-deleted, corrupt→rebuild, signature);
    `test_cli.py` +3 (`--every` parse, re-run-each-interval, dispatch). Suite
    **610 → 620**. Verified end-to-end: cache lifecycle (create → reload →
    incremental reindex → drop-deleted → corrupt-survives). **Next: W4a.**
