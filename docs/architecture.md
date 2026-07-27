# Architecture

AIBot is split into a **backend API**, a **static UI**, a **local LLM call layer**, a
**layered memory system**, and **pluggable TTS workers** — all offline.

## Processes

```
┌────────────────────┐        HTTP         ┌──────────────────────────┐
│ apps/ui_app.py     │ ─────────────────▶  │ apps/backend_app.py      │
│ static UI  :8765   │                     │ API server :8766         │
│ (serves frontend/) │                     │ subclasses LocalUIHandler│
└────────────────────┘                     │ in apps/web_app.py       │
                                           └────────────┬─────────────┘
                                                        │
                    ┌───────────────────────────────────┼───────────────────────────────┐
                    ▼                                    ▼                                ▼
          core/local_llm_writer.py            core/ memory + context            tts/ workers
          call_model / stream_model           (SQLite + vector recall)          (Chatterbox/Kokoro/…)
                    │                                                                     
                    ▼                                                                     
              Ollama :11434  (or any OpenAI-compatible local server)
```

## The turn pipeline (`apps/web_app.py`)

A chat turn flows through two shared helpers so the blocking and streaming paths never fork:

1. **`_prepare_turn(body)`** — validate → build `ChatConfig` → resolve companion/persona →
   compute `response_shape` (length/register) → **route to the best model per role**
   ([Model Routing](model-routing.md)) → rank memories + fold in semantic recall →
   `assemble_context()` (token-budgeted message list).
2. **`call_model` / `stream_model`** — generate (both consume the same `ctx["config"]`).
3. **`_finalize_turn(ctx, reply)`** — trim to last sentence → persist → learn personal
   memories → kick off rolling summarization on a daemon thread.

## Layered memory

| Tier | Where | Module |
|---|---|---|
| Working context (budgeted recent turns) | in-memory per turn | `core/aibot_context.py` |
| Session summary (rolling) | SQLite conversation row | `core/aibot_summary.py` |
| Long-term structured (personal facts, boundaries) | SQLite | `core/aibot_personal_memory.py` |
| Long-term semantic (recall) | local vector index | `core/aibot_memory.py` |
| Durable conversation history (source of truth) | `.aibot/conversations.sqlite3` | `core/aibot_storage.py` |

## Model routing

Each turn is auto-routed to the best local model for its **role** (`code`, `language`,
`companion`, `summary`) via `core/agent_router.py` and the `model_roles` map in
`profiles.json`. This generalizes the pre-existing pattern where summarization already used
a separate model. Full detail: [Model Routing](model-routing.md).

## Agent harness (Revenant)

A reusable tool-calling loop (`core/agent_loop.py`) drives a local model to take actions:
call → parse action → (approve) → dispatch tool → feed observation back → repeat. One loop
powers **two front-ends** with different tool sets: the [coding CLI](revenant-cli.md)
(`revenant`; fs + shell tools, qwen2.5) and the [companion agent](companion-agent.md)
(`/api/agent`; memory/reminder tools, Stheno). See [ADR 0003](adr/0003-local-agent-harness.md).

## Voice (TTS)

Multiple local neural voice engines live under `tts/` (Chatterbox, Kokoro, Orpheus,
Qwen3-MLX), each a persistent worker, all behind a single `/api/tts` endpoint so the UI and
native apps never care which engine is active. Engine decisions: [ADR 0002](adr/0002-qwen3-tts-local-voice-engine.md).

## Design records

- [ADR 0001 — Offline Local LLM Interface](adr/0001-offline-local-llm-interface.md)
- [ADR 0002 — Qwen3-TTS Local Voice Engine](adr/0002-qwen3-tts-local-voice-engine.md)
- [ADR 0003 — Local Agent Harness](adr/0003-local-agent-harness.md)
- [Companion Harness Plan](companion-harness-plan.md) · [Agent Harness Plan](agent-harness-plan.md)
- [Knowledge Base](knowledge-base.md) — long-form reference.
