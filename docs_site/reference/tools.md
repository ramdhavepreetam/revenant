# Tools reference

The tools the Revenant agent can call during a run. Read-only tools run freely;
mutating tools are approval-gated (see
[Approvals & safety](../guides/approvals-and-safety.md)).

All tools are **path-confined to the workspace** set by `--workspace`.

---

## Read-only tools

| Tool | Purpose | Key arguments |
|------|---------|---------------|
| `read` | Read a file's contents. | `path` |
| `list` | List a directory. | `path` |
| `glob` | Find files by glob pattern. | `pattern` |
| `grep` | Search file contents by pattern. | `pattern`, `path` |

!!! example "Read a file"
    ```json
    {"tool": "read", "args": {"path": "src/app.py"}}
    ```

## Mutating tools (approval-gated)

| Tool | Purpose | Key arguments |
|------|---------|---------------|
| `write` | Create or overwrite a file. | `path`, `content` |
| `edit` | Apply a find/replace edit to a file. | `path`, `old`, `new` |
| `bash` | Run a shell command in the workspace. | `command` |

!!! example "Propose an edit"
    ```json
    {"tool": "edit", "args": {"path": "src/app.py", "old": "def f():", "new": "def f() -> None:"}}
    ```

!!! warning "Every mutating call is gated"
    `write`, `edit`, and `bash` prompt for approval unless you pass `--yolo`.
    Destructive shell commands are hard-blocked in all modes.

## Code-graph tools

Revenant indexes your workspace into a symbol/dependency graph (stdlib `ast` for
Python; a regex fallback for other languages) and exposes read-only tools so the
agent reasons about the repo **by structure, not text search**. Skip indexing
with `--no-graph`.

| Tool | Purpose | Key arguments |
|------|---------|---------------|
| `defn_of` | Where a symbol is defined (file:line + snippet). | `symbol` |
| `who_calls` | Real call sites of a function/method. | `symbol` |
| `neighbors` | A file's imports, importers, and defined symbols. | `path` |
| `impact_of` | Blast radius: transitive callers by hop distance. | `symbol` |

!!! example "Ask what calls a function"
    ```json
    {"tool": "who_calls", "args": {"symbol": "dispatch"}}
    ```
    Returns the actual call sites from the graph — not a grep guess.

The graph also powers **structure-aware context** (a symbol's definition plus its
immediate callers surfaced together) and supports **incremental re-indexing** of
a single changed file, so it stays accurate as code evolves.

## Sub-agent tool (approval-gated)

| Tool | Purpose | Key arguments |
|------|---------|---------------|
| `spawn_subagent` | Delegate a self-contained sub-goal to a fresh, budgeted agent; returns a short summary. | `goal`, `tools` *(optional)* |

The sub-agent runs its own loop with its own tool scope and budget, then reports
back a summary — keeping the parent's context small. Recursion is depth-capped.

## MCP tools

When you configure [MCP servers](config.md#mcp-servers), each remote tool appears
in the registry namespaced as `<server>.<tool>` (e.g. `git.status`). Remote tools
are approval-gated by default; a server entry may mark specific tools `read_only`
to relax that. See [Extending Revenant](../guides/extending.md#mcp-servers).

## The tool-call protocol

Revenant accepts tool calls in two forms and auto-detects which a model supports:

=== "Native tool_calls"

    Structured calls emitted by models with a tool template. Preferred.

=== "Prompt-based action fallback"

    A fenced block for models without a tool template:

    ````text
    ```action
    {"tool": "read", "args": {"path": "src/app.py"}}
    ```
    ````

    The parser tolerates single quotes, mixed quotes (via `ast.literal_eval`),
    `arguments`/`args` aliases, and double-wrapped payloads — so imperfect 8B
    output still parses.

Force the fallback with `--no-native-tools`. Detection is cached per model; after
re-pulling a model you can clear the cache so it re-probes.

---

## Related engine modules

The engine that implements these tools lives in `nerva-agent`:

| Module | Responsibility |
|--------|----------------|
| `agent_loop` | The main loop and context compaction. |
| `agent_tools` | Tool registry and dispatch. |
| `agent_protocol` | Parses native and prompt-based tool calls. |
| `agent_fs_tools` | Read-only filesystem tools. |
| `agent_edit_tools` | `write` / `edit`. |
| `agent_bash_tool` | `bash`, with the destructive-command block list. |
| `agent_router` | Per-turn model routing. |
| `agent_capacity` | RAM-based `max_steps` / context tuning. |
| `agent_native_tools` | Per-model native-tool detection (probe + cache). |
| `mcp_client` / `mcp_tools` | MCP client (stdio JSON-RPC) and remote-tool adapter. |
| `skills` | `SKILL.md` discovery, progressive disclosure, tool scoping. |
| `loop_driver` | Iterate-until-done driver for `revenant loop`. |
| `code_graph.indexer` / `code_graph.tools` | Symbol/dependency index + retrieval tools. |
| `subagent` | The `spawn_subagent` tool. |

---

## Next steps

- [CLI reference](cli.md)
- [Architecture](../architecture.md) — how the loop dispatches tools.
- [Approvals & safety](../guides/approvals-and-safety.md)
