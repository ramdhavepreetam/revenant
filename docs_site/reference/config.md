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

## Project config: `.revenant.toml`

A `.revenant.toml` at (or above) your workspace sets repo-specific defaults, found
by walking up like `.git`. A user file at `~/.config/revenant/config.toml` sets
personal defaults. Precedence: **flag › project `.revenant.toml` › user config ›
built-in default**. Unknown keys are ignored; a malformed file is skipped with a
warning rather than breaking the CLI.

```toml title=".revenant.toml"
# Scalar defaults (any subset)
base_url = "http://localhost:11434"
model = "qwen2.5-coder:7b"
read_only = false
max_steps = 20
max_context_tokens = 8000
ignore = ["build/", "*.gen.py"]   # extra ignore globs

# MCP servers (see below)
[[mcp.servers]]
name = "git"
transport = "stdio"
command = "mcp-server-git"
args = ["--repo", "."]
read_only = ["status", "log"]     # these tools skip the approval gate
```

### MCP servers

Each `[[mcp.servers]]` table connects a [Model Context
Protocol](../guides/extending.md#mcp-servers) server whose tools the agent can
call. Only the **stdio** transport is supported today (a local subprocess).

| Key | Required | Description |
|-----|----------|-------------|
| `name` | Yes | Server name; tools appear as `<name>.<tool>`. |
| `transport` | — | `"stdio"` (default). |
| `command` | Yes (stdio) | The server executable. |
| `args` | — | Arguments passed to the command. |
| `env` | — | Extra environment variables. |
| `read_only` | — | Tool names that skip the approval gate. |
| `alias` | — | Override the `<name>` prefix on tool names. |

Inspect configured servers with [`revenant mcp list`](cli.md#mcp).

### Skills

Reusable workflows live in `SKILL.md` files, discovered from
`.revenant/skills/<name>/SKILL.md` (project) and
`~/.config/revenant/skills/<name>/SKILL.md` (user). Frontmatter is **TOML fenced
by `+++`**:

```markdown title=".revenant/skills/run-tests/SKILL.md"
+++
name = "run-tests"
description = "Run the suite and summarize failures."
trigger = "/run-tests"                       # optional
tools = ["run_bash", "read_file"]            # optional tool scope
+++
1. Run `pytest -q`.
2. If it fails, read the failing file and propose a fix.
```

Only a skill's one-line `description` sits in context until it is invoked (with
`/skill <name>` in `chat`), when its full body loads. List/show with
[`revenant skills`](cli.md#skills).

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
| `--no-graph` | off | Skip building the code graph. |
| `--no-color` | off | Disable colored output. |

See the [CLI reference](cli.md) for subcommand-specific flags (`loop`, `undo`,
`mcp`, `skills`, `resume`).

## Data directory layout

| Path | Contents |
|------|----------|
| `.aibot/profiles.json` | Model roles and overrides. |
| `.aibot/conversations.sqlite3` | Local conversation history. |
| `.aibot/checkpoints.json` | File-snapshot undo store (non-git workspaces). |
| `.aibot/sessions/*.json` | Saved sessions for `revenant resume`. |
| `.revenant/skills/*/SKILL.md` | Project skills. |
| `refs/revenant/undo/*` | Git-native undo snapshots (git workspaces; git refs, not files). |

!!! warning "CWD-relative"
    `.aibot/` is resolved from the current working directory. Run from a
    consistent location (e.g. the repo root).

---

## Next steps

- [Configuration](../configuration.md)
- [CLI reference](cli.md)
- [Configure model routing](../guides/model-routing.md)
