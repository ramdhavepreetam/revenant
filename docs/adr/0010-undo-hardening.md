# ADR-0010 — Undo checkpointing & test debt (bridge to P5)

- **Status:** Implemented
- **Phase:** P2.5 (bridge) · **F-slices:** F8 (undo), F9 (git-undo → deferred to P8)
- **Date proposed:** 2026-07-30 · **Date implemented:** 2026-07-30
- **Depends on:** ADR-0003 (`before_tool` hook) · **Blocks:** ADR-0006 (Loops)

## Context
A coding agent is only trustworthy to edit files if its changes can be reverted.
F8 adds file checkpointing: before any mutating tool runs, snapshot the target
file; `revenant undo` restores it. This is also the **safety floor for
autonomous loops (P5)** — unattended editing is only acceptable once undo is
proven. The wiring landed in this session; **unit tests are still missing** — the
one coverage gap in an otherwise well-tested engine.

## Decision
Snapshot-based, per-file undo, persisted per workspace so `revenant undo` works
as a **separate invocation after the session ends**. Shell side-effects
(`run_bash`) are explicitly out of scope for snapshots and handed to git-native
undo in P8.

## Design detail — as implemented
**New file `revenant_cli/checkpoint.py`:**
- `_Snapshot{rel_path, existed, content, tool, ts}` — pre-edit state.
- `Checkpointer{workspace, store_path, snapshots}`:
  - `snapshot(tool, args)` — the `before_tool` hook. Only snapshots tools in
    `_SNAPSHOTTED_TOOLS = {"write_file": "path", "edit_file": "path"}`; records
    `existed=False` for new files so undo can delete them. No-op for `run_bash`.
  - `undo_last()` / `undo_all()` — pop and restore (newest first).
  - `_restore(snap)` — deletes newly-created files; rewrites prior content
    otherwise.
  - `_persist()` / `load(workspace, store_path)` — JSON mirror to disk; both
    best-effort (never raise).

**`agent_loop.py`:** added `BeforeToolHook` type + `before_tool` param; the loop
calls it after the approval gate, only when `tool.mutating`, wrapped so a
checkpoint failure never aborts the tool.

**`cli.py`:**
- `_checkpoint_store(workspace) → workspace/.aibot/checkpoints.json`.
- `_build_agent` constructs a `Checkpointer` in write mode and passes
  `before_tool=checkpointer.snapshot`.
- `undo` subcommand (`--workspace`, `--all`, `--no-color`) + `cmd_undo` +
  dispatch in `main`. Reconstructs via `Checkpointer.load`; empty case prints a
  clean message and exits 0.

**Verified manually (this session):** cross-process undo restores an edited file
and deletes a created file; empty-undo degrades cleanly; full suite 194 green.

## Test plan (the debt — CLEARED 2026-07-30)
`tests/test_checkpoint.py` (16 tests):
- [x] snapshot of an existing file captures prior content; `undo_last` restores.
- [x] snapshot of a non-existent path records `existed=False`; undo deletes it.
- [x] `run_bash` (and unknown tools) are not snapshotted.
- [x] missing / empty / non-str path arg is ignored.
- [x] `undo_all` reverts newest-first; returns descriptions; empty cases.
- [x] persist → `load` round-trips across a fresh instance (process boundary).
- [x] `load` of missing / corrupt store returns empty; persist updates after undo.
- [x] unreadable file / OSError in `snapshot` records nothing (no bogus restore).
- [x] `_persist` OSError is swallowed; in-memory undo still works.
- [x] restoring an already-removed "new" file is a clean no-op.

`tests/test_cli.py` additions (7 tests):
- [x] undo parser flags; `_checkpoint_store` path under `.aibot/`.
- [x] `cmd_undo` with no store → "nothing to undo", exit 0.
- [x] `cmd_undo` (last) restores from a seeded store.
- [x] `cmd_undo --all` after seeded snapshots reverts and reports count.
- [x] write-mode `_build_agent` wires a non-None `before_tool`; read-only doesn't.

## Acceptance criteria
- [x] All test-plan cases pass (23 new tests; suite 194 → 217 green).
- [x] `.aibot/` confirmed git-ignored so checkpoints aren't accidentally
      committed (`git check-ignore .aibot/checkpoints.json` ✓, 2026-07-30).
- [x] ADR + README progress logs updated; F8 marked Implemented.

## Progress log
- 2026-07-30 — Wiring completed and verified end-to-end; tests still outstanding.
  Recorded as the gate for P5.
- 2026-07-30 — Test debt cleared: added `tests/test_checkpoint.py` (16) and 7
  undo cases in `tests/test_cli.py`. Suite 194 → 217 green. **Status →
  Implemented.** P5 (Loops) safety floor is now in place.
