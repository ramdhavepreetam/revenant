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


def _edit_file(root: Path, path: str, old: str, new: str) -> str:
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
    if count > 1:
        raise WorkspaceError(
            f"old string is not unique ({count} matches). Add surrounding context "
            "so it matches exactly one location."
        )
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    return f"edited {_rel(root, target)} (1 replacement)"


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
            "Replace exactly one occurrence of `old` with `new` in a file. `old` "
            "must match a unique span (include surrounding context if needed).",
            [
                ToolParam("path", "string", "File path relative to the workspace root."),
                ToolParam("old", "string", "Exact text to replace (must occur exactly once)."),
                ToolParam("new", "string", "Replacement text."),
            ],
            run=lambda path, old, new: _edit_file(root_path, path, old, new),
            mutating=True,
        ),
    ]
