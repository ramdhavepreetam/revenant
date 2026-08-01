# CLI reference

Complete reference for the `revenant` command and its subcommands.

---

## Synopsis

```bash
revenant <command> [OPTIONS]
revenant "GOAL"              # shorthand for `revenant run "GOAL"`
```

Revenant runs a tool-calling agent loop against your local Ollama model,
dispatching read-only tools freely and mutating tools behind approval. A bare
`revenant "GOAL"` is treated as `revenant run "GOAL"` for backward compatibility.

!!! example "Typical invocation"
    ```bash
    revenant --workspace ~/proj --model qwen2.5:7b "where is auth handled?"
    ```

## Commands

| Command | Purpose |
|---------|---------|
| [`run`](#run) | Run a single goal to completion (one-shot). |
| [`chat`](#chat) | Interactive multi-turn session (REPL). |
| [`loop`](#loop) | Run a goal **autonomously** until a condition is met. |
| [`undo`](#undo) | Revert changes the agent made. |
| [`mcp`](#mcp) | Inspect configured MCP servers and their tools. |
| [`skills`](#skills) | List and show reusable `SKILL.md` workflows. |
| [`doctor`](#doctor) | Check Ollama + model setup; show resolved config. |
| [`models`](#models) | List models pulled on the Ollama server. |
| [`resume`](#resume) | Resume a saved session. |
| `config` | Show/edit configuration *(coming soon)*. |

## Common options

Shared by `run`, `chat`, `loop`, and `resume`:

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--workspace PATH` | path | `.` (cwd) | Repo root the agent may read. Tools are confined to it. |
| `--model NAME` | string | `code` role | Override the coding model for this run. |
| `--base-url URL` | string | `http://localhost:11434` | Ollama server base URL. |
| `--max-steps N` | int | `0` (auto) | Hard cap on loop iterations. `0` = derive from RAM. |
| `--max-context-tokens N` | int | `0` (auto) | Context budget before compaction. `0` = derive from RAM. |
| `--read-only` | flag | off | Disable mutating tools (`write`, `edit`, `bash`). |
| `--no-native-tools` | flag | off | Force the prompt-based action protocol instead of native `tool_calls`. |
| `--yolo` | flag | off | Auto-approve mutating tools. **Destructive commands are still blocked.** |
| `--no-graph` | flag | off | Skip building the [code graph](tools.md#code-graph-tools) (`defn_of`, `who_calls`, …). |
| `--no-color` | flag | off | Disable ANSI color in output. |

!!! warning "`--yolo` safety"
    `--yolo` writes files and runs shell commands without prompting. Destructive
    footguns (`rm -rf /`, fork bombs, `mkfs`) remain hard-blocked, but ordinary
    damaging commands are not. Use only in disposable workspaces, or rely on
    [`revenant undo`](#undo). See [Approvals & safety](../guides/approvals-and-safety.md).

---

## `run`

```bash
revenant run "GOAL" [common options]
```

Runs one goal to a final answer or the step cap, printing tool calls and
observations as they happen. This is the default command.

```bash
revenant run --read-only "Explain the auth flow."
revenant "Add input validation to create_user()."   # shorthand
```

Pass `--skill <name>` to run a [skill's](#skills) procedure as the goal (the goal
argument becomes optional, and the agent's tools are scoped to the skill):

```bash
revenant run --skill run-tests            # run the skill's body
revenant run --skill review-diff "focus on error handling"   # skill + extra goal
```

Pass `--plan` to decompose a larger goal into small steps and run them **one at a
time**, each checked before the next — so a local model isn't asked to hold the
whole task in its head:

```bash
revenant run --plan "add pagination to the users endpoint and test it"
```

---

## `chat`

```bash
revenant chat [common options]
```

An interactive REPL. One agent is built once; each line you type continues the
same conversation. The session is **auto-saved** so you can [`resume`](#resume) it.

REPL commands:

| Command | Effect |
|---------|--------|
| `/exit`, `/quit` | Leave the session. |
| `/reset` | Clear the conversation context. |
| `/help` | Show the command list. |
| `/skills` | List available [skills](#skills). |
| `/skill <name>` | Load a skill's procedure and run it as the next turn. |

---

## `loop`

```bash
revenant loop "GOAL" [common options] [loop options]
```

Runs a goal **autonomously**, iterating until a success condition is met or a
budget is exhausted. Autonomy is always bounded — there is no run-forever mode.

| Flag | Default | Description |
|------|---------|-------------|
| `--autonomous` | off | Run unattended: auto-approve edits within the budget. A checkpoint is taken before each iteration so `undo` can step back a whole round. |
| `--until CMD` | — | Success when the shell command `CMD` exits `0`. |
| `--until-tests` | off | Success when the test command (`--test-cmd`) exits `0`. |
| `--until-file PATH` | — | Success when `PATH` exists. |
| `--test-cmd CMD` | `pytest -q` | Command used by `--until-tests`. |
| `--max-iterations N` | `10` | Stop after N iterations. |
| `--max-wall SECONDS` | `0` | Stop after this many seconds of wall clock (`0` = no limit). |
| `--dry-run` | off | Preview: forces read-only, so the agent narrates its plan without writing. |
| `--watch GLOB` | — | Re-run the whole loop whenever a workspace file matching `GLOB` changes (mtime poll; respects ignore globs). `Ctrl-C` to stop. |
| `--watch-interval SECONDS` | `1.0` | Poll interval for `--watch`. |

```bash
# Iterate until the test suite passes, unattended, checkpointing each round.
revenant loop --autonomous --until-tests "make the failing tests pass"

# Preview what an autonomous run would do, with zero disk writes.
revenant loop --dry-run --until-file out.txt "generate out.txt"

# Re-run the loop every time a source file changes.
revenant loop --watch 'src/**/*.py' --until-tests "keep the tests green"
```

Exit code `0` means the condition was met; `3` means a budget was hit first (the
partial run is saved — the command prints a `revenant resume <id>` hint).

---

## `undo`

```bash
revenant undo [--all] [--workspace PATH] [--no-color]
```

Reverts changes the agent made in a workspace. Two backends, chosen automatically:

- **Git-native** (when the workspace is a git repo): restores the whole working
  tree from a shadow-commit, including files a `run_bash` command created.
  Snapshots live under `refs/revenant/undo/*` and never touch your branches.
- **File-snapshot** (non-git workspaces): restores files `write_file`/`edit_file`
  touched.

`--all` reverts every recorded change; the default reverts only the most recent.

```bash
revenant undo          # revert the last change
revenant undo --all    # revert everything from the run
```

---

## `mcp`

```bash
revenant mcp list [--workspace PATH]
revenant mcp test <name> [--workspace PATH]
```

Inspect [Model Context Protocol](../guides/extending.md#mcp-servers) servers
configured via `[[mcp.servers]]` in your `.revenant.toml`.

- `mcp list` — show configured servers and the tools each exposes.
- `mcp test <name>` — connect to one server and report its health.

```bash
revenant mcp list
revenant mcp test git
```

---

## `skills`

```bash
revenant skills list [--workspace PATH]
revenant skills show <name> [--workspace PATH]
```

Inspect reusable [skills](../guides/extending.md#skills) — `SKILL.md` workflows
discovered from `.revenant/skills/` (project) and `~/.config/revenant/skills/`
(user).

- `skills list` — all discovered skills with their descriptions.
- `skills show <name>` — a skill's full procedure body.

Invoke a skill inside `chat` with `/skill <name>`.

---

## `doctor`

```bash
revenant doctor [--base-url URL] [--model NAME] [--workspace PATH]
```

Diagnoses your setup: whether Ollama is reachable, which models are pulled, the
config that a run would resolve (model / base URL / workspace), and whether you're
ready to run. Exit `0` if healthy, `1` otherwise (scriptable). Run this first if
anything isn't working.

## `models`

```bash
revenant models [--base-url URL]
```

Lists the models pulled on the Ollama server, marking the one your `code` role
resolves to. If Ollama isn't reachable, it says so with the fix.

!!! tip "First-run setup"
    Every run does a **pre-flight** check first: if Ollama isn't running or the
    model isn't pulled, it stops with the exact command to fix it (`ollama serve`
    / `ollama pull …`) — and offers a picker of your pulled models. Bypass with
    `--skip-preflight`. Point at a non-default server with `--base-url` or the
    `OLLAMA_HOST` environment variable.

## `resume`

```bash
revenant resume [SESSION_ID] [common options]
revenant resume list
```

Resume a saved session — the transcript is re-hydrated into a REPL so the agent
keeps its prior context. Sessions are saved automatically by `chat` and `loop`
under `<workspace>/.aibot/sessions/`.

- `resume list` — recent sessions for the workspace (newest first).
- `resume` — resume the most recent session.
- `resume <id>` — resume a specific session.

```bash
revenant resume list
revenant resume            # continue where you left off
```

---

## Next steps

- [Tools reference](tools.md) — every tool the agent can call, including the code graph.
- [Configuration reference](config.md) — settings, `[[mcp.servers]]`, and `SKILL.md`.
- [Extending Revenant](../guides/extending.md) — MCP, skills, loops, and the code graph.
