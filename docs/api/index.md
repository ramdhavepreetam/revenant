# API Reference

Module-level documentation generated from source docstrings via
[mkdocstrings](https://mkdocstrings.github.io/). Modules live under `core/`, `tts/`, and
`apps/` (flat, added to the import path in `mkdocs.yml`).

## core — LLM, routing, memory

| Module | What it does |
|---|---|
| [`local_llm_writer`](local_llm_writer.md) | `ChatConfig`, `call_model`, `stream_model` — the local LLM call layer (Ollama + OpenAI-compatible) and the CLI. |
| [`agent_router`](agent_router.md) | Task-based multi-model routing: `classify`, `config_for_role`, `warm_role`. See [Model Routing](../model-routing.md). |
| [`agent_tools`](agent_tools.md) | Revenant harness: `Tool` + `ToolRegistry` (tool schema, prompt docs, dispatch). |
| [`agent_protocol`](agent_protocol.md) | Revenant harness: dual tool-call protocol — `render_system_block`, `parse_action`. |
| [`agent_fs_tools`](agent_fs_tools.md) | Revenant: read-only, path-confined fs tools (`read_file`, `list_dir`, `glob`, `grep`). |
| [`agent_edit_tools`](agent_edit_tools.md) | Revenant: mutating, approval-gated `write_file` / `edit_file` (path-confined). |
| [`agent_bash_tool`](agent_bash_tool.md) | Revenant: `run_bash` (approval-gated; destructive-command footgun block). |
| [`agent_companion_tools`](agent_companion_tools.md) | Revenant companion front-end: `memory_search` / `memory_save` / `set_reminder` (boundary-gated). |
| [`agent_capacity`](agent_capacity.md) | Hardware/model detection → loop-knob recommendations (`recommend`, `detect_machine`). |
| [`agent_native_tools`](agent_native_tools.md) | Native tool-calling detection per model (`supports_native_tools`, probe-once cache). |
| [`agent_loop`](agent_loop.md) | Revenant: the `AgentLoop` engine (call → parse → approve → dispatch → observe → repeat). |
| [`aibot_context`](aibot_context.md) | Token-budgeted context assembly, memory ranking, sentence buffering. |
| [`aibot_summary`](aibot_summary.md) | Rolling session summarization with a small factual model. |
| [`aibot_profiles`](aibot_profiles.md) | Model/style/companion profiles + `model_roles`; profile resolution. |
| [`aibot_storage`](aibot_storage.md) | SQLite conversation store (source of truth), summaries, episodes. |
| [`aibot_personal_memory`](aibot_personal_memory.md) | Structured personal-memory learning + human-gated approval. |
| [`aibot_memory`](aibot_memory.md) | Local semantic recall (vector index adapter). |
| [`aibot_companion_memory`](aibot_companion_memory.md) | Per-companion memory store. |
| [`aibot_companion_compiler`](aibot_companion_compiler.md) | Compiles a plain-language companion brief into a system block. |

## tts — voice

| Module | What it does |
|---|---|
| [`aibot_tts`](aibot_tts.md) | TTS dispatch layer behind `/api/tts`. |

## apps — servers

| Module | What it does |
|---|---|
| [`web_app`](web_app.md) | Core request handler (`LocalUIHandler`): the turn pipeline, routing integration, all API routes. |
| [`agent_cli`](agent_cli.md) | The `revenant` coding-agent CLI entry point. |

!!! note
    Some older modules still lack module-level docstrings; their public functions and
    classes are documented where docstrings exist. Docstrings are added opportunistically as
    modules are touched.
