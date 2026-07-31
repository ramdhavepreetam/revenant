"""Minimal Model Context Protocol (MCP) client — stdio transport (F11.1, ADR-0004).

Lets the Revenant agent call tools exposed by an MCP server it didn't ship with
(git, a database, a browser, ...). The client speaks MCP's JSON-RPC 2.0 over a
subprocess's stdin/stdout — no third-party SDK, stdlib only, so the offline
footprint stays zero (ADR-0001). Only local subprocess (stdio) servers are
supported here; an HTTP/SSE transport is a later addition.

Shape (see ADR-0004):
    spec   = McpServerSpec(name="git", transport="stdio",
                           command="mcp-server-git", args=["--repo", "."])
    client = McpClient(spec); client.connect()
    for tdef in client.list_tools():
        ... adapt tdef into a registry Tool (see mcp_tools.py) ...
    client.close()

Every network/transport failure raises `McpError`; callers (the tool adapter)
turn that into a recoverable observation so a bad server never crashes the loop.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any

# MCP protocol version this client advertises in `initialize`.
PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "revenant", "version": "0.1.0"}

# Default per-request timeout (seconds). A server that doesn't answer in time is
# treated as a transport error rather than hanging the agent.
DEFAULT_TIMEOUT = 30.0


class McpError(RuntimeError):
    """Any MCP transport / protocol / server error. The tool adapter converts
    this into an observation the model can recover from — never a crash."""


@dataclass
class McpServerSpec:
    """How to reach one MCP server. `transport="stdio"` spawns `command args`."""

    name: str
    transport: str = "stdio"          # "stdio" (implemented) | "http" (future)
    command: str | None = None        # stdio: the server executable
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None            # http: reserved for the future transport
    # Optional policy carried from config (consumed by the adapter, not here):
    read_only: list[str] = field(default_factory=list)   # tool names that skip approval
    alias: str | None = None                             # name-prefix override


@dataclass
class McpToolDef:
    """A tool as advertised by a server's `tools/list`."""

    name: str
    description: str
    input_schema: dict[str, Any]      # JSON Schema for the tool's arguments
    server_name: str


class McpClient:
    """A JSON-RPC 2.0 client over one stdio MCP server subprocess.

    Not thread-safe for concurrent requests; the agent loop calls tools serially.
    A lock guards id allocation so a background reader can't race the writer.
    """

    def __init__(self, spec: McpServerSpec, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        if spec.transport != "stdio":
            raise McpError(f"{spec.name}: only the stdio transport is implemented")
        if not spec.command:
            raise McpError(f"{spec.name}: stdio server needs a 'command'")
        self.spec = spec
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._id = 0
        self._lock = threading.Lock()
        self._initialized = False

    # --- lifecycle ---------------------------------------------------------
    def connect(self) -> None:
        """Spawn the server and perform the MCP `initialize` handshake."""
        env = {**os.environ, **self.spec.env}
        try:
            self._proc = subprocess.Popen(
                [self.spec.command, *self.spec.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=env,
                text=True,
                bufsize=1,  # line-buffered
            )
        except (OSError, ValueError) as exc:
            raise McpError(f"{self.spec.name}: failed to start server: {exc}") from exc

        # Handshake: initialize -> notifications/initialized.
        self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": _CLIENT_INFO,
        })
        self._notify("notifications/initialized", {})
        self._initialized = True

    def close(self) -> None:
        """Terminate the server subprocess. Idempotent, never raises."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        for step in (proc.terminate, proc.kill):
            if proc.poll() is not None:
                break
            try:
                step()
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                continue

    # --- MCP methods -------------------------------------------------------
    def list_tools(self) -> list[McpToolDef]:
        """Return the server's advertised tools (MCP `tools/list`)."""
        result = self._request("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        defs: list[McpToolDef] = []
        for t in tools:
            if not isinstance(t, dict) or "name" not in t:
                continue
            defs.append(McpToolDef(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema") or t.get("input_schema") or {},
                server_name=self.spec.name,
            ))
        return defs

    def call_tool(self, name: str, args: dict[str, Any]) -> str:
        """Invoke a tool (MCP `tools/call`) and flatten its result to text."""
        result = self._request("tools/call", {"name": name, "arguments": args or {}})
        return _flatten_content(result)

    # --- JSON-RPC plumbing -------------------------------------------------
    def _next_id(self) -> int:
        with self._lock:
            self._id += 1
            return self._id

    def _request(self, method: str, params: dict) -> Any:
        """Send a request and block for its response. Raises McpError on failure."""
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise McpError(f"{self.spec.name}: server not connected")
        rid = self._next_id()
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        # Read lines until we get the response with our id (skip notifications
        # and unrelated messages a server may interleave).
        while True:
            msg = self._read()
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                err = msg["error"]
                raise McpError(f"{self.spec.name}: {method} failed: "
                               f"{err.get('message', err)}")
            return msg.get("result")

    def _notify(self, method: str, params: dict) -> None:
        """Fire-and-forget JSON-RPC notification (no id, no response)."""
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, obj: dict) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps(obj) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise McpError(f"{self.spec.name}: write failed: {exc}") from exc

    def _read(self) -> dict:
        assert self._proc is not None and self._proc.stdout is not None
        line = self._proc.stdout.readline()
        if line == "":  # EOF: the server died
            raise McpError(f"{self.spec.name}: server closed the connection")
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            raise McpError(f"{self.spec.name}: invalid JSON from server: {exc}") from exc
        if not isinstance(msg, dict):
            raise McpError(f"{self.spec.name}: expected a JSON object, got {type(msg).__name__}")
        return msg


def _flatten_content(result: Any) -> str:
    """Turn an MCP tool result into a single observation string.

    MCP results look like {"content": [{"type": "text", "text": "..."}], ...}.
    We concatenate text parts; non-text parts are summarized so the model still
    sees that something came back.
    """
    if not isinstance(result, dict):
        return str(result)
    content = result.get("content")
    if not isinstance(content, list):
        # Some servers return a bare structured result; show it as JSON.
        return json.dumps(result, ensure_ascii=False)
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            parts.append(str(item))
        elif item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        else:
            parts.append(f"[{item.get('type', 'unknown')} content]")
    text = "\n".join(p for p in parts if p)
    if result.get("isError"):
        return f"ERROR: {text}" if text else "ERROR: (tool reported an error)"
    return text
