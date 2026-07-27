"""Read-only filesystem tools for the Revenant coding agent (P2).

All tools are confined to a **workspace root**: a path argument is resolved and
rejected if it escapes the root (via `..`, an absolute path elsewhere, or a
symlink pointing outside). This is a correctness/damage guardrail, not content
censorship -- the agent can read anything inside the project, nothing outside it.

Tools here are all read-only: `parallel_safe=True`, `mutating=False`, no approval.
Mutating tools (write/edit/bash) arrive in P3 with the approval gate.

`build_fs_tools(root)` returns a list of `Tool`s bound to that root, ready to load
into a `ToolRegistry`.
"""
from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
from pathlib import Path

from nerva_agent.agent_tools import Tool, ToolParam, ToolError

# Cap file/search output so a huge file can't blow the model's context window.
MAX_FILE_BYTES = 64_000
MAX_GREP_MATCHES = 200
MAX_GLOB_RESULTS = 400


class WorkspaceError(ToolError):
    """A path escaped the workspace root, or a filesystem op failed."""


def _resolve_in_root(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`, rejecting anything that escapes it.

    Uses realpath resolution so symlinks that point outside the root are caught.
    """
    root = root.resolve()
    # An absolute path is only allowed if it already lives under root.
    candidate = (root / rel).resolve() if not os.path.isabs(rel) else Path(rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise WorkspaceError(
            f"path {rel!r} escapes the workspace root; access denied"
        ) from None
    return candidate


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


# --- Tool implementations (each closes over `root`) ------------------------

def _read_file(root: Path, path: str) -> str:
    target = _resolve_in_root(root, path)
    if not target.exists():
        raise WorkspaceError(f"no such file: {path}")
    if target.is_dir():
        raise WorkspaceError(f"{path} is a directory; use list_dir")
    data = target.read_bytes()
    truncated = len(data) > MAX_FILE_BYTES
    text = data[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
    if truncated:
        text += f"\n\n[... truncated at {MAX_FILE_BYTES} bytes ...]"
    return text


def _list_dir(root: Path, path: str = ".") -> str:
    target = _resolve_in_root(root, path or ".")
    if not target.exists():
        raise WorkspaceError(f"no such directory: {path}")
    if not target.is_dir():
        raise WorkspaceError(f"{path} is not a directory")
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name)):
        if child.name.startswith(".") and child.name not in (".gitignore",):
            continue
        entries.append(child.name + ("/" if child.is_dir() else ""))
    return "\n".join(entries) if entries else "(empty)"


def _glob(root: Path, pattern: str) -> str:
    root = root.resolve()
    matches: list[str] = []
    # Walk once; match basename or relative path against the glob.
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip dotdirs and common noise.
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("node_modules", "__pycache__", "site")]
        for name in filenames:
            full = Path(dirpath) / name
            rel = _rel(root, full)
            if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(name, pattern):
                matches.append(rel)
                if len(matches) >= MAX_GLOB_RESULTS:
                    matches.append(f"[... capped at {MAX_GLOB_RESULTS} results ...]")
                    return "\n".join(matches)
    return "\n".join(sorted(matches)) if matches else "(no matches)"


def _grep(root: Path, pattern: str, path: str = ".") -> str:
    base = _resolve_in_root(root, path or ".")
    rg = shutil.which("rg")
    if rg:
        try:
            proc = subprocess.run(
                [rg, "--line-number", "--no-heading", "--color", "never",
                 "--max-count", str(MAX_GREP_MATCHES), pattern, str(base)],
                capture_output=True, text=True, timeout=30,
            )
            out = proc.stdout.strip()
            if proc.returncode not in (0, 1):  # 1 = no matches (not an error)
                raise WorkspaceError(f"grep failed: {proc.stderr.strip()[:200]}")
            # Make paths workspace-relative for readability.
            lines = []
            for line in out.splitlines()[:MAX_GREP_MATCHES]:
                lines.append(line.replace(str(root.resolve()) + os.sep, ""))
            return "\n".join(lines) if lines else "(no matches)"
        except subprocess.TimeoutExpired:
            raise WorkspaceError("grep timed out")
    # Pure-Python fallback.
    import re
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        raise WorkspaceError(f"invalid regex: {exc}") from None
    results: list[str] = []
    targets = [base] if base.is_file() else (
        p for p in base.rglob("*") if p.is_file()
    )
    for f in targets:
        if any(part.startswith(".") or part in ("node_modules", "__pycache__", "site") for part in f.parts):
            continue
        try:
            for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                if rx.search(line):
                    results.append(f"{_rel(root, f)}:{i}:{line.strip()[:200]}")
                    if len(results) >= MAX_GREP_MATCHES:
                        return "\n".join(results)
        except (OSError, UnicodeDecodeError):
            continue
    return "\n".join(results) if results else "(no matches)"


def build_fs_tools(root: str | Path) -> list[Tool]:
    """Build the read-only fs toolset bound to `root`."""
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise WorkspaceError(f"workspace root is not a directory: {root}")

    return [
        Tool(
            "read_file",
            "Read a UTF-8 text file from the workspace and return its contents.",
            [ToolParam("path", "string", "File path relative to the workspace root.")],
            run=lambda path: _read_file(root_path, path),
            parallel_safe=True,
        ),
        Tool(
            "list_dir",
            "List the entries of a directory in the workspace.",
            [ToolParam("path", "string", "Directory path (default: workspace root).", required=False)],
            run=lambda path=".": _list_dir(root_path, path),
            parallel_safe=True,
        ),
        Tool(
            "glob",
            "Find files by glob pattern (e.g. '**/*.py' or 'core/*.py').",
            [ToolParam("pattern", "string", "A glob pattern matched against relative paths.")],
            run=lambda pattern: _glob(root_path, pattern),
            parallel_safe=True,
        ),
        Tool(
            "grep",
            "Search file contents by regular expression. Returns path:line:match.",
            [
                ToolParam("pattern", "string", "A regular expression to search for."),
                ToolParam("path", "string", "File or directory to search (default: whole workspace).", required=False),
            ],
            run=lambda pattern, path=".": _grep(root_path, pattern, path),
            parallel_safe=True,
        ),
    ]
