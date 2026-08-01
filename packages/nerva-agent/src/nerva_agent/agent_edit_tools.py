"""Mutating file tools for the Revenant coding agent (P3): write_file, edit_file.

Both are `mutating` (so `Tool.__post_init__` sets `requires_approval=True`) and
confined to the workspace root, reusing `_resolve_in_root` from `agent_fs_tools`.
The loop's approval gate pauses before either runs unless the session is in
auto-approve (yolo) mode.

- write_file(path, content): create or overwrite a file.
- edit_file(path, old, new): replace EXACTLY ONE occurrence of `old` with `new`
  (mirrors Claude Code's Edit: errors on 0 or >1 matches, so the model must give
  enough surrounding context to be unambiguous).
"""
from __future__ import annotations

from pathlib import Path

from nerva_agent.agent_tools import Tool, ToolParam
from nerva_agent.agent_fs_tools import _resolve_in_root, _rel, WorkspaceError


def _write_file(root: Path, path: str, content: str) -> str:
    target = _resolve_in_root(root, path)
    existed = target.exists()
    if existed and target.is_dir():
        raise WorkspaceError(f"{path} is a directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    verb = "overwrote" if existed else "created"
    lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
    return f"{verb} {_rel(root, target)} ({lines} lines, {len(content)} bytes)"


def _coerce_bool(value: object) -> bool:
    """Weak models pass booleans as strings ('true'/'1'); coerce leniently."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y", "all")
    return bool(value)


def _edit_file(root: Path, path: str, old: str, new: str,
               replace_all: object = False) -> str:
    target = _resolve_in_root(root, path)
    if not target.exists():
        raise WorkspaceError(f"no such file: {path}")
    if target.is_dir():
        raise WorkspaceError(f"{path} is a directory")
    text = target.read_text(encoding="utf-8", errors="replace")
    if old == new:
        raise WorkspaceError("old and new are identical; nothing to change")
    count = text.count(old)
    if count == 0:
        raise WorkspaceError(
            "old string not found. Provide the exact text to replace, including "
            "whitespace and surrounding context."
        )
    all_ = _coerce_bool(replace_all)
    # W4a (ADR-0020): replace_all replaces every occurrence; the default (False)
    # keeps the exactly-one contract — a non-unique match without the flag errors,
    # so a careless edit can't silently touch multiple spots.
    if count > 1 and not all_:
        raise WorkspaceError(
            f"old string is not unique ({count} matches). Add surrounding context "
            "so it matches exactly one location, or pass all=true to replace every "
            "occurrence."
        )
    n = count if all_ else 1
    target.write_text(text.replace(old, new, n), encoding="utf-8")
    return f"edited {_rel(root, target)} ({n} replacement{'s' if n != 1 else ''})"


def build_edit_tools(root: str | Path) -> list[Tool]:
    """Build the mutating write/edit toolset bound to `root`. Both require approval."""
    root_path = Path(root).resolve()
    if not root_path.is_dir():
        raise WorkspaceError(f"workspace root is not a directory: {root}")

    return [
        Tool(
            "write_file",
            "Create a new file or overwrite an existing one with the given content.",
            [
                ToolParam("path", "string", "File path relative to the workspace root."),
                ToolParam("content", "string", "The full file content to write."),
            ],
            run=lambda path, content: _write_file(root_path, path, content),
            mutating=True,
        ),
        Tool(
            "edit_file",
            "Replace occurrences of `old` with `new` in a file. By default `old` "
            "must match exactly one span (include surrounding context if needed); "
            "pass all=true to replace every occurrence in the file.",
            [
                ToolParam("path", "string", "File path relative to the workspace root."),
                ToolParam("old", "string", "Exact text to replace."),
                ToolParam("new", "string", "Replacement text."),
                ToolParam("all", "boolean",
                          "Replace every occurrence (default: false = exactly one).",
                          required=False),
            ],
            run=lambda path, old, new, all=False: _edit_file(root_path, path, old, new, all),
            mutating=True,
        ),
    ]
