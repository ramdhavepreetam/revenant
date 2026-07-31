"""Tests for the MCP tool adapter (F11.2, ADR-0004).

Covers JSON-Schema -> ToolParam mapping, name namespacing/alias, the
approval-by-default policy with read_only relaxation, error-to-observation
behavior, and build_mcp_tools' degrade-on-failure. Uses a fake client so no
subprocess is spawned.
"""
from __future__ import annotations

import pytest

from nerva_agent.mcp_client import McpToolDef, McpServerSpec, McpError
from nerva_agent.mcp_tools import (
    mcp_tool_to_tool, build_mcp_tools, _params_from_schema,
)


class _FakeClient:
    """Stands in for McpClient: records calls, returns a canned string or raises."""

    def __init__(self, result="ok", raises=False):
        self.result = result
        self.raises = raises
        self.calls = []

    def call_tool(self, name, args):
        self.calls.append((name, args))
        if self.raises:
            raise McpError("boom")
        return self.result


def _tdef(name="echo", schema=None, server="git"):
    return McpToolDef(
        name=name,
        description=f"{name} desc",
        input_schema=schema if schema is not None else {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "the text"}},
            "required": ["text"],
        },
        server_name=server,
    )


# --- schema mapping ----------------------------------------------------------

def test_scalar_schema_maps_to_toolparams():
    schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "count": {"type": "integer"},
            "ratio": {"type": "number"},
            "flag": {"type": "boolean"},
        },
        "required": ["text", "count"],
    }
    params = _params_from_schema(schema)
    by_name = {p.name: p for p in params}
    assert by_name["text"].type == "string" and by_name["text"].required
    assert by_name["count"].type == "integer" and by_name["count"].required
    assert by_name["ratio"].type == "number" and not by_name["ratio"].required
    assert by_name["flag"].type == "boolean" and not by_name["flag"].required


def test_complex_type_degrades_to_string_with_note():
    schema = {"type": "object",
              "properties": {"items": {"type": "array", "description": "a list"}}}
    (p,) = _params_from_schema(schema)
    assert p.type == "string"
    assert "array" in p.description  # real type recorded for the model


def test_missing_or_empty_schema_yields_no_params():
    assert _params_from_schema({}) == []
    assert _params_from_schema({"type": "object"}) == []
    assert _params_from_schema(None) == []  # type: ignore[arg-type]


# --- naming ------------------------------------------------------------------

def test_name_is_namespaced_by_server():
    tool = mcp_tool_to_tool(_FakeClient(), _tdef(server="git"))
    assert tool.name == "git.echo"


def test_alias_overrides_server_prefix():
    spec = McpServerSpec(name="git", transport="stdio", command="x", alias="g")
    tool = mcp_tool_to_tool(_FakeClient(), _tdef(server="git"), spec=spec)
    assert tool.name == "g.echo"


# --- approval policy ---------------------------------------------------------

def test_remote_tool_requires_approval_by_default():
    tool = mcp_tool_to_tool(_FakeClient(), _tdef())
    assert tool.mutating is True
    assert tool.requires_approval is True
    assert tool.parallel_safe is False


def test_read_only_entry_relaxes_approval():
    spec = McpServerSpec(name="git", transport="stdio", command="x",
                         read_only=["status"])
    tool = mcp_tool_to_tool(_FakeClient(), _tdef(name="status"), spec=spec)
    assert tool.mutating is False
    assert tool.requires_approval is False
    assert tool.parallel_safe is True


# --- invocation --------------------------------------------------------------

def test_run_dispatches_to_client():
    client = _FakeClient(result="echo: hi")
    tool = mcp_tool_to_tool(client, _tdef())
    out = tool.invoke({"text": "hi"})
    assert out == "echo: hi"
    assert client.calls == [("echo", {"text": "hi"})]


def test_client_error_becomes_observation_not_crash():
    tool = mcp_tool_to_tool(_FakeClient(raises=True), _tdef())
    out = tool.invoke({"text": "hi"})
    assert out.startswith("ERROR:")


# --- build_mcp_tools degrade-on-failure --------------------------------------

def test_build_skips_unreachable_server(monkeypatch, capsys):
    # A spec whose command can't start -> connect() raises inside build; skipped.
    bad = McpServerSpec(name="ghost", transport="stdio",
                        command="definitely-not-a-real-binary-xyz")
    tools, clients = build_mcp_tools([bad])
    assert tools == [] and clients == []
    assert "unavailable" in capsys.readouterr().err
