"""File checkpointing for undo (F8).

A coding agent is only trustworthy to edit files if its changes can be reverted.
Before any mutating tool runs, the loop calls the checkpointer's `snapshot` hook
with the tool name and args; the checkpointer records the pre-edit state of the
file(s) that tool is about to touch. `undo_last` / `undo_all` then restore them.

Snapshots are kept in memory for the session and mirrored to disk (under the data
dir) so `revenant undo` can work as a separate invocation after the session ends.

Scope note: `write_file` and `edit_file` name their target in `args["path"]`, so
we snapshot exactly that file (including "file did not exist" so undo can delete a
newly-created file). `run_bash` can touch anything unpredictably, so it is NOT
file-snapshotted here — git integration (F9) is the right undo layer for shell
side effects.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

# Tools whose target file we snapshot, and the arg naming that file.
_SNAPSHOTTED_TOOLS = {"write_file": "path", "edit_file": "path"}


@dataclass
class _Snapshot:
    rel_path: str          # workspace-relative path of the touched file
    existed: bool          # was there a file before the edit?
    content: str           # its prior text ("" when it didn't exist)
    tool: str
    ts: float = field(default_factory=time.time)


@dataclass
class Checkpointer:
    """Records pre-edit file state so mutating tool calls can be reverted."""

    workspace: Path
    store_path: Path | None = None       # where to persist snapshots (optional)
    snapshots: list[_Snapshot] = field(default_factory=list)

    def snapshot(self, tool: str, args: dict) -> None:
        """before_tool hook: capture the target file's current state.

        No-op for tools we don't snapshot (e.g. run_bash) or a missing path arg.
        """
        arg_name = _SNAPSHOTTED_TOOLS.get(tool)
        if arg_name is None:
            return
        rel = args.get(arg_name)
        if not isinstance(rel, str) or not rel:
            return
        target = (self.workspace / rel)
        existed = target.is_file()
        content = ""
        if existed:
            try:
                content = target.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return  # can't snapshot -> don't record a bogus restore point
        self.snapshots.append(_Snapshot(rel, existed, content, tool))
        self._persist()

    # --- undo --------------------------------------------------------------
    def undo_last(self) -> str | None:
        """Revert the most recent snapshot. Returns a description, or None if empty."""
        if not self.snapshots:
            return None
        snap = self.snapshots.pop()
        desc = self._restore(snap)
        self._persist()
        return desc

    def undo_all(self) -> list[str]:
        """Revert all snapshots, newest first. Returns descriptions of each."""
        done: list[str] = []
        while self.snapshots:
            snap = self.snapshots.pop()
            done.append(self._restore(snap))
        self._persist()
        return done

    def _restore(self, snap: _Snapshot) -> str:
        target = self.workspace / snap.rel_path
        if not snap.existed:
            # The file was created by the edit; undo means deleting it.
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            return f"removed {snap.rel_path} (was newly created)"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(snap.content, encoding="utf-8")
        return f"restored {snap.rel_path}"

    # --- persistence -------------------------------------------------------
    def _persist(self) -> None:
        if self.store_path is None:
            return
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            data = [
                {"rel_path": s.rel_path, "existed": s.existed,
                 "content": s.content, "tool": s.tool, "ts": s.ts}
                for s in self.snapshots
            ]
            self.store_path.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass  # persistence is best-effort; in-memory undo still works

    @classmethod
    def load(cls, workspace: Path, store_path: Path) -> "Checkpointer":
        """Reconstruct a checkpointer from a persisted store (for `revenant undo`)."""
        cp = cls(workspace=workspace, store_path=store_path)
        try:
            raw = json.loads(store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cp
        for d in raw:
            cp.snapshots.append(_Snapshot(
                rel_path=d["rel_path"], existed=d["existed"],
                content=d["content"], tool=d.get("tool", ""), ts=d.get("ts", 0.0),
            ))
        return cp
