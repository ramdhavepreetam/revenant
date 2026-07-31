# ADR-0004 — MCP: external tools over the wire (Phase 3)

- **Status:** Proposed
- **Phase:** P3 · **F-slices:** F11.1 client, F11.2 adapter, F11.3 config, F11.4 subcommand
- **Date proposed:** 2026-07-30 · **Date implemented:** —
- **Depends on:** ADR-0003 (registry, loop), ADR-0002 (placement) · **Blocks:** ADR-0005 (skill-scoped tools), ADR-0008/0009 partially

## Context
Revenant ships a fixed tool set (`read_file`, `list_dir`, search, `write_file`,
`edit_file`, `run_bash`). Real workflows need more: git operations, a database,
a browser, a docs fetcher. The **Model Context Protocol (MCP)** is the emerging
standard for exposing tools to agents. Adding an MCP client lets Revenant call
tools it didn't ship with — **without touching the loop, approval gate, or
undo**, because MCP tools become ordinary `Tool` objects.

**Seams already cut:** `config.py` parses `[[mcp.servers]]` ("reserved for
F11"); `cli.py` registers an `mcp` subcommand stub.

**Offline invariant (ADR-0001):** only local or user-configured MCP servers.
stdio servers run as local subprocesses; HTTP/SSE servers must be user-specified
endpoints. No servers are contacted by default.

## Decision
Build a minimal MCP **client** in `nerva-agent`, an **adapter** that wraps each
remote tool as a registry `Tool`, wire it to the reserved config section, and
fill the `mcp` subcommand. Support **stdio** first (most common, fully local),
then **HTTP/SSE**.

Rejected: pulling in a heavyweight MCP SDK if it drags cloud deps or bloats the
offline install. Prefer a small dependency or a vendored minimal JSON-RPC client;
decide at implementation time, keeping the offline footprint small.

## Design detail

### F11.1 — MCP client (`nerva_agent/mcp_client.py`)
- `McpServerSpec{name, transport: "stdio"|"http", command/args | url, env}`.
- `McpClient`:
  - `connect()` — spawn subprocess (stdio) or open session (http); perform the
    MCP `initialize` handshake.
  - `list_tools() -> list[McpToolDef]` where `McpToolDef{name, description,
    input_schema (JSON Schema), server_name}`.
  - `call_tool(name, args) -> str` — JSON-RPC `tools/call`; flatten the result
    content to text for the observation.
  - `close()`.
- All calls time-bounded; any transport error raises a client error the adapter
  turns into a tool observation (never crashes the loop).

### F11.2 — Tool adapter (`nerva_agent/mcp_tools.py`)
- `mcp_tool_to_tool(client, tdef) -> Tool`:
  - `name` namespaced to avoid collisions: `f"{server}.{tool}"` (or config alias).
  - `params` derived from the JSON-Schema `properties`/`required` → `ToolParam`
    (scalar types only in v1, mirroring `ToolParam`'s scope; complex schemas
    documented in the description).
  - `run = lambda **kw: client.call_tool(tdef.name, kw)`.
  - `mutating=True` and thus `requires_approval=True` **by default** (we can't
    know a remote tool is safe). A server entry may declare `read_only: true`
    tools to relax this per tool name.
- `build_mcp_tools(specs) -> (list[Tool], list[McpClient])` — connect each
  server, collect tools, return clients so the CLI can close them on exit.

### F11.3 — Config wiring (`revenant_cli/config.py` + `cli.py`)
`[[mcp.servers]]` schema (read from `_raw_project` / `_raw_user`):
```toml
[[mcp.servers]]
name = "git"
transport = "stdio"
command = "mcp-server-git"
args = ["--repo", "."]
# optional:
read_only = ["git_log", "git_status"]   # tools that skip the approval gate
alias = "g"                               # optional name prefix override
```
- `load_config` already stashes the raw dicts; add a typed reader
  `mcp_server_specs(config) -> list[McpServerSpec]`.
- `_build_agent`: after building fs/edit/bash tools, if not read-only and specs
  exist, `tools += build_mcp_tools(specs)`; register clients for cleanup.
- Failure to connect a server logs a warning and is skipped (degrade, ADR-0001).

### F11.4 — `revenant mcp` subcommand (`cli.py`)
Replace the stub:
- `revenant mcp list` — show configured servers + their discovered tools.
- `revenant mcp test <name>` — connect, list tools, disconnect; report health.
- `revenant mcp add …` — append a server block to the project/user config
  (optional in v1; can start with list/test only).

## Failure & degradation
- A server that fails to connect: warn, continue without it.
- A tool call that errors/times out: return an error observation the model can
  recover from (same path as `ToolError`).
- Native-schema rendering must tolerate remote tools with schemas richer than
  `ToolParam` supports — degrade to a described string param, never crash.

## Test plan
`tests/test_mcp_client.py` — against a **fake in-process stdio server** (a small
script speaking MCP JSON-RPC):
- [ ] handshake + `list_tools` returns expected defs.
- [ ] `call_tool` round-trips args and flattens result to text.
- [ ] transport error → client error, not a crash.

`tests/test_mcp_tools.py`:
- [ ] `mcp_tool_to_tool` maps JSON-Schema → `ToolParam` (required/optional/type).
- [ ] adapted tool defaults to `requires_approval=True`; `read_only` list relaxes it.
- [ ] name namespacing / alias applied.

`tests/test_config.py` additions:
- [ ] `mcp_server_specs` parses `[[mcp.servers]]` from project and user layers.

`tests/test_cli.py` additions:
- [ ] `_build_agent` includes MCP tools in write mode, none in read-only.
- [ ] `revenant mcp list` renders configured servers (with a fake client).

## Acceptance criteria
- [ ] A `[[mcp.servers]]` git server entry yields working `git.*` tools in a run.
- [ ] Remote mutating tools hit the approval gate; `read_only` ones don't.
- [ ] `revenant mcp list/test` work; failures degrade gracefully.
- [ ] Offline footprint unchanged for users with no MCP servers configured.
- [ ] Tests green; ADR + README updated; F11 marked Implemented.

## Progress log
- 2026-07-30 — Proposed. Seams confirmed present in `config.py` and `cli.py`.
