# ADR-0021 — W-series Phase C: reach — routing & transport (W5/W6)

- **Status:** Accepted — implementation starting with W5
- **Phase:** W-series (0.6.0) Phase C · **W-slices:** W5 role-routed sub-agents ·
  W6 `mcp add` config writer + MCP HTTP/SSE transport
- **Date proposed:** 2026-08-01 · **Date implemented:** —
- **Depends on:** ADR-0018 (W-series strategy), ADR-0009 (sub-agents — W5 routes
  them), ADR-0004 (MCP — W6 extends transport + adds `add`) · **Relates to:**
  ADR-0001 (offline — routing is local model selection; W6 HTTP targets a local
  MCP server), ADR-0002 (placement: engine in `nerva-agent`, CLI writer in
  `revenant-cli`)

## Context
Phase C closes the deferred backlog with two self-contained, independent
extensions that reuse pre-cut seams — neither blocks the other, so C can ship (or
slip) without touching the streaming/refactor core. Verified in code:

- **Sub-agents clone the parent config verbatim.** `_make_subagent_factory`
  (`cli.py:624-651`) does `copy.copy(parent_args)` and rebuilds — the child uses
  the **same** model as the parent. Role routing already exists (`config_for_role`,
  `agent_router.py:69-110`, returns `None` on an unresolved role → clean fallback)
  but is never applied to a sub-agent. `build_spawn_tool`/`LoopFactory`
  (`subagent.py:35,86-149`) have no `role` concept.
- **MCP is stdio-only; no `mcp add`.** `McpClient.__init__` hard-rejects non-stdio
  (`mcp_client.py:76-77`); the JSON-RPC layer is cleanly abstracted behind
  `_request`/`_write`/`_read` (`mcp_client.py:155-196`), so a transport swap is
  contained. The config **reader** already carries `transport`/`url` through
  (`config.py:152,160`) and `McpServerSpec` models them (`mcp_client.py:48,52`) —
  only the client rejects them. The `mcp` subcommand is `list`/`test` only
  (`cli.py:297-304`); there is **no config writer** for `[[mcp.servers]]`.

## Decision
Ship W5 (role-routed sub-agents) then W6 (`mcp add` + HTTP/SSE). Both are additive
and degrade gracefully; the offline and approval invariants hold.

### W5 — role-routed sub-agents
- Add an optional `role` param to the `spawn_subagent` tool (`subagent.py:133-149`)
  and thread it through `LoopFactory` (`subagent.py:35`): `loop_factory(goal,
  tools, depth, role)`.
- In `_make_subagent_factory` (`cli.py:624-651`), when a `role` is given, resolve
  `config_for_role(role, base_url, profiles)` and hand the child **that** config
  instead of the copied parent one; on an unresolved role (`None`) or no role,
  fall back to today's verbatim-clone behavior (byte-identical). This enables a
  strong-planner / cheap-executor split within one run — the `keep_resident`
  capacity signal (`agent_capacity.py`) already exists to inform it.
- The `role` is documented in the tool so the model can pick one (e.g. `code`,
  `summary`). Unknown/empty role never errors — it just uses the parent config.

### W6 — `mcp add` config writer + MCP HTTP/SSE transport
- **`mcp add`.** A new `mcp add` subparser (`cli.py:~304`) + handler in `cmd_mcp`
  that **appends** an `[[mcp.servers]]` entry to the user (or `--project`) config
  file. No TOML writer exists today, so add a minimal one that reads the file,
  appends a well-formed `[[mcp.servers]]` block (name/command/args or
  transport+url), and writes it back — the existing `mcp_server_specs` reader then
  round-trips it. Refuses to clobber an existing server of the same name.
- **HTTP/SSE transport.** Relax `McpClient` (`mcp_client.py:75-196`) to speak HTTP
  when `spec.transport in ("http", "sse")`: keep the same JSON-RPC method calls
  (`initialize`/`tools/list`/`tools/call`) but route `_request` over an HTTP POST
  to `spec.url` (JSON-RPC body; SSE/streamed or plain-JSON response), instead of
  the subprocess pipe. `connect`/`close` become no-ops (or open/close a session).
  `list_tools`/`call_tool`/the adapter (`mcp_tools.py`) are **unchanged** — they
  only call `_request`. Offline invariant holds: `spec.url` is a user-configured
  **local** server, same class as Ollama (ADR-0001).

### Failure & degradation
- Unresolved/empty sub-agent role → parent config (never errors). `mcp add` on a
  duplicate name → clean error, no write. A malformed/corrupt config file → `mcp
  add` reports it rather than destroying it. HTTP transport connect/tool failure →
  the existing MCP error-to-observation path (a bad server never crashes the CLI,
  F11.3).

## Test plan (model-free / offline; CI bare `pytest`)
- **W5** `test_subagent.py`: `spawn_subagent(role="…")` builds a child whose config
  matches `config_for_role`; default (no role) preserves the verbatim-clone
  behavior; an unknown role falls back (never crashes); depth cap still enforced;
  the `role` threads through `LoopFactory` correctly.
- **W6 `mcp add`** `test_cli.py`/`test_config.py`: `mcp add` writes a well-formed
  `[[mcp.servers]]` entry that the existing `mcp_server_specs` reader parses back
  (round-trip); a duplicate name is refused; HTTP entry (transport+url) round-trips.
- **W6 HTTP transport** `test_mcp_client.py`: a fake **local** HTTP server (stdlib
  `http.server` in a thread, or a mocked opener) answers `initialize`/`tools/list`/
  `tools/call`; `McpClient` with `transport="http"` lists + calls a tool; a
  connect/HTTP error degrades to an `McpError` (adapter turns it into an
  observation). stdio path unchanged.

## Acceptance criteria
- [ ] `spawn_subagent(role=…)` runs the sub-agent under the role's model
      (`config_for_role`); no/unknown role = today's behavior (byte-identical).
- [ ] `revenant mcp add <name> …` appends a valid `[[mcp.servers]]` entry the
      reader round-trips; duplicate names refused; supports stdio and http entries.
- [ ] `McpClient` speaks HTTP/SSE when `transport` is http/sse (same JSON-RPC
      methods); the adapter + stdio path are unchanged; a bad server degrades.
- [ ] Suite green (bare `pytest`); ADR-0021 + README updated per slice.

## Open questions
- **Sub-agent role default:** leave default = parent config (explicit opt-in), or
  auto-`classify` the sub-goal to a role? Start explicit (smallest surface,
  deterministic tests); auto-classify can layer on later.
- **HTTP response shape:** MCP over HTTP may return a single JSON body or an SSE
  stream. Support plain JSON first (the common local case), accept an SSE
  `data:` frame if present — reuse the `stream_message` SSE parsing shape from W2.

## Progress log
- 2026-08-01 — Proposed + Accepted. Phase-C spec written before code (per ADR-0018
  / the series workflow). Seams re-verified: `_make_subagent_factory` verbatim
  clone + `config_for_role` (None-on-miss), `McpClient` stdio-enforce +
  `_request`/`_write`/`_read` abstraction + config reader already carrying
  transport/url. Implementation begins with W5.
