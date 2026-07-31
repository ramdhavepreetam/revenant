"""Adapt MCP server tools into registry `Tool`s (F11.2, ADR-0004).

An MCP tool is just a remote capability with a JSON-Schema for its arguments.
`mcp_tool_to_tool` wraps one as an ordinary `Tool` whose `run` dispatches over
the wire — so the agent loop, approval gate, and undo hooks need ZERO changes.

Safety default (ADR-0004): a remote tool is `mutating=True` (hence
`requires_approval=True`) unless the server's config entry lists it as read-only.
We can't know a remote tool is safe, so we make the human confirm by default.

Schema mapping is deliberately conservative, mirroring `ToolParam`'s v1 scope
(scalar types only). Anything richer — nested objects, arrays, enums — degrades
to a described string param rather than crashing; the full schema shape is noted
in the parameter description so the model still has guidance.
"""
from __future__ import annotations

from nerva_agent.agent_tools import Tool, ToolParam
from nerva_agent.mcp_client import McpClient, McpServerSpec, McpToolDef, McpError

# JSON-Schema scalar types ToolParam understands directly.
_SCALAR_TYPES = {"string", "integer", "number", "boolean"}


def _namespaced_name(tdef: McpToolDef, spec: McpServerSpec | None) -> str:
    """`<server-or-alias>.<tool>` so tools from different servers don't collide."""
    prefix = (spec.alias if spec and spec.alias else tdef.server_name)
    return f"{prefix}.{tdef.name}"


def _params_from_schema(input_schema: dict) -> list[ToolParam]:
    """Map a JSON-Schema object to a flat list of ToolParam.

    Non-scalar property types degrade to a "string" param whose description
    records the real JSON type, so we never crash on a schema we can't fully
    model. A missing or malformed schema yields no params (the tool takes none).
    """
    if not isinstance(input_schema, dict):
        return []
    props = input_schema.get("properties")
    if not isinstance(props, dict):
        return []
    required = set(input_schema.get("required") or [])
    params: list[ToolParam] = []
    for pname, pschema in props.items():
        pschema = pschema if isinstance(pschema, dict) else {}
        jtype = pschema.get("type", "string")
        desc = pschema.get("description", "")
        if jtype in _SCALAR_TYPES:
            ptype = jtype
        else:
            # Complex type: expose as string, but tell the model what it really is.
            ptype = "string"
            note = f"(JSON {jtype})" if jtype else "(JSON value)"
            desc = f"{desc} {note}".strip()
        params.append(ToolParam(
            name=pname, type=ptype, description=desc, required=pname in required,
        ))
    return params


def mcp_tool_to_tool(
    client: McpClient,
    tdef: McpToolDef,
    *,
    spec: McpServerSpec | None = None,
) -> Tool:
    """Wrap one MCP tool as a registry Tool bound to `client`.

    - Name is namespaced by server (or the server's config alias).
    - Args map from the tool's JSON-Schema (scalars direct; complex→string).
    - `mutating`/`requires_approval` default True; a `spec.read_only` entry
      naming this tool relaxes it to a parallel-safe read-only tool.
    - A transport/server error during a call becomes the observation text (the
      loop already treats a returned string as a recoverable result).
    """
    read_only = bool(spec and tdef.name in (spec.read_only or []))

    def run(**kwargs) -> str:
        try:
            return client.call_tool(tdef.name, kwargs)
        except McpError as exc:
            return f"ERROR: {exc}"

    description = tdef.description or f"MCP tool '{tdef.name}' from server '{tdef.server_name}'."

    return Tool(
        name=_namespaced_name(tdef, spec),
        description=description,
        params=_params_from_schema(tdef.input_schema),
        run=run,
        parallel_safe=read_only,
        mutating=not read_only,
        # For a read-only tool we must clear approval explicitly, since a mutating
        # tool auto-sets requires_approval in Tool.__post_init__.
        requires_approval=not read_only,
    )


def build_mcp_tools(
    specs: list[McpServerSpec],
) -> tuple[list[Tool], list[McpClient]]:
    """Connect every server in `specs`, collect their tools, return (tools, clients).

    Degrades per ADR-0001: a server that fails to connect or list tools is
    skipped with a warning; the rest still load. Returned clients must be
    `close()`d by the caller (the CLI does this on exit).
    """
    import sys

    tools: list[Tool] = []
    clients: list[McpClient] = []
    for spec in specs:
        try:
            client = McpClient(spec)
            client.connect()
        except McpError as exc:
            print(f"warning: MCP server '{spec.name}' unavailable: {exc}", file=sys.stderr)
            continue
        clients.append(client)
        try:
            for tdef in client.list_tools():
                tools.append(mcp_tool_to_tool(client, tdef, spec=spec))
        except McpError as exc:
            print(f"warning: MCP server '{spec.name}' tools/list failed: {exc}", file=sys.stderr)
    return tools, clients
