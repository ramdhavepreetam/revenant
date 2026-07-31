"""Tests for the stdio MCP client (F11.1, ADR-0004).

Drives McpClient against a *fake in-process MCP server*: a tiny Python script
(written to tmp_path) that speaks MCP JSON-RPC 2.0 over stdin/stdout. No network,
no real server binary — just the protocol contract.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from nerva_agent.mcp_client import (
    McpClient, McpServerSpec, McpError, _flatten_content,
)


# A minimal, correct MCP server: handshake, tools/list, tools/call (echo).
_GOOD_SERVER = textwrap.dedent('''
    import json, sys
    def send(obj): sys.stdout.write(json.dumps(obj) + "\\n"); sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        msg = json.loads(line)
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params", {})
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid,
                  "result": {"protocolVersion": "2024-11-05",
                             "serverInfo": {"name": "fake", "version": "1"},
                             "capabilities": {}}})
        elif method == "notifications/initialized":
            pass  # notification: no reply
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": "echo", "description": "Echo text back.",
                 "inputSchema": {"type": "object",
                                 "properties": {"text": {"type": "string"}},
                                 "required": ["text"]}}]}})
        elif method == "tools/call":
            args = params.get("arguments", {})
            send({"jsonrpc": "2.0", "id": mid,
                  "result": {"content": [{"type": "text",
                                          "text": "echo: " + str(args.get("text", ""))}]}})
        else:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": "method not found"}})
''')

# A server that dies immediately (EOF) to exercise transport-error handling.
_DYING_SERVER = "import sys; sys.exit(0)"


def _spec(tmp_path: Path, source: str, name: str = "fake") -> McpServerSpec:
    script = tmp_path / f"{name}_server.py"
    script.write_text(source)
    return McpServerSpec(name=name, transport="stdio",
                         command=sys.executable, args=[str(script)])


@pytest.fixture
def good_client(tmp_path):
    client = McpClient(_spec(tmp_path, _GOOD_SERVER))
    client.connect()
    yield client
    client.close()


# --- handshake + discovery ---------------------------------------------------

def test_connect_and_list_tools(good_client):
    defs = good_client.list_tools()
    assert len(defs) == 1
    d = defs[0]
    assert d.name == "echo"
    assert d.description == "Echo text back."
    assert d.server_name == "fake"
    assert d.input_schema["required"] == ["text"]


def test_call_tool_round_trips_and_flattens(good_client):
    out = good_client.call_tool("echo", {"text": "hello"})
    assert out == "echo: hello"


def test_call_unknown_method_raises_mcp_error(good_client):
    # tools/call on a name the server errors on still returns text; but a bad
    # JSON-RPC method surfaces as McpError via _request.
    with pytest.raises(McpError):
        good_client._request("no/such/method", {})


# --- transport / lifecycle ---------------------------------------------------

def test_dead_server_raises_on_handshake(tmp_path):
    client = McpClient(_spec(tmp_path, _DYING_SERVER, name="dead"))
    with pytest.raises(McpError):
        client.connect()


def test_close_is_idempotent(good_client):
    good_client.close()
    good_client.close()  # second call must not raise


def test_non_stdio_transport_rejected():
    with pytest.raises(McpError):
        McpClient(McpServerSpec(name="x", transport="http", url="http://y"))


def test_stdio_without_command_rejected():
    with pytest.raises(McpError):
        McpClient(McpServerSpec(name="x", transport="stdio"))


# --- content flattening (pure) ----------------------------------------------

def test_flatten_text_parts():
    result = {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
    assert _flatten_content(result) == "a\nb"


def test_flatten_marks_non_text_parts():
    result = {"content": [{"type": "image", "data": "..."}]}
    assert _flatten_content(result) == "[image content]"


def test_flatten_error_result():
    result = {"isError": True, "content": [{"type": "text", "text": "boom"}]}
    assert _flatten_content(result) == "ERROR: boom"


def test_flatten_structured_result_falls_back_to_json():
    result = {"value": 42}
    assert "42" in _flatten_content(result)
