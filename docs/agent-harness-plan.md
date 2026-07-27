# Revenant — AIBot Agent Harness Plan

**Revenant** is a local, offline agentic harness ("Claude Code, but for your private Ollama LLM").
Reusable tool-calling loop that powers **both** a coding assistant (`revenant` CLI) and the companion.
Name verified clear in the AI-agent/CLI space (2026-07-26).

## 0. Grounding facts (verified 2026-07-26)

- **See [ADR 0003](adr/0003-local-agent-harness.md)** for the full decision record + hardware table.
- **Machine:** Apple M5 Pro, 15 cores, **24 GB** unified RAM, ~467 GB free, `rg` installed, Ollama 0.32.
  → can hold 2× 7-8B Q4 models resident (~10 GB); 14B Q4 viable but slower.
- **Model decision:** pull **`qwen2.5:7b`** (native tool-calling) for the CODING agent; keep
  **Stheno-8B** for the COMPANION (prompt-based protocol). qwen2.5 pull started this session.
- **Backend is Ollama** at `localhost:11434`. Models present:
  `hf.co/.../L3-8B-Stheno-v3.2-gguf:Q4_K_M` (roleplay) and `gemma:latest` (2yr old).
- **LLM call layer already exists**: `core/local_llm_writer.py` → `call_model()` (blocking)
  and `stream_model()` (SSE deltas), both Ollama + OpenAI-compatible. The harness reuses these.
- **Turn machinery already exists**: `apps/web_app.py` → `_prepare_turn()` / `_finalize_turn()`,
  budgeted context (`aibot_context.py`), rolling summary (`aibot_summary.py`), layered memory
  (`aibot_personal_memory.py`, `aibot_storage.py` SQLite).
- **CRITICAL CONSTRAINT — no native tool calling.** Passing an OpenAI-style `tools` array to
  `/api/chat` returns **empty content, `tool_calls: null`** on *both* current models. The Stheno
  GGUF has no tool template; this gemma build predates Ollama tool support.
  → The harness MUST use **prompt-based tool calling**: instruct the model to emit a tagged block,
    then parse it out of the text. Model-agnostic; works today, and still works if you later pull a
    tool-native model (e.g. `qwen2.5`, `llama3.1:8b`) — we detect and prefer native then.

## 1. Architecture — one loop, two front-ends

```
                          core/agent_loop.py
                    ┌────────────────────────────┐
   coding CLI ─────▶│  AgentLoop.run(goal)       │
   companion  ─────▶│   while not done:          │
                    │     msgs = build_context() │──▶ call_model / stream_model  (existing)
                    │     action = parse(reply)  │◀── model reply (text)
                    │     if action.tool:        │
                    │        obs = registry.run()│──▶ ToolRegistry.dispatch()
                    │        append(obs)         │        ├─ fs tools (read/write/edit/glob/grep)
                    │     else: final = reply    │        ├─ shell (gated)
                    └────────────────────────────┘        ├─ memory (search/save)  ← reuses stores
                                                          └─ web (optional, offline-default OFF)
```

- **`core/agent_loop.py`** — engine. Model-agnostic; knows nothing about companion vs coding.
- **`core/agent_tools.py`** — `Tool` dataclass + `ToolRegistry`. Each tool = name, description,
  JSON-ish schema (for the prompt), a `run(**kwargs) -> str`, and flags: `parallel_safe`,
  `requires_approval`, `mutating`.
- **`core/agent_protocol.py`** — the prompt-based tool protocol: renders tool docs into the system
  prompt, and `parse_action(text)` extracts the tool call (or "final answer"). Supports two wire
  formats, auto-selected: native `tool_calls` if the model emits them, else the tagged-text fallback.
- **Front-ends**: a coding entry point (`apps/agent_cli.py` (the `revenant` CLI)) and companion integration (new
  `handle_agent_turn` path in `web_app.py`) both instantiate `AgentLoop` with a different
  `ToolRegistry` + system persona.

## 2. The tool-call protocol (prompt-based, the load-bearing piece)

System prompt gets an injected block:

```
You can act by emitting ONE action per reply, as a fenced block:
```action
{"tool": "read_file", "args": {"path": "core/agent_loop.py"}}
```
When you are done, reply normally with NO action block.
Available tools:
- read_file(path): return file contents
- write_file(path, content): create/overwrite a file
- run_bash(command): run a shell command  [asks for approval]
...
```

Parser (`parse_action`) precedence:
1. If the response object carried native `message.tool_calls` → use it (future tool-native models).
2. Else scan for a ```action fenced block → `json.loads` the body.
3. Else treat the whole reply as the **final answer** (loop ends).

Robustness (8B models are sloppy): tolerate missing fences, trailing prose, single-quotes →
try `json.loads`, then a lenient regex extract of `"tool"` + `"args"`, then give the model a
one-line "your action block was malformed, resend just the JSON" nudge (max 1 retry) before
falling back to treating it as final text.

## 3. Tool set (v1)

| Tool | Args | Flags | Notes |
|------|------|-------|-------|
| `read_file` | path | safe | clamp to workspace root; max N KB |
| `write_file` | path, content | mutating, approval | staleness check optional |
| `edit_file` | path, old, new | mutating, approval | exact-match single replace (mirror your Edit tool) |
| `glob` | pattern | safe, parallel | |
| `grep` | pattern, path? | safe, parallel | ripgrep if present, else Python |
| `run_bash` | command | mutating, approval | allowlist + block `&& | ; \` $()` by default |
| `list_dir` | path | safe | |
| `memory_search` | query | safe | **reuses `STATE.memory.recall` / PersonalMemoryStore** |
| `memory_save` | category, content | mutating | reuses `PersonalMemoryStore.learn`/notes; boundary stays gated |
| `web_search` | query | safe, **off by default** | offline-first; only if user enables |

Companion front-end exposes a **subset**: `memory_search`, `memory_save`, `set_reminder`,
`recall_episode` — no filesystem/shell. Coding front-end exposes fs + shell + grep/glob.
Same loop, different registry — that is the whole point of the shared harness.

## 4. Safety / approval (mirrors Claude Code)

- Every `mutating` / `requires_approval` tool call pauses and asks the human, unless a
  `--yolo` / durable-approval flag is set (per session).
- `run_bash` defaults to an **allowlist** + operator-blocklist; hard-deny `rm -rf`, fork bombs.
- Path confinement: all fs tools resolve under a fixed workspace root; reject `..`/symlink escape.
- Reuses the companion advisor safety rule: `boundary`-category memory writes stay human-gated
  (already enforced in `PersonalMemoryStore.learn`; the `memory_save` tool routes through it, not around).

## 5. Context & loop control (reuse what exists)

- Reuse `assemble_context()` (token-budgeted message list) for the loop's message array — the loop
  is just a multi-turn conversation where some "user" turns are tool observations.
- Reuse `maybe_summarize()` so long agentic runs roll old turns into a summary (prevents 8B
  context blowout — the local analog of compaction).
- `max_steps` cap (default ~15) + `max_bad_parses` cap to prevent infinite loops on a weak model.
- Sentence/step streaming already solved (`sentences_from_deltas`) — surface each action + observation
  to the UI/CLI as it happens.

## 6. Build phases (each verified by running it)

- **P1 — Registry + protocol + parser** (`agent_tools.py`, `agent_protocol.py`), unit-tested on
  malformed 8B-style outputs. No LLM needed. *Deliverable: parser passes fixtures.*
- **P2 — AgentLoop engine** (`agent_loop.py`) with fs tools (read/glob/grep/list) only, read-only,
  no approval path yet. *Deliverable: `agent_cli.py "summarize what core/ does"` runs a real
  read→grep→answer loop against Ollama.*
- **P3 — Mutating tools + approval gate** (write/edit/bash, allowlist, path confinement).
  *Deliverable: "add a /health route to backend_app.py" edits the file after approval; verify import.*
- **P4 — Context/summary/step-streaming integration** (wire `assemble_context` + `maybe_summarize`
  into the loop; stream steps). *Deliverable: a 10+ step task doesn't blow context.*
- **P5 — Companion front-end** (companion registry: memory/reminder tools; `handle_agent_turn`
  route). *Deliverable: "remember we talked about Goa tomorrow" triggers memory_save mid-chat.*
- **P6 — Model upgrade path** (detect native `tool_calls`; recommend pulling a tool-native model
  like `qwen2.5:7b`/`llama3.1:8b` for the coding front-end while keeping Stheno for companion).

## 7. Explicitly NOT doing

- **No Anthropic SDK / cloud API.** Project is offline-first by ADR; the `claude-api` skill's
  `anthropic.Anthropic()` path is wrong here. All LLM calls stay on local `call_model`/`stream_model`.
- No native Ollama `tools=` reliance on current models (verified broken). Kept as an auto-detected
  fast path for future models only.
```
