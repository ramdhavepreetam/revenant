# ADR-0022 — Cross-session memory for the coding agent (M-series)

- **Status:** Implemented — M0–M4 all done
- **Phase:** M-series (0.8.0) · **M-slices:** M0 the store · M1 remember/recall
  tools · M2 auto-recall into the preamble · M3 gated end-of-run suggestions ·
  M4 `memory` subcommand + `/memory`
- **Date proposed:** 2026-08-03 · **Date implemented:** —
- **Depends on:** ADR-0001 (offline / zero-dep — the store is stdlib SQLite),
  ADR-0002 (placement: engine store in `nerva-agent`, wiring in `revenant-cli`),
  ADR-0011 (harness-carries-the-model — capture is *gated*, not automatic),
  ADR-0013 (context injection — auto-recall folds into the preamble the same way),
  ADR-0007 (session store — same `.aibot/` persistence pattern) · **Relates to:**
  ADR-0008 (code-graph cache — same load/degrade discipline)

## Context
The coding agent has **no persistent memory**. Every `revenant` run starts cold
and re-learns the project's conventions, layout, and past pitfalls — "this project
uses pytest", "the API lives in `packages/api/`", "editing `config.py` directly
breaks the loader — use `write_scalar`". This is the biggest capability gap found
in the codebase survey.

A memory store *does* exist — `NervaPackMemory` (`nerva-core/aibot_memory.py`) —
but it (a) serves only the **companion** front-end, and (b) depends on
`chromadb` + `nervapack`, which pull **~150–200 MB** (onnxruntime 70MB, numpy
33MB, grpcio, kubernetes, uvicorn, tokenizers…) and are **not** installed with
`revenant-cli`. Reusing it would make memory the project's first heavy *required*
dependency — directly contradicting the founding invariant of **zero required
deps, fully offline, lightweight** (ADR-0001/0002; the code graph even chose
stdlib `ast` over tree-sitter for exactly this reason, ADR-0008).

## Decision
Build a **lightweight, stdlib-native memory store** and wire it into the coding
loop, with **hybrid, gated capture**. Two governing choices, both analyzed:

### Backend: stdlib SQLite FTS5 (not chromadb)
- Persist to `<ws>/.aibot/memory.db` using Python's **built-in** `sqlite3` +
  the **FTS5** full-text extension (verified present on the bundled SQLite 3.39.4
  — a `CREATE VIRTUAL TABLE … USING fts5` works with **zero new dependency**).
- Mirrors how sessions (`session_store.py`), checkpoints, and the code-graph cache
  already persist under `.aibot/`. Per-project → memory travels with the repo.
- **Recall is keyword/FTS.** For coding memory — dense with file paths, symbol
  names, tool names, "pytest", "SQLite" — keyword match is strong; the case where
  semantic wins (pure synonyms) is comparatively rare here. Semantic recall can be
  added later as an **optional** `revenant-cli[semantic]` extra (local embeddings),
  so this is the strict-superset path: lightweight by default, semantic if opted in.
- **Rejected — reuse `NervaPackMemory`:** semantic out of the box, but a
  150–200 MB required dep (incl. a web server and kubernetes client) in a
  zero-dep offline CLI. Not worth it for storing text notes.

### Capture: hybrid (explicit tool + gated suggestions), never aggressive auto
- **Explicit:** the agent has a `remember` tool it calls deliberately mid-task.
- **Gated suggestions:** at run-end the model *proposes* ≤3 durable facts; each is
  **confirmed** (interactive prompt / TUI affordance) before it persists. Nothing
  is written unattended.
- **Rejected — aggressive auto-capture:** a small local model auto-deciding
  "this is a durable fact" and writing it to disk — where it's injected into
  *every* future run — is a compounding-error machine (one wrong fact reinforces
  itself). It violates ADR-0011's core rule (*the model proposes; the harness
  verifies*). The gate is the same principle as verify→repair and the approval gate.

## Design detail (M-slices)

### M0 — the store (`nerva-agent/memory_store.py`)
`MemoryStore(db_path)` over stdlib `sqlite3` + FTS5. Rows: `id, kind
(fact|decision|outcome|note), content, created_at, source`. API: `remember(content,
kind, source)`, `recall(query, limit=5)` (FTS5 `MATCH`, newest-first tiebreak),
`list_all()`, `forget(id)`, `clear()`, `count()`. A bad FTS5 query is sanitized and
falls back to `LIKE`; a DB that can't open degrades to empty (never raises) — same
discipline as the graph cache. Pure stdlib, model-free.

### M1 — agent tools (`nerva-agent/memory_tools.py`)
`build_memory_tools(store) -> [Tool]`: `remember(note, kind="fact")` and
`recall(query)`. **`mutating=False`** (like the graph tools) — writing to the
agent's *own* memory is not a workspace mutation, so no approval gate. Wired into
`_build_agent` in every mode (incl. read-only).

### M2 — auto-recall at run start (`cli.py`)
Before a run, `store.recall(goal, limit=N)`; fold hits into the system preamble at
the `compose_preamble` seam (`cli.py:569`) under "Project memory (recalled):" —
exactly like the skill index folds in. Bounded (top-N, char-capped) so it never
blows the context budget. Empty/absent memory → preamble unchanged (byte-parity).

### M3 — gated end-of-run suggestions (`cli.py`)
After `loop.run(goal)` returns, one constrained model call (reusing the summarizer
/ `config_for_role` config) proposes ≤3 durable facts. Each is shown and confirmed
before `store.remember`. Interactive: `[y/N]` per candidate. Non-interactive or
`--no-memory-suggest`: skipped (never writes unattended). Runs in the CLI after the
loop returns — `nerva-agent` stays model-policy-free.

### M4 — CLI surface (`cli.py`, TUI)
`revenant memory list|show <id>|forget <id>|clear` (mirrors `resume`), plus a TUI
`/memory` slash command. Lets the user audit/prune what the agent remembers.

### Config surface
`[memory]` section (mirrors `[verify]`): `enabled` (default true), `max_recall`
(5), `suggest` (true). Flags `--no-memory`, `--no-memory-suggest`.

### Failure & degradation
Unopenable/corrupt DB → empty store, run proceeds (like a missing graph cache).
FTS5 syntax error → LIKE fallback. Empty memory → zero preamble change. All
additive; base `pip install revenant-cli` still pulls **nothing**.

## Test plan (model-free / offline; CI bare `pytest`)
- **M0** `test_memory_store.py`: remember→recall by keyword; newest-first;
  forget/clear/count; an FTS5 special-char query doesn't crash (LIKE fallback);
  a corrupt/locked DB degrades to empty.
- **M1** `test_memory_tools.py`: `remember`/`recall` dispatch through a registry;
  `mutating=False`; recall returns stored notes.
- **M2** `test_cli.py`: a matching memory is injected into the preamble; empty
  memory leaves it byte-identical; bounded to `max_recall`.
- **M3** `test_cli.py`: fake model proposes facts; a "y" persists, "n" doesn't;
  non-interactive / `--no-memory-suggest` writes nothing.
- **M4** `test_cli.py` + `test_tui_app.py`: `memory list/forget`; `/memory` lists.
- **Dep check**: no new dependency declared; base install pulls nothing.

## Acceptance criteria
- [ ] A `MemoryStore` (stdlib SQLite/FTS5) remembers + recalls project facts under
      `.aibot/memory.db`; degrades cleanly on a bad DB.
- [ ] The agent has `remember`/`recall` tools (no approval gate; available in every
      mode).
- [ ] Relevant memories auto-recall into the preamble at run start; empty memory =
      byte-identical preamble.
- [ ] End-of-run suggestions are **gated** — nothing persists without a confirm;
      non-interactive never auto-writes.
- [ ] `revenant memory list/show/forget/clear` + TUI `/memory` work.
- [ ] **No new dependency**; base `pip install revenant-cli` unchanged. Suite green.
- [ ] ADR-0022 + README + CHANGELOG updated.

## Open questions
- **Recall query = the goal, or a distilled key-phrase?** Start with the raw goal
  (simple, deterministic); a distilled query is a later refinement.
- **Dedup**: skip storing a near-duplicate of an existing memory? Start with an
  exact-content dedup; fuzzier dedup later if noise appears.
- **Scope**: per-project only (in `.aibot/`) for v1. A user-global memory layer is
  a possible later addition.

## Progress log
- 2026-08-03 — Proposed + Accepted. Strategy written before code (durable record
  first, per the series workflow). Backend + capture decisions analyzed with the
  user against real dependency weights (chromadb ~150–200MB vs stdlib FTS5, 0MB)
  and ADR-0011 (gated capture over aggressive auto). Implementation begins with M0.
- 2026-08-03 — **M0–M4 Implemented — the M-series is complete.**
  - **M0** `memory_store.py`: `MemoryStore` over stdlib `sqlite3` + FTS5 at
    `.aibot/memory.db`. remember/recall/list_all/forget/clear/count; a safe FTS5
    query builder (quotes tokens so paths/`foo.bar` can't be read as operators) +
    LIKE fallback; exact-content dedup; an unopenable DB → null store (never
    raises). 12 tests.
  - **M1** `memory_tools.py`: `remember`/`recall` Tools, `mutating=False` (own
    memory, not the workspace → no approval gate). Wired into `_build_agent`
    (every mode); `--no-memory` disables; store stashed on `loop._memory`. 7 tests.
  - **M2** auto-recall: `_recall_block`/`_apply_memory_recall` fold goal-relevant
    memories into the preamble (cmd_run + REPL turn-1 + TUI worker turn-1);
    byte-identical when empty/absent. `memory_config` reader. 8 tests.
  - **M3** gated suggestions: `_maybe_suggest_memories` — one constrained model
    call proposes ≤3 facts, each **confirmed** before it persists; skips when
    off/non-final/non-TTY. Nothing auto-writes. 5 tests.
  - **M4** surface: `revenant memory list/show/forget/clear` + TUI `/memory`.
    10 tests.
  - **No new dependency** (verified: `nerva-core` deps still `[]`); pure stdlib.
    Suite **684 → 722**. Verified end-to-end: a fact remembered in run 1 persists
    to disk and auto-recalls into a fresh run 2's system prompt. **Ships in 0.8.0.**
