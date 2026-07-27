# Revenant + AIBot — offline local-LLM monorepo

Two offline, on-prem programs built on a shared core, talking to local models via
Ollama (no cloud, no telemetry):

- **Revenant** — a local coding-agent CLI (Claude-Code-style tool-calling loop).
- **AIBot** — a companion web app (backend API + static UI + local TTS).

## Layout (pip-installable packages)

```
packages/
  nerva-core/     shared: LLM layer + memory/profiles/storage
  nerva-agent/    the agent engine (tool loop, protocol, tools, routing, capacity)
  revenant-cli/   the `revenant` command            (depends on the two above)
  aibot-app/      the AIBot web app + TTS            (depends on the two above)
```

Dev install (editable, in dependency order):

```bash
make dev        # pip install -e each package
make test       # pytest (158 tests)
make docs       # mkdocs build --strict
```

## Revenant CLI

```bash
revenant "summarize what packages/nerva-agent does"
revenant --workspace ~/proj "where is auth handled?"
```

Full guide: [docs/revenant-cli.md](docs/revenant-cli.md). Design: [ADR 0003](docs/adr/0003-local-agent-harness.md).

## AIBot app

See [ADR 0001](docs/adr/0001-offline-local-llm-interface.md) for the offline local interface decision.

The app is split into a backend API and a static UI. Start the backend first:

```bash
aibot-backend
```

The backend listens on:

```text
http://127.0.0.1:8766
```

Native apps should call this backend directly. Main endpoints include:

- `GET /api/profiles`
- `GET /api/conversations`
- `POST /api/chat`
- `GET /api/memories`
- `POST /api/tts`
- `GET /audio/<file>`

Start the static browser UI in a second terminal:

```bash
aibot-ui
```

Open the UI:

```text
http://127.0.0.1:8765
```

Use another UI port or backend URL if needed:

```bash
aibot-ui --port 8770 --api-base-url http://127.0.0.1:8766
```

`web_app.py` remains as a compatibility single-process launcher, but new development should target `backend_app.py` plus `ui_app.py`.

The UI provides:

- Saved local conversations
- Model, story style, and generation preset selection
- Personal memory dashboard plus NervaPack-backed semantic memory recall
- Local neural voice generation
- Markdown export
- Local Ollama or OpenAI-compatible model calls

## Optional Qwen3-TTS Voice Engine

Qwen3-TTS is available as an experimental local voice engine. It is not the
default yet because it needs a separate dependency install and local benchmark
on Apple Silicon.

Set up the Apple Silicon MLX worker environment:

```bash
python3 -m venv .aibot/qwen3-mlx-venv
.aibot/qwen3-mlx-venv/bin/python -m pip install -U pip mlx-audio
```

Then select the `qwen3-serena-local` voice profile. The backend still uses the
same `POST /api/tts` endpoint, so native apps do not need a separate voice API.

The official Qwen/Torch runtime is kept separate because its dependency set is
not compatible with `mlx-audio`:

```bash
python3 -m venv .aibot/qwen3-torch-venv
.aibot/qwen3-torch-venv/bin/python -m pip install -U pip qwen-tts soundfile
```

See [ADR 0002](docs/adr/0002-qwen3-tts-local-voice-engine.md) for the decision
and benchmarking plan.

## Requirements

- Python 3.10+
- A local model server
- NervaPack and its ChromaDB dependency for memory indexing

The current environment already has NervaPack installed. Runtime app data is written under `.aibot/` by default and is ignored by git.

## Ollama

Start Ollama and pull a model:

```bash
ollama pull llama3.1:8b
```

Run the writer:

```bash
python3 local_llm_writer.py --backend ollama --model llama3.1:8b
```

Run with the local Stheno profile:

```bash
python3 local_llm_writer.py \
  --model-profile stheno-8b \
  --style-profile nsfw-erotic \
  --generation-preset local-8b-14b-balanced
```

The default profile uses this Ollama model tag:

```text
hf.co/RichardErkhov/Sao10K_-_L3-8B-Stheno-v3.2-gguf:Q4_K_M
```

If your local runner exposes a different exact name, edit `.aibot/profiles.json` after the first run.

Check installed Ollama model tags:

```bash
ollama list
```

The UI's **Check runtime** button verifies both the local server and selected model tag.

For a 14B model:

```bash
python3 local_llm_writer.py --backend ollama --model qwen2.5:14b
```

## LM Studio, llama.cpp, or other OpenAI-compatible servers

Start your local server, then run:

```bash
python3 local_llm_writer.py \
  --backend openai \
  --base-url http://localhost:1234 \
  --model local-model
```

For llama.cpp server, the default endpoint is usually:

```bash
python3 local_llm_writer.py \
  --backend openai \
  --base-url http://localhost:8080 \
  --model local-model
```

## Useful Options

```bash
python3 local_llm_writer.py --help
```

Common tuning flags:

- `--min-tokens 400`
- `--max-tokens 800`
- `--temperature 0.85`
- `--top-p 0.9`
- `--repeat-penalty 1.08`
- `--context-messages 18`
- `--data-dir .aibot`
- `--no-memory`

If replies are too short, raise `--max-tokens` to `900` and include "continue the scene in depth" in your prompt. If replies ramble, lower `--temperature` to `0.7` or `--max-tokens` to `650`.

## Custom System Prompt

Create a text file and pass it in:

```bash
python3 local_llm_writer.py --system-prompt-file prompts/story.txt
```

Inside the chat:

- `/reset` clears conversation history
- `/system` prints the active system prompt
- `/quit` exits

## Local Conversations

Conversations are saved automatically in `.aibot/conversations.sqlite3`.

List saved conversations:

```bash
python3 local_llm_writer.py --list-conversations
```

Resume a conversation:

```bash
python3 local_llm_writer.py --conversation-id CONVERSATION_ID
```

Export a conversation:

```bash
python3 local_llm_writer.py \
  --export-conversation CONVERSATION_ID \
  --export-format md
```

Supported export formats are `md`, `json`, and `txt`.

## NervaPack Memory

The browser/native API uses structured personal memories in SQLite, then indexes approved active memories into a local NervaPack/ChromaDB store under `.aibot/memory/`. On each turn, the app recalls relevant local memories and prepends them as context for the local LLM.

Disable memory for a session:

```bash
python3 local_llm_writer.py --no-memory
```

Durable conversation history and semantic memory are separate:

- SQLite stores the canonical saved conversation.
- NervaPack/ChromaDB stores searchable recall memory.
- Exports create portable user-controlled artifacts.
