"""Configuration loading for revenant (F2).

Layered config so users don't have to pass the same flags every invocation. A
project `.revenant.toml` (found by walking up from the workspace, like git finds
.git) sets repo-specific defaults; a user `~/.config/revenant/config.toml` sets
personal defaults. Precedence, highest first:

    command-line flag  >  project .revenant.toml  >  user config  >  built-in default

Uses the stdlib `tomllib` (Python 3.11+), so no new dependency. Unknown keys are
ignored; a malformed file is skipped with a warning rather than crashing the CLI —
config should never make the tool unusable.

Recognized keys (all optional):
    base_url          str    model server URL
    model             str    coding model override
    read_only         bool
    yolo              bool
    max_steps         int
    max_context_tokens int
    ignore            list[str]  glob patterns (reserved for F7)
    [[mcp.servers]]   table array (reserved for F11)
"""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from typing import Any

PROJECT_FILENAME = ".revenant.toml"

# Scalar keys that participate in the layered merge. `ignore` and `mcp` are read
# through but not merged as scalars (they're consumed by later features).
_SCALAR_KEYS = (
    "base_url", "model", "read_only", "yolo", "max_steps", "max_context_tokens",
)


def _load_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file; return {} if missing or malformed (never raises)."""
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except FileNotFoundError:
        return {}
    except (tomllib.TOMLDecodeError, OSError) as exc:
        print(f"warning: ignoring malformed config {path}: {exc}", file=sys.stderr)
        return {}


def find_project_config(start: Path) -> Path | None:
    """Walk up from `start` looking for a project .revenant.toml (like .git)."""
    start = start.resolve()
    for directory in (start, *start.parents):
        candidate = directory / PROJECT_FILENAME
        if candidate.is_file():
            return candidate
    return None


def user_config_path() -> Path:
    """~/.config/revenant/config.toml (honoring XDG_CONFIG_HOME)."""
    import os

    base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "revenant" / "config.toml"


def _render_toml_scalar(value: Any) -> str:
    """Render a Python scalar as a TOML value (str/bool/int only — our scalars)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_scalar(key: str, value: Any, scope: str = "user",
                 workspace: "Path | None" = None) -> Path:
    """Upsert a top-level `key = value` in a config file (generalizes U2's model
    write; used by `config set`).

    scope="user" → ~/.config/revenant/config.toml; scope="project" →
    <workspace>/.revenant.toml. A minimal single-scalar upsert (read text, replace
    or append the top-level `key = ...` line, write back) — deliberately avoids a
    TOML-writer dependency. Returns the path written. Raises OSError on a failed
    write; ValueError on an unknown key.
    """
    if key not in _SCALAR_KEYS:
        raise ValueError(f"unknown config key {key!r}. Known: {', '.join(_SCALAR_KEYS)}")
    if scope == "project":
        target = (workspace or Path.cwd()) / PROJECT_FILENAME
    else:
        target = user_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        text = ""

    new_line = f"{key} = {_render_toml_scalar(value)}"
    # Replace an existing top-level `key = ...` line, else append.
    import re as _re
    pattern = _re.compile(rf'^\s*{_re.escape(key)}\s*=.*$', _re.M)
    if pattern.search(text):
        text = pattern.sub(new_line, text, count=1)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += new_line + "\n"
    target.write_text(text, encoding="utf-8")
    return target


def write_model_choice(model: str, scope: str = "user",
                       workspace: "Path | None" = None) -> Path:
    """Persist `model = "..."` to a config file so a picked model sticks (U2).

    Thin wrapper over `write_scalar` (kept for the U2 call sites)."""
    return write_scalar("model", model, scope=scope, workspace=workspace)


def _toml_str(value: str) -> str:
    """Minimal TOML string escaping (double-quote, backslash)."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_mcp_server(
    name: str,
    *,
    command: "str | None" = None,
    args: "list[str] | None" = None,
    transport: str = "stdio",
    url: "str | None" = None,
    scope: str = "user",
    workspace: "Path | None" = None,
) -> Path:
    """Append an `[[mcp.servers]]` entry to a config file (W6, ADR-0021).

    Mirrors `write_model_choice`'s dependency-free text approach: read the file,
    refuse if a server of the same `name` is already present, else append a
    well-formed `[[mcp.servers]]` block and write it back. The existing
    `mcp_server_specs` reader round-trips what this writes. Returns the path.
    Raises ValueError on a duplicate name or a malformed spec.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("mcp add: a server name is required")
    transport = (transport or "stdio").strip()
    if transport == "stdio":
        if not command:
            raise ValueError("mcp add: a stdio server needs --command")
    elif transport in ("http", "sse"):
        if not url:
            raise ValueError(f"mcp add: a {transport} server needs --url")
    else:
        raise ValueError(f"mcp add: unknown transport {transport!r} "
                         "(use stdio, http, or sse)")

    if scope == "project":
        target = (workspace or Path.cwd()) / PROJECT_FILENAME
    else:
        target = user_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        text = ""

    # Refuse to clobber an existing server of the same name.
    existing = _load_toml(target) if target.exists() else {}
    for entry in (((existing.get("mcp") or {}).get("servers")) or []):
        if isinstance(entry, dict) and entry.get("name") == name:
            raise ValueError(f"mcp add: a server named {name!r} already exists in "
                             f"{target}")

    lines = ["", "[[mcp.servers]]", f"name = {_toml_str(name)}",
             f"transport = {_toml_str(transport)}"]
    if transport == "stdio":
        lines.append(f"command = {_toml_str(command)}")
        if args:
            rendered = ", ".join(_toml_str(a) for a in args)
            lines.append(f"args = [{rendered}]")
    else:
        lines.append(f"url = {_toml_str(url)}")

    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n".join(lines) + "\n"
    target.write_text(text, encoding="utf-8")
    return target


def load_config(workspace: Path) -> dict[str, Any]:
    """Merge user then project config (project wins). Returns recognized keys only.

    The full parsed dicts are also stashed under `_raw_project` / `_raw_user` so
    later features (F7 ignore globs, F11 mcp.servers) can read their sections.
    """
    user = _load_toml(user_config_path())
    project_path = find_project_config(workspace)
    project = _load_toml(project_path) if project_path else {}

    merged: dict[str, Any] = {}
    for layer in (user, project):  # later layer wins
        for key in _SCALAR_KEYS:
            if key in layer:
                merged[key] = layer[key]
    merged["_raw_project"] = project
    merged["_raw_user"] = user
    merged["_project_path"] = str(project_path) if project_path else None
    return merged


def mcp_server_specs(config: dict[str, Any]):
    """Read `[[mcp.servers]]` entries from the merged config into McpServerSpec.

    Project entries win over user entries with the same `name` (project layer is
    more specific, mirroring the scalar merge). Malformed entries are skipped with
    a warning — a bad server block must never make the CLI unusable (F11.3).

    Returns `list[McpServerSpec]`. Import is local so `nerva-agent` stays an
    implementation detail of this reader, not a load-time dependency of config.
    """
    from nerva_agent.mcp_client import McpServerSpec  # cli-tier dep (ADR-0002)

    def _entries(raw: dict[str, Any]) -> list[dict]:
        mcp = raw.get("mcp") if isinstance(raw, dict) else None
        servers = mcp.get("servers") if isinstance(mcp, dict) else None
        return [s for s in servers if isinstance(s, dict)] if isinstance(servers, list) else []

    # user first, project second → project overrides by name.
    by_name: dict[str, McpServerSpec] = {}
    for raw in (config.get("_raw_user", {}), config.get("_raw_project", {})):
        for entry in _entries(raw):
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                print("warning: skipping [[mcp.servers]] entry without a name",
                      file=sys.stderr)
                continue
            transport = entry.get("transport", "stdio")
            read_only = entry.get("read_only") or []
            by_name[name] = McpServerSpec(
                name=name,
                transport=transport,
                command=entry.get("command"),
                args=list(entry.get("args") or []),
                env=dict(entry.get("env") or {}),
                url=entry.get("url"),
                read_only=[t for t in read_only if isinstance(t, str)],
                alias=entry.get("alias"),
            )
    return list(by_name.values())


def verify_config(config: dict[str, Any]) -> dict[str, Any]:
    """Read the `[verify]` section (H1, ADR-0012) from the merged config.

    Project overrides user. Returns a normalized dict:
        {enabled: bool, commands: list[str], max_repair_attempts: int,
         pycompile: bool}
    A missing section means verification is OFF (no behavior change), so the
    feature is strictly opt-in.
    """
    def _section(raw: dict[str, Any]) -> dict:
        v = raw.get("verify") if isinstance(raw, dict) else None
        return v if isinstance(v, dict) else {}

    merged: dict[str, Any] = {}
    for raw in (config.get("_raw_user", {}), config.get("_raw_project", {})):
        merged.update(_section(raw))

    commands = merged.get("commands") or []
    if not isinstance(commands, list):
        commands = []
    return {
        "enabled": bool(merged.get("enabled", False)),
        "commands": [c for c in commands if isinstance(c, str) and c.strip()],
        "max_repair_attempts": int(merged.get("max_repair_attempts", 3) or 0),
        "pycompile": bool(merged.get("pycompile", True)),  # on by default when enabled
    }


def context_config(config: dict[str, Any]) -> dict[str, Any]:
    """Read the `[context]` section (H2, ADR-0013) from the merged config.

    Project overrides user. Returns a normalized dict:
        {inject_on_edit: bool, resolve_errors: bool, max_callers: int}
    A missing section means both H2.1 (pre-edit injection) and H2.2 (error-symbol
    resolution) default to on — they are additive/no-op-safe by construction (an
    absent graph or unresolved symbol yields empty text either way), matching the
    ADR's "identical to today when the graph is absent" guarantee even when the
    section itself is absent. Set `inject_on_edit`/`resolve_errors` to false to
    opt out explicitly.
    """
    def _section(raw: dict[str, Any]) -> dict:
        v = raw.get("context") if isinstance(raw, dict) else None
        return v if isinstance(v, dict) else {}

    merged: dict[str, Any] = {}
    for raw in (config.get("_raw_user", {}), config.get("_raw_project", {})):
        merged.update(_section(raw))

    return {
        "inject_on_edit": bool(merged.get("inject_on_edit", True)),
        "resolve_errors": bool(merged.get("resolve_errors", True)),
        "max_callers": int(merged.get("max_callers", 5) or 0),
    }


def resolve(key: str, flag_value: Any, config: dict[str, Any], default: Any) -> Any:
    """Apply precedence for one setting: flag > config > default.

    `flag_value` is the value straight from argparse. Because argparse can't tell
    "user passed the default" from "user passed nothing", callers pass the flag's
    *sentinel* (empty string for str, 0 for int, False for store_true) and we treat
    that sentinel as "unset" so config can supply a value. An explicitly-set flag
    (non-sentinel) always wins.
    """
    if flag_value not in ("", 0, False, None):
        return flag_value
    if key in config:
        return config[key]
    return default
