# CLI reference

Complete reference for the `revenant` command.

---

## Synopsis

```bash
revenant [OPTIONS] "GOAL"
```

Revenant runs an agent loop toward `GOAL` against the model and workspace you
specify, dispatching read-only tools freely and mutating tools behind approval.

!!! example "Typical invocation"
    ```bash
    revenant --workspace ~/proj --model qwen2.5:7b "where is auth handled?"
    ```

## Positional argument

| Argument | Required | Description |
|----------|----------|-------------|
| `goal` | Yes | What you want the agent to do, in natural language. |

## Options

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
| `--no-color` | flag | off | Disable ANSI color in output. |

!!! warning "`--yolo` safety"
    `--yolo` writes files and runs shell commands without prompting. Destructive
    footguns (`rm -rf /`, fork bombs, `mkfs`) remain hard-blocked, but ordinary
    damaging commands are not. Use only in disposable workspaces. See
    [Approvals & safety](../guides/approvals-and-safety.md).

## Exit behavior

The loop ends when the model produces a final answer or reaches `--max-steps`.
Revenant prints the final answer to stdout; tool calls and observations are
printed as the loop runs.

## Examples

=== "Explore (read-only)"

    ```bash
    revenant --read-only "Explain the auth flow."
    ```

=== "Make an approved change"

    ```bash
    revenant --model qwen2.5-coder:7b "Add input validation to create_user()."
    ```

=== "Point at a subdirectory"

    ```bash
    revenant --workspace ./services/api --read-only "List the public endpoints."
    ```

=== "Remote Ollama server"

    ```bash
    revenant --base-url http://192.168.1.50:11434 "Summarize this repo."
    ```

=== "Force prompt-based tools"

    ```bash
    revenant --no-native-tools --model my-gguf-model "..."
    ```

---

## Next steps

- [Tools reference](tools.md) — the tools the agent can call.
- [Configuration reference](config.md) — settings and environment variables.
- [Configure model routing](../guides/model-routing.md)
