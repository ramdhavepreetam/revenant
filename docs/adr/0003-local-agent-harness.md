# ADR 0003 — Local Agent Harness (offline, tool-using, on-prem private)

- **Status:** Accepted (2026-07-26)
- **Deciders:** Preetam (owner), pairing session
- **Supersedes / relates to:** builds on [ADR 0001](0001-offline-local-llm-interface.md)
  (offline local LLM interface). Companion memory/context work: `docs/companion-harness-plan.md`.
- **Full build plan:** `docs/agent-harness-plan.md` (read alongside this ADR).

---

## Context

AIBot currently calls a local LLM (Ollama) and gets back **prose only** — it cannot read files,
run commands, search its own memory, or take multi-step actions. The owner wants a "Claude Code
for my private LLM": a reusable **agentic tool-calling harness** on top of the existing local
model, capable of powering **both** a coding assistant and the companion, running **fully
offline / on-prem** with **no development-time content censorship** (authorized private machine).

### Hardware (measured 2026-07-26)

| Property | Value | Implication for harness |
|---|---|---|
| Chip | Apple M5 Pro (15 cores: 5 E + 10 P) | Fast local inference; parallel-safe tools can fan out |
| Unified memory | 24 GB | Can hold **two** 7–8B Q4 models resident (~10 GB) + app; 14B Q4 (~9 GB) viable but slower |
| Disk free | ~467 GB | Ample for extra models |
| Ollama | 0.32.0 | Supports native `tools=` **for models with a tool template** |
| ripgrep | present (`/opt/homebrew/bin/rg`) | `grep` tool shells to `rg` |
| Python | 3.11.1 | matches project |

### Models

- `hf.co/.../L3-8B-Stheno-v3.2-gguf:Q4_K_M` — companion/roleplay. **No tool template.**
- `gemma:latest` — 2yr old, summaries. **Predates Ollama tool support.**
- `qwen2.5:7b` — **being pulled for the coding agent** (native tool-calling, strong instruction-following).

### The decisive technical finding

Passing an OpenAI-style `tools` array to `/api/chat` on **Stheno** and **this gemma** returns
`tool_calls: null` **and empty content** — they have no tool template. Native tool calling is
therefore **not usable on the companion model**. qwen2.5 *does* ship a template, so native works there.

---

## Decision

**Name:** the agent harness / CLI is **`revenant`** (invoked as `revenant …`). Chosen for a
persistent local mind that "returns" each session; verified clear in the AI-agent/CLI space
(unlike `ghost`, which collides with several existing local-Ollama agents + the Ghost blog CLI).
The coding front-end lives at `apps/agent_cli.py` and is exposed as the `revenant` command.

Build a **model-agnostic agent harness** in `core/`, layered on the existing
`call_model`/`stream_model` HTTP functions. It uses a **dual tool-call protocol**:

1. **Native `tool_calls`** when the model supports it (qwen2.5 coding path) — preferred, most robust.
2. **Prompt-based tagged-block protocol** as the universal fallback (Stheno companion path, any model) —
   the model emits a ` ```action {json} ``` ` block; the harness parses it out of the text.

The harness auto-detects which to use per model (probe once, cache the result).

### Why not the Anthropic SDK / cloud API

The bundled `claude-api` skill produces `anthropic.Anthropic()` cloud code needing an API key.
This project is **offline-first by [ADR 0001]** — no cloud, no telemetry. **Rejected.** Every LLM
call stays on local Ollama via `call_model`/`stream_model`. (Native `tool_calls` detection is an
Ollama feature, not an Anthropic one.)

### Component layout

| File | Responsibility |
|---|---|
| `core/agent_tools.py` | `Tool` dataclass + `ToolRegistry`. Flags: `parallel_safe`, `requires_approval`, `mutating`. |
| `core/agent_protocol.py` | Render tool docs into system prompt; `parse_action(reply, raw_msg)` → `ToolCall | FinalAnswer`. Dual protocol + sloppy-8B tolerance. |
| `core/agent_loop.py` | The engine: build context → call model → parse → dispatch tool → append observation → repeat. Reuses `assemble_context` + `maybe_summarize`. Caps: `max_steps`, `max_bad_parses`. |
| `core/agent_capacity.py` | Detect hardware/model (RAM, cores, model size, native-tools?) → recommend context budget, whether to keep 2 models resident, step caps. Encodes the table above at runtime. |
| `apps/agent_cli.py` | Coding front-end (fs + shell + grep/glob registry, qwen2.5). |
| `web_app.py` `handle_agent_turn` | Companion front-end (memory/reminder registry subset, Stheno). |

### Reasoning / "in-depth thinking"

- The loop keeps a visible **scratchpad**: before each action the model is prompted to think
  (`## Thinking` section) then act. Thinking is streamed to the UI/CLI but not persisted as a
  final answer. For qwen2.5, optionally enable Ollama's think mode if available.
- `max_steps` default **15** (coding) / **6** (companion); tunable via `agent_capacity`.
- Long runs roll old steps into `maybe_summarize()` (local analog of context compaction) so the
  8B/7B context window doesn't overflow.

### Safety model — "no censorship" clarified

**No *content* refusals for development tasks** (this is an authorized private box). BUT keep
**correctness/damage guardrails**, which are NOT censorship:

- Mutating tools (`write_file`, `edit_file`, `run_bash`) pause for **human approval** unless a
  per-session `--yolo` flag is set.
- **Path confinement**: fs tools resolve under a fixed workspace root; reject `..`/symlink escape.
- `run_bash`: block obvious footguns (`rm -rf /`, fork bombs) even in yolo; otherwise allow (private dev).
- Companion `memory_save` routes through existing `PersonalMemoryStore.learn` so the `boundary`
  category stays human-gated (existing advisor safety rule — unrelated to dev censorship).

---

## Consequences

**Positive:** reusable engine for coding + companion; fully offline; works on today's models via
prompt protocol and gets more robust automatically when a tool-native model is used; hardware-aware.

**Negative / risks:** prompt-based protocol on an 8B roleplay model will mis-format sometimes
(mitigated: lenient parser + 1 retry nudge + `max_bad_parses`). Running two models resident costs
~10 GB (fine at 24 GB, but close if other heavy apps run — `agent_capacity` will warn).

**Follow-ups:** if coding needs more muscle, a 14B Q4 fits (slower). Consider a dedicated
tool-native summary model to retire the 2yr gemma.

---

## Build phases — ALL COMPLETE (2026-07-26)

- **P1 ✅** registry + protocol + parser (`agent_tools.py`, `agent_protocol.py`).
- **P2 ✅** loop engine + read-only fs tools + the `revenant` CLI (`agent_loop.py`, `agent_fs_tools.py`, `agent_cli.py`).
- **P3 ✅** mutating tools + approval gate + path confinement + bash footgun-block (`agent_edit_tools.py`, `agent_bash_tool.py`).
- **P4 ✅** in-loop context compaction (token-budgeted; keeps system+goal+recent verbatim).
- **P5 ✅** companion front-end (`agent_companion_tools.py`, `/api/agent` `handle_agent_turn`); boundary-gated.
- **P6 ✅** hardware-aware tuning (`agent_capacity.py`) + native-`tool_calls` auto-detection (`agent_native_tools.py`).

Note: P4 did NOT reuse `assemble_context`/`maybe_summarize` directly (SQLite-coupled + chat-shaped);
built a purpose-fit in-loop compactor reusing `estimate_tokens`. See `docs/agent-harness-plan.md`.

## Status: harness COMPLETE

The Revenant harness is fully built and verified end-to-end. Coding front-end (`revenant`) and
companion front-end (`/api/agent`) both run the shared `AgentLoop`. 158 tests pass; docs (MkDocs +
RTD) strict-clean. Live-verified: coding read/edit/bash-with-approval, companion memory_save with the
boundary safety gate, context compaction, hardware detection, and native-tool auto-detection.

**Possible future work (not planned):** stream companion turns; wire the coding CLI into a TUI; a
dedicated tool-native summary model to retire the 2yr gemma; a `revenant chat` interactive REPL mode.
