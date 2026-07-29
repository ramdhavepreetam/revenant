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
