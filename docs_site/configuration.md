# Configuration

Revenant is configured three ways, in increasing precedence:

1. **Built-in defaults** — sensible defaults baked into `nerva-core`.
2. **`profiles.json`** — a JSON file in your data directory that overrides model
   roles and settings.
3. **Command-line flags** — override everything for a single run.

!!! note "Prerequisites"
    You've [installed Revenant](installation.md) and have Ollama running.

---

## Data directory

Revenant stores its runtime state under a **data directory** that resolves
relative to the current working directory:

```text
.aibot/
├── profiles.json        # model roles + settings (optional; created on demand)
└── conversations.sqlite3 # local history (never leaves your machine)
```

!!! warning "CWD-relative"
    Because `.aibot/` is found relative to `Path.cwd()`, always run `revenant`
    from a stable working directory (e.g. your repo root) so it finds the same
    config and history each time.

## Models

Revenant assigns models to **roles**. A role is a job ("write code", "classify
this request"); a model is the Ollama tag that fills it. Defaults live in
`nerva_core.aibot_profiles.DEFAULT_PROFILES` and can be overridden in
`.aibot/profiles.json`.

| Role | Purpose | Suggested model |
|------|---------|-----------------|
| `code` | Writing and editing code (the main coding driver) | `qwen2.5-coder:7b` (tool-capable) |
| `language` | General discussion and reasoning | `qwen2.5:14b` |
| `router` | Tiny, fast classifier for model routing | `qwen2.5:7b` |
| `fallback` | Used when classification fails | → `language` |

Pull the models you plan to use:

```bash
ollama pull qwen2.5-coder:7b
ollama pull qwen2.5:7b
```

!!! tip "Native tools vs. prompt fallback"
    Models with a **tool template** (e.g. `qwen2.5-coder:7b`) use native
    `tool_calls`. Models **without** one use a prompt-based `#!json action`
    protocol instead — Revenant auto-detects which per model and caches the
    result. Force the fallback with `--no-native-tools`. See
    [Configure model routing](guides/model-routing.md).

### profiles.json

```json title=".aibot/profiles.json"
{
  "model_roles": {
    "code": "qwen2.5-coder:7b",
    "language": "qwen2.5:14b",
    "router": "qwen2.5:7b"
  }
}
```

An explicit `--model` on the command line overrides the `code` role for that run.

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://localhost:11434` | Base URL of the Ollama server. Overridden by `--base-url`. |
| `NO_COLOR` | _(unset)_ | If set, disables ANSI color in output (same as `--no-color`). |

!!! note "Secrets handling"
    Revenant needs **no API keys or secrets** — the model is local. There is
    nothing to store, rotate, or leak. The only network endpoint is your own
    Ollama server.

## Command-line settings

Every setting can be overridden per run. The most common:

| Flag | Default | Overrides |
|------|---------|-----------|
| `--model` | `code` role | The coding model for this run. |
| `--base-url` | `http://localhost:11434` | Ollama server URL. |
| `--workspace` | `.` (cwd) | The repo root the agent may read. |
| `--max-steps` | auto (from RAM) | Hard cap on agent loop iterations. |
| `--max-context-tokens` | auto (from RAM) | Context budget before compaction. |
| `--read-only` | off | Disable all mutating tools. |
| `--no-native-tools` | off | Force the prompt-based tool protocol. |

See the full [CLI reference](reference/cli.md) for every flag.

---

## Next steps

- [Configure model routing](guides/model-routing.md) — how roles are chosen per turn.
- [CLI reference](reference/cli.md) — every argument and flag.
- [Configuration reference](reference/config.md) — the complete settings table.
