# AIBot — Knowledge Base

> Shared reference for the AIBot AI-companion project: what it is, how its memory
> works today, and how we plan to use [NervaPack](https://nervapack.readthedocs.io/en/latest/)
> for a code knowledge graph + MCP so we can work on this repo together efficiently.
>
> Companion to [ADR 0001](adr/0001-offline-local-llm-interface.md).
>
> **Status of the NervaPack sections:** the *AIBot code* facts below are verified
> against the source. The *NervaPack library* facts (SDK signatures, MCP command,
> tool names) are taken from the NervaPack docs and **should be confirmed against
> the locally installed package** — see [Verify locally](#verify-nervapack-locally).
> They were not verifiable in the authoring session because the shell was unavailable.

---

## 1. Overview

AIBot is an **offline-first local LLM companion / long-form writing tool**. It talks
to a local model server (Ollama or any OpenAI-compatible server such as LM Studio /
llama.cpp / vLLM), targets 400–800 token replies, and keeps everything on disk. No
cloud inference, no telemetry, no accounts.

It ships in three runnable pieces plus a CLI:

| Entry point | Role | Default port |
|---|---|---|
| `backend_app.py` | HTTP API (chat, memory, TTS, export, health) | `127.0.0.1:8766` |
| `ui_app.py` | Static browser UI server (serves `web/`) | `127.0.0.1:8765` |
| `web_app.py` | Legacy single-process launcher **and** the core request handler (`LocalUIHandler`) that `backend_app.py` subclasses | — |
| `local_llm_writer.py` | CLI client + the reusable LLM-calling layer (`ChatConfig`, `call_model`) | — |

Runtime data lives under `.aibot/` (git-ignored). See the [file map](#4-file-map).

---

## 2. Memory architecture (what AIBot does *today*)

AIBot has a **layered memory system**. Three distinct stores serve three purposes —
keep them separate in your head:

```
                       ┌─────────────────────────────────────────────┐
   user message ──────►│  handle_chat()  (web_app.py)                 │
                       │                                              │
                       │  1. load structured personal memory ────────┼──► SQLite
                       │  2. semantic recall (top-5) ────────────────┼──► NervaPack VectorStore
                       │  3. response_shape() sizes the reply         │
                       │  4. call_model() → Ollama / OpenAI-compat    │
                       │  5. save turn ──────────────────────────────┼──► SQLite
                       │  6. extract_personal_memories() (learn) ────┼──► SQLite (pending/active)
                       │  7. index active memories ──────────────────┼──► NervaPack VectorStore
                       └─────────────────────────────────────────────┘
```

### 2a. Durable conversation store — SQLite (source of truth)

- File: `aibot_storage.py` → `ConversationStore`, backed by `.aibot/conversations.sqlite3`.
- Tables: `conversations` (id, title, timestamps) and `messages` (id, conversation_id,
  role, content, created_at), indexed on `(conversation_id, created_at)`.
- This is the **canonical** record. Exports (`md` / `json` / `txt`) are produced from here.

### 2b. Structured personal memory — SQLite + a learning engine

- File: `aibot_personal_memory.py` → `PersonalMemoryStore` (same SQLite DB).
- Each `PersonalMemory` has a **category** (`identity_fact`, `preference`, `need`,
  `boundary`, `companion_style`, `relationship_state`, `story_fact`, `voice_preference`)
  and a **status** (`active`, `pending`, `archived`), plus `pinned` and `confidence`.
- `extract_personal_memories()` is a **regex/heuristic learner**: after each turn it
  pulls candidate facts (the user's name, stated preferences, boundaries, relationship
  cues, story facts, voice feedback) out of the user's message. Conflicting items
  (e.g. a new boundary that contradicts an existing preference) are saved as `pending`
  for human approval rather than applied silently.
- `prompt_memories()` formats the active/pinned memories for injection into the system
  prompt every turn.
- `aibot_companion_memory.py` (`CompanionMemoryStore`) holds the older relationship-state
  blob (`user_name`, `companion_name`, `tone_preferences`, `boundaries`, …) in
  `.aibot/companion_memory.json`, and includes a one-time migration into the
  `personal_memories` table.

### 2c. Semantic recall — NervaPack `VectorStore` (offline index)

- File: `aibot_memory.py` → `NervaPackMemory`, backed by `.aibot/memory/nervapack_chroma`
  (ChromaDB).
- **Important framing — this is an intentional adapter, not standard NervaPack usage.**
  NervaPack is a *code* knowledge-graph tool (AST → graph → K-Hop retrieval; see §3).
  AIBot deliberately reaches *past* that and uses only NervaPack's underlying
  `VectorStore` as a generic, persistent, fully-offline semantic index. The class
  docstring says so explicitly: *"NervaPack is optimized for graph/code retrieval, but
  its VectorStore gives us an offline persistent semantic index."*
- It does this by wrapping each conversational item as a synthetic "chunk" with a
  **fake `file_path`** so the store will ingest it:

  ```python
  # aibot_memory.py — a chat turn becomes a pseudo-"file"
  chunk = {
      "header": f"{role} message",
      "file_path": f"conversation/{conversation_id}/{role}/{uuid.uuid4()}",
      "content": content,
  }
  self.store.ingest_chunks([chunk])
  ```

- API actually used (verified in source):
  - `VectorStore(db_path=...)`
  - `store.ingest_chunks([{header, file_path, content}, ...])`
  - `store.search(query, n_results=limit)` → `results["documents"][0]`
  - `store.collection.delete(where={"type": "markdown"})` (used by `rebuild_notes`)
- **Consequence to remember:** because AIBot only uses `VectorStore`, the NervaPack
  *graph*, *K-Hop BFS*, and `GraphRetriever` are **not exercised at all** by AIBot's
  runtime memory. They're a separate capability we can adopt for the codebase itself (§3).

---

## 3. NervaPack for *this repo*: code knowledge graph + MCP (forward-looking)

This section is about helping **us work on AIBot together** — not about AIBot's runtime
memory. The idea: ingest the AIBot repo into a NervaPack knowledge graph and expose it
over MCP, so future Claude sessions can answer "where is X / what calls Y" with
token-efficient, AST-grounded context instead of re-reading files.

> ⚠️ Confirm the commands/signatures in this section against the installed package
> before relying on them — see [Verify locally](#verify-nervapack-locally).

### 3a. What NervaPack actually is

A privacy-first, **offline** knowledge graph for code. It parses source with
**tree-sitter** (deterministic AST, no arbitrary chunks), links docs to code with a
local LLM, and retrieves context with **K-Hop BFS** for up to ~90% token savings vs
naive RAG. 100% local (ChromaDB + Ollama).

**Graph model** (`NetworkX DiGraph`):

- Node types: `file`, `class`, `function`, `import`, `markdown`.
- Edge types: `DEFINES` (file → code entity) and `EXPLAINS` (markdown → code entity).

**Layers / modules** (per docs):

| Layer | Module | Key class |
|---|---|---|
| Parser | `nervapack.parser` | `ASTParser`, `MarkdownChunker`, `LanguageRegistry` → `ParsedEntity` |
| Vector store | `nervapack.graph.vector_store` | `VectorStore` (ChromaDB, `all-MiniLM-L6-v2`, 384-dim) |
| LLM linking | `nervapack.llm` | `LLMProvider` base; `OllamaProvider`, `ClaudeAPIProvider`, `OpenAIProvider`, `MCPDelegationProvider` |
| Graph build | `nervapack.graph.builder` | `GraphBuilder` |
| Retrieval | `nervapack.graph.retrieval` | `GraphRetriever` |

On-disk layout: `.nervapack/graph.graphml`, `.nervapack/chroma_db/`,
`.nervapack/query_history.jsonl`, plus generated HTML visualizations.

### 3b. Ingest the repo

```bash
pip install "nervapack[mcp]"     # mcp extra needed for the MCP server
cd /Users/preetam/Documents/AI/AIBot
nervapack ingest .               # builds .nervapack/ (≈5–10 min for ~500 files)
```

This writes a `.nervapack/` directory — **add it to `.gitignore`** alongside `.aibot/`.

### 3c. Python SDK (for ad-hoc scripts)

Per the docs, the SDK is three classes:

```python
from nervapack.graph.builder import GraphBuilder
from nervapack.graph.vector_store import VectorStore
from nervapack.graph.retrieval import GraphRetriever

graph     = GraphBuilder().load_graph()
hits      = VectorStore().search("how is chat handled", n_results=3)   # → {"ids": [...], ...}
retriever = GraphRetriever(graph)
subgraph  = retriever.retrieve_context(start_nodes=hits["ids"][0], max_hops=1)
markdown  = retriever.format_as_markdown(subgraph)
```

> Note: AIBot's own `aibot_memory.py` constructs `VectorStore(db_path=...)` and calls
> `ingest_chunks(...)`. The doc snippets above show no-arg constructors. Treat the
> installed package's signatures as authoritative where they differ.

### 3d. MCP server (the part that lets us "work together")

Per the docs, NervaPack ships an MCP server exposing three tools:

| Tool | Purpose |
|---|---|
| `query_codebase(prompt, max_hops)` | semantic + K-Hop retrieval over the graph |
| `graph_status()` | health / freshness of the graph |
| `list_entities(type, file_path)` | browse nodes (files, classes, functions, …) |

Documented client config (e.g. `.mcp.json`):

```json
{
  "mcpServers": {
    "nervapack": {
      "command": "nervapack-mcp",
      "description": "NervaPack knowledge graph"
    }
  }
}
```

> ⚠️ **Low-confidence details** (came from a docs page that could not be re-verified):
> the standalone `nervapack-mcp` command, the three exact tool names, and the minimal
> config shape. A Typer-based CLI often exposes MCP as a *subcommand*
> (`nervapack mcp serve`) rather than a separate console script. **Confirm before
> wiring it up** (next section). Once confirmed, add the server to Claude Code via
> `.mcp.json` in the repo root (or `claude mcp add`).

### Verify NervaPack locally

Run these in a working terminal and paste the output back to me; I'll lock the §3
details to ground truth:

```bash
pip show nervapack                 # version + install Location
nervapack --help                   # is `mcp` a subcommand? is `ingest` there?
which nervapack-mcp || echo "no standalone nervapack-mcp script"
python -c "import nervapack, inspect; from nervapack.graph.vector_store import VectorStore; print(inspect.signature(VectorStore.__init__)); print([m for m in dir(VectorStore) if not m.startswith('_')])"
# entry points (settles the exact MCP command):
python -c "import importlib.metadata as m; print(m.entry_points(group='console_scripts'))" | tr ',' '\n' | grep -i nerva
```

---

## 4. File map

### Python (backend / core)
| File | Responsibility |
|---|---|
| `backend_app.py` | API server; `BackendAPIHandler(LocalUIHandler)` adds CORS + OPTIONS |
| `ui_app.py` | Static UI server; injects `window.AIBOT_API_BASE_URL` via `/config.js` |
| `web_app.py` | `LocalUIHandler` (all `/api/*` routes), `AppState` singleton, `response_shape()`, `handle_chat()` |
| `local_llm_writer.py` | `ChatConfig`, `call_ollama` / `call_openai_compatible` / `call_model`, `trim_messages`, CLI `main()` |
| `aibot_profiles.py` | `DEFAULT_PROFILES`, `load/save/ensure_profiles`, `apply_profile`, `build_companion_prompt` |
| `aibot_storage.py` | `ConversationStore` + `Conversation` (SQLite, source of truth) |
| `aibot_personal_memory.py` | `PersonalMemoryStore`, `PersonalMemory`, `extract_personal_memories`, `prompt_memories` |
| `aibot_companion_memory.py` | `CompanionMemoryStore` (legacy JSON blob + migration) |
| `aibot_memory.py` | `NervaPackMemory` (VectorStore adapter), `format_memory_context` |
| `aibot_tts.py` | voice profiles, `detect_mood`, `synthesize_tts` → Kokoro or macOS `say` |
| `tts_kokoro_worker.py` | isolated Kokoro subprocess (runs in `.aibot/tts-venv`) |

### Frontend (`web/`)
| File | Responsibility |
|---|---|
| `web/index.html` | sidebar + workspace shell; controls, companion panels, memory dashboard |
| `web/app.js` | global state, `api()` fetch client, profile/memory rendering, event handlers |
| `web/styles.css` | design-system CSS variables + components |

### HTTP API (handled in `web_app.py`)
`GET /api/profiles` · `GET /api/voice-profiles` · `GET|POST /api/conversations` ·
`GET /api/conversations/{id}` · `POST /api/chat` · `GET|POST /api/memories` ·
`POST /api/memories/{id}[/approve|/archive|/pin]` · `DELETE /api/memories/{id}` ·
`POST /api/memories/rebuild-index` · `POST /api/companion-memory` · `POST /api/tts` ·
`POST /api/export` · `POST /api/health` · `GET /audio/{file}`

### Runtime data — `.aibot/` (git-ignored)
`conversations.sqlite3` (conversations + personal memory) · `memory/nervapack_chroma/`
(VectorStore index) · `audio/` · `profiles.json` · `voice_profiles.json` ·
`companion_memory.json` (legacy) · `exports/` · `tts-venv/`

---

## 4b. Active work — companion "feeling" fixes (started 2026-06-25)

Problem: the companion felt flat — replies read like a *generated script* and the
voice read text without *feeling* it. Confirmed flat in **both** layers.

### Text layer — DONE
`web_app.py` `handle_chat` was assembling the prompt in a way that made the model
"execute a writing task" each turn. Fixed:
- Persona / writing-mode / companion / personalization memory moved into the **system**
  message; the user turn is now just the user's words (+ quiet recalled background).
- **Removed the visible token target** (`Target about N-M tokens`) — length is enforced
  by the API `max_tokens`; showing the number made the model write *to* a word count.
  The reply-shape *instruction* (tone/pacing) is kept.
- Removed the **duplicate style-prompt injection** (it was already in `config.system_prompt`).

### Voice layer — IN PROGRESS: Kokoro → Chatterbox
Root cause: `aibot_tts.py` `apply_mood` only changes speed + swaps a voice — **no prosody
shaping**, so Kokoro narrates flat. Decision (see memory `aibot-tts-engine-decision`):
replace Kokoro with **Chatterbox TTS** (Resemble AI) — MIT, no content restriction,
`exaggeration` 0.25–2.0 + `cfg` controls, Apple-Silicon MPS capable, built-in voice for now.
**Replace Kokoro entirely once Chatterbox is proven better**, then clean Kokoro off disk.

Migration checklist:
1. **(user)** Install Chatterbox in `.aibot/chatterbox-venv`; smoke-test on MPS/CPU; confirm
   it sounds expressive. (Bash is broken in-session, so the user runs this.)
2. **(claude)** Write `tts_chatterbox_worker.py` (same stdin/stdout-JSON subprocess pattern
   as `tts_kokoro_worker.py`); map mood → `exaggeration`/`cfg`; add **per-sentence mood**
   synthesis (detect mood per sentence, generate each, stitch with pauses).
3. **(claude)** Add **pauses + emphasis** at punctuation / ellipses / quoted lines.
4. **(claude)** Remove Kokoro code + `mira-*neural` profiles. **(user)** delete model
   weights, pip packages, and `.aibot/tts-venv` (needs Bash) — only AFTER Chatterbox is
   confirmed, so there's never a no-voice gap.

## 5. Things to keep straight (gotchas)

1. **"Memory graph" is two different things.** AIBot's runtime memory uses NervaPack's
   `VectorStore` as a flat semantic index — *no graph, no K-Hop*. The actual NervaPack
   *graph* is something we'd build over the **repo's code** (§3), for our own dev workflow.
2. **SQLite is the source of truth**, not the vector store. The Chroma index is rebuildable
   from active personal memories (`POST /api/memories/rebuild-index`, `rebuild_notes`).
3. **AIBot uses no MCP today.** Adding it (§3d) is forward-looking and benefits *us*, not
   the app's end-users.
4. **`web_app.py` is not dead.** It's the legacy launcher *and* the base handler class —
   `backend_app.py` subclasses it.
5. **Two Python environments exist:** the app's own interpreter and the isolated
   `.aibot/tts-venv` for Kokoro/PyTorch. NervaPack must be importable from whichever runs
   the app.
