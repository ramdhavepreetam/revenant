# ADR-0004 — MCP: external tools over the wire (Phase 3)

- **Status:** Implemented
- **Phase:** P3 · **F-slices:** F11.1 client, F11.2 adapter, F11.3 config, F11.4 subcommand
- **Date proposed:** 2026-07-30 · **Date implemented:** 2026-07-30
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

## Test plan — DONE (29 tests, 2026-07-30)
`tests/test_mcp_client.py` (11) — against a **fake in-process stdio server**:
- [x] handshake + `list_tools` returns expected defs.
- [x] `call_tool` round-trips args and flattens result to text.
- [x] transport error / dead server / bad JSON-RPC → `McpError`, not a crash.
- [x] non-stdio / no-command specs rejected; `close()` idempotent.
- [x] `_flatten_content`: text parts, non-text marker, error result, JSON fallback.

`tests/test_mcp_tools.py` (12):
- [x] `mcp_tool_to_tool` maps JSON-Schema → `ToolParam` (required/optional/type).
- [x] complex types degrade to string with a type note; empty schema → no params.
- [x] adapted tool defaults to `requires_approval=True`; `read_only` relaxes it.
- [x] name namespacing / alias applied; error→observation; build skips bad server.

`tests/test_config.py` (4):
- [x] `mcp_server_specs` parses `[[mcp.servers]]`; project overrides user by name;
      entries without a name skipped with a warning.

`tests/test_cli.py` (6):
- [x] `_build_agent` wiring unaffected (before_tool still correct).
- [x] `revenant mcp list` / `test` render servers+tools against a real fake server.
- [x] unknown server errors; no-servers case; **flag-ordering regression guard**.

## Acceptance criteria
- [x] A `[[mcp.servers]]` git server entry yields working `git.*` tools
      (verified end-to-end via the real CLI entrypoint + fake stdio server).
- [x] Remote mutating tools hit the approval gate (`requires_approval=True`);
      `read_only` ones are parallel-safe and skip it.
- [x] `revenant mcp list/test` work; a failing server degrades with a warning.
- [x] Offline footprint unchanged (stdlib-only client; no MCP servers → no-op).
- [x] Tests green (217 → 247); ADR + README updated; F11 marked Implemented.

## Implementation notes (what actually shipped)
- **Stdlib only.** The client (`nerva_agent/mcp_client.py`) speaks MCP JSON-RPC
  2.0 over a subprocess's stdin/stdout with `subprocess` + `json` — no MCP SDK,
  no new dependency, offline footprint unchanged. HTTP/SSE transport is stubbed
  in `McpServerSpec` but deferred.
- **Client cleanup.** Connected clients are stashed on `loop._mcp_clients` and
  closed via `_close_mcp(loop)` in a `try/finally` around `cmd_run` / the
  `cmd_chat` REPL, so server subprocesses don't leak.
- **Deviation from the sketch:** the `mcp add` action was **not** built in v1
  (list/test only), matching the ADR's "optional in v1" note. Adding a server is
  still a hand-edit of `.revenant.toml`.
- **Bug found by end-to-end testing (not unit tests):** argparse rejected
  parent optionals placed *after* the sub-action (`mcp list --workspace X`).
  Fixed by adding the flags to each sub-action parser with `default=SUPPRESS`
  so a parent-level value isn't clobbered; covered by a regression test.

## Progress log
- 2026-07-30 — Proposed. Seams confirmed present in `config.py` and `cli.py`.
- 2026-07-30 — **Implemented.** F11.1–F11.4 built and tested (29 new tests,
  suite 217 → 247). Verified end-to-end through the real CLI against a fake
  stdio MCP server (`mcp list`, `mcp test`, bare `mcp`). Status → Implemented.
  `mcp add` deferred; HTTP transport deferred. Next phase: P4 Skills (ADR-0005).
