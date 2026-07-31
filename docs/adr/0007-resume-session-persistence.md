# ADR-0007 — Resume & session persistence (Phase 6)

- **Status:** Implemented
- **Phase:** P6 · **F-slices:** F3.1 session store, F3.2 resume subcommand
- **Date proposed:** 2026-07-30 · **Date implemented:** 2026-07-30
- **Depends on:** ADR-0003 (`AgentResult.messages`), `nerva_core.aibot_storage` · **Blocks:** ADR-0006 (run journal)

## Context
Compaction (F5) keeps a *live* run inside the model's window, but nothing
persists a run so it can be **picked up later**. That's the missing half of the
story. The `cli.py` `resume` subcommand is a stub, and `ConversationStore`
already carries "Phase 3 support: rolling session summary + how many turns it
covers." Small, high-leverage, and a prerequisite for the P5 run journal.

## Decision
Persist a run's full transcript + metadata to the data dir, and fill the
`resume` subcommand to re-hydrate it into a new loop.

## Design detail

### F3.1 — Session store (`revenant_cli/session_store.py`)
**Decision (2026-07-30): one JSON file per session under
`<ws>/.aibot/sessions/<id>.json`, not the shared `ConversationStore` SQLite DB.**
Rationale: ADR-0007 requires sessions to be *per-workspace* ("travel with the
repo"), but `ConversationStore` is a single shared companion DB — those conflict.
Per-workspace JSON mirrors the pattern `checkpoint.py` already established
(`.aibot/checkpoints.json`), keeps sessions offline and repo-local, and needs no
schema migration. `ConversationStore` stays the companion's store.

- Session record: `{id, created_at, updated_at, workspace, model, goal,
  messages (the AgentResult transcript), summary, turns_covered}`.
- `save_session(workspace, ...) -> id`, `load_session(workspace, id) -> record`,
  `list_sessions(workspace) -> [meta]` (metadata only, no transcripts, newest
  first). `id` is a short timestamp-based token so files sort chronologically.
- Best-effort persistence and tolerant loading (skip corrupt/unknown), mirroring
  `checkpoint.py` and `config.py`.

### F3.2 — `revenant resume` (`cli.py`)
Replace the stub:
- `revenant resume list` — recent sessions for the workspace (id, goal, when).
- `revenant resume [<id>]` — load the transcript as `history` and start a REPL
  (or continue a one-shot with an appended new goal). Defaults to the most
  recent session when no id is given.
- `run`/`chat` gain `--save`/auto-save so sessions exist to resume.

## Failure & degradation
- Corrupt/missing session file → clear error, list what's available.
- Schema drift → tolerate unknown fields; never crash resume.

## Test plan — DONE (18 tests, 2026-07-30)
`tests/test_session_store.py` (11):
- [x] save → load round-trips the full transcript + metadata.
- [x] persisted under `<ws>/.aibot/sessions/<id>.json`.
- [x] save with an existing id updates in place (keeps created_at, one file).
- [x] list newest-updated first; metadata carries goal+count, not the transcript.
- [x] `latest_session_id`; missing/empty cases.
- [x] corrupt file skipped in list; corrupt load → None; missing/extra keys
      tolerated; save OSError → None (best-effort).
`tests/test_cli.py` (7):
- [x] `resume` parser (optional id); `chat` auto-saves a session.
- [x] `resume list` renders sessions; empty message; unknown-id error; no-session
      default; **`resume <id>` re-hydrates history into the loop**.

## Acceptance criteria
- [x] A run can be saved and resumed in a later invocation with context intact
      (verified end-to-end: chat→save→`resume <id>` threads the transcript back).
- [x] `resume list` shows workspace sessions; bare `resume` resumes the latest.
- [x] Tests green (272 → 290); ADR + README updated; F3 marked Implemented.

## Implementation notes (what actually shipped)
- **Standalone `revenant_cli/session_store.py`**, per-workspace JSON — *not* the
  shared `ConversationStore` SQLite DB (see the decision box in Design detail:
  sessions must be repo-local, which a shared DB can't do). Mirrors the
  `checkpoint.py` per-workspace best-effort pattern.
- `cmd_chat` gained `initial_history` + auto-save after every turn (stable id for
  the REPL's life); `cmd_resume` handles `list` / `<id>` / latest and re-enters
  `cmd_chat` with the re-hydrated transcript.
- **Deviation:** one-shot `run` does not auto-save (multi-turn `chat` is where
  resumable context accrues); a `--save`/`run`-resume path can be added later.

## Progress log
- 2026-07-30 — Proposed. Uses the existing `ConversationStore` "Phase 3" hooks.
- 2026-07-30 — **Implemented.** F3.1 session store + F3.2 resume (18 tests, suite
  272 → 290). Chose per-workspace JSON over the shared DB. Verified end-to-end.
  Status → Implemented. **This unblocks the P5 Loops run journal (F13.4).**
  Next phase: P5 Loops (ADR-0006).
