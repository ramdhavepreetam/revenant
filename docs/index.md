# AIBot

**AIBot** is an offline-first, on-premise local LLM system: a companion, a long-form
writing tool, and (in progress) an agentic harness — all talking to local models via
[Ollama](https://ollama.com) or any OpenAI-compatible local server. No cloud, no telemetry.

## What's here

| Area | What it does |
|---|---|
| **Chat / companion** | A stateful companion with layered memory, budgeted context, rolling summaries, and sentence-level text + voice streaming. |
| **Model routing** | Task-based multi-model routing — each turn is auto-routed to the best local model for the kind of work (coding vs discussion vs companion). See [Model Routing](model-routing.md). |
| **Local voice (TTS)** | Pluggable local neural voice engines (Chatterbox, Kokoro, Orpheus, Qwen3) behind one `/api/tts` endpoint. |
| **Agent harness** | A reusable tool-calling loop (planned) that will power both a coding assistant and the companion. See [ADR 0003](adr/0003-local-agent-harness.md). |

## Architecture at a glance

- **Backend API** — `apps/backend_app.py` (port 8766), subclassing the core `LocalUIHandler` in `apps/web_app.py`.
- **Static UI** — `apps/ui_app.py` (port 8765) + a React/Vite frontend under `frontend/`.
- **LLM call layer** — `core/local_llm_writer.py` (`ChatConfig`, `call_model`, `stream_model`).
- **Memory** — SQLite (`.aibot/conversations.sqlite3`) is the source of truth; semantic recall via a local vector index.

See [Architecture](architecture.md) for the full picture, and the [API Reference](api/index.md)
for module-level docs generated from source docstrings.

## Running it

```bash
python3 apps/backend_app.py   # API on http://127.0.0.1:8766
python3 apps/ui_app.py        # static UI on http://127.0.0.1:8765
```

Requires a local model server (Ollama by default) with at least one model pulled.

## These docs

Built with [MkDocs](https://www.mkdocs.org/) + the Read the Docs theme, with API pages
auto-generated from Python docstrings via
[mkdocstrings](https://mkdocstrings.github.io/). Build locally:

```bash
mkdocs serve   # live preview at http://127.0.0.1:8000
mkdocs build   # static site into ./site
```

Design records — the [ADRs](adr/0001-offline-local-llm-interface.md), the
[Companion Harness Plan](companion-harness-plan.md), and the
[Agent Harness Plan](agent-harness-plan.md) — capture *why* things are the way they are.
The [Knowledge Base](knowledge-base.md) is the long-form reference.
