# ADR-0007 — Resume & session persistence (Phase 6)

- **Status:** Proposed
- **Phase:** P6 · **F-slices:** F3.1 session store, F3.2 resume subcommand
- **Date proposed:** 2026-07-30 · **Date implemented:** —
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

### F3.1 — Session store (`nerva_core/aibot_storage.py` or a thin `session_store.py`)
- Reuse the existing `ConversationStore`/data-dir conventions. A session record:
  `{id, created_at, updated_at, workspace, model, goal, messages (the
  AgentResult transcript), summary, turns_covered}`.
- `save_session(...) -> id`, `load_session(id) -> record`,
  `list_sessions(workspace?) -> [meta]`.
- Sessions live under `<workspace>/.aibot/` (same place as checkpoints) so they
  travel with the repo and stay offline.

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

## Test plan
`tests/test_session_store.py`:
- [ ] save → load round-trips the full transcript + metadata.
- [ ] list filters by workspace, newest first.
- [ ] corrupt record handled gracefully.
`tests/test_cli.py`:
- [ ] `resume list` renders saved sessions.
- [ ] `resume <id>` re-hydrates history into the loop (fake loop asserts history in).

## Acceptance criteria
- [ ] A run can be saved and resumed in a later invocation with context intact.
- [ ] `resume list` shows workspace sessions; default resumes the latest.
- [ ] Tests green; ADR + README updated; F3 marked Implemented.

## Progress log
- 2026-07-30 — Proposed. Uses the existing `ConversationStore` "Phase 3" hooks.
