# Configuration reference

The complete set of Revenant settings, in one place. For task-oriented guidance
see [Configuration](../configuration.md).

---

## Precedence

Settings resolve in this order (later wins):

1. Built-in defaults (`nerva_core.aibot_profiles.DEFAULT_PROFILES`)
2. `.aibot/profiles.json`
3. Environment variables
4. Command-line flags

## Model roles

Set in `.aibot/profiles.json` under `model_roles`.

| Role | Default intent | Notes |
|------|----------------|-------|
| `code` | Coding driver | Overridable per run with `--model`. |
| `language` | Reasoning / discussion | — |
| `router` | Fast classifier | Small model recommended. |
| `fallback` | Classification miss | Defaults to `language`. |

```json title=".aibot/profiles.json"
{
  "model_roles": {
    "code": "qwen2.5-coder:7b",
    "language": "qwen2.5:14b",
    "router": "qwen2.5:7b"
  }
}
```

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL. Overridden by `--base-url`. |
| `NO_COLOR` | _(unset)_ | Disables ANSI color when set (same as `--no-color`). |

!!! note "No secrets"
    Revenant uses no API keys. The only endpoint is your local Ollama server, so
    there is nothing to store or rotate.

## Command-line flags

| Flag | Default | Description |
|------|---------|-------------|
| `--workspace PATH` | `.` | Repo root the agent may read. |
| `--model NAME` | `code` role | Coding model for this run. |
| `--base-url URL` | `http://localhost:11434` | Ollama server URL. |
| `--max-steps N` | `0` (auto) | Loop iteration cap; `0` derives from RAM. |
| `--max-context-tokens N` | `0` (auto) | Context budget; `0` derives from RAM. |
| `--read-only` | off | Disable mutating tools. |
| `--no-native-tools` | off | Force prompt-based tool protocol. |
| `--yolo` | off | Auto-approve mutations (destructive commands still blocked). |
| `--no-color` | off | Disable colored output. |

## Data directory layout

| Path | Contents |
|------|----------|
| `.aibot/profiles.json` | Model roles and overrides. |
| `.aibot/conversations.sqlite3` | Local conversation history. |

!!! warning "CWD-relative"
    `.aibot/` is resolved from the current working directory. Run from a
    consistent location (e.g. the repo root).

---

## Next steps

- [Configuration](../configuration.md)
- [CLI reference](cli.md)
- [Configure model routing](../guides/model-routing.md)
