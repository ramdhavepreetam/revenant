"""Git-native checkpointing for undo (F16.1, ADR-0009).

File-snapshot undo (checkpoint.py) can revert `write_file`/`edit_file`, but it
cannot capture what `run_bash` does — a shell command may touch anything. When
the workspace is a git repo, this checkpointer captures the *whole tree* (tracked
+ untracked) as a **shadow-commit** before a mutating boundary, so `revenant undo`
can restore everything, shell side-effects included.

`checkpoint.py`'s own docstring named this as the right layer for shell effects;
this is that layer (closing F9). Shadow-commits live under a private ref
namespace (`refs/revenant/undo/*`) so they never touch the user's branches,
HEAD, or history. A non-git workspace has no git checkpointer — the caller falls
back to file-snapshots.

Implementation: `git stash create` builds a commit object capturing the working
tree without modifying HEAD, the index, or the working directory. We store its
sha under our ref namespace; undo does a `git stash apply` of that commit and
then restores untracked deletions via checkout. Everything shells out to `git`
(already a dependency of the workflow); no libgit2.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_UNDO_REF_PREFIX = "refs/revenant/undo"


def _git(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(workspace),
        capture_output=True, text=True, check=check,
    )


def is_git_repo(workspace: Path) -> bool:
    """True if `workspace` is the top level of its own git work tree.

    We require the work-tree root to BE the workspace (not merely inside some
    ancestor repo), so a plain directory that happens to sit under a parent repo
    is correctly treated as non-git and falls back to file-snapshots.
    """
    try:
        r = _git(workspace, "rev-parse", "--show-toplevel", check=False)
    except (OSError, FileNotFoundError):
        return False
    if r.returncode != 0:
        return False
    try:
        return Path(r.stdout.strip()).resolve() == Path(workspace).resolve()
    except OSError:
        return False


@dataclass
class GitCheckpointer:
    """Whole-tree undo via git shadow-commits. Use when `workspace` is a git repo.

    Shares the checkpoint.py interface the loop expects: `snapshot(tool, args)` is
    the before_tool hook, and `undo_last`/`undo_all` revert. Snapshots are stored
    as refs, so they persist across invocations without a separate store file.
    """

    workspace: Path
    _refs: list[str] = field(default_factory=list)  # this session's ref names, oldest→newest

    # --- capture -----------------------------------------------------------
    def snapshot(self, tool: str, args: dict) -> None:
        """before_tool hook: capture the whole working tree as a shadow-commit.

        Unlike file-snapshots, this is tool-agnostic — it captures state before
        ANY mutating tool (including run_bash), because the shell can touch
        anything. A no-op (returns silently) if there's nothing to capture or git
        errors; a checkpoint failure must never block the tool.
        """
        try:
            created = _git(self.workspace, "stash", "create",
                           f"revenant undo before {tool}", check=False)
        except (OSError, FileNotFoundError):
            return
        sha = created.stdout.strip()
        if not sha:
            # Clean tree: nothing to stash. Record a sentinel so undo of a
            # "no change" boundary is a clean no-op rather than a surprise.
            sha = self._head_sha() or ""
            if not sha:
                return
        ref = f"{_UNDO_REF_PREFIX}/{len(self.list_refs())}-{sha[:8]}"
        r = _git(self.workspace, "update-ref", ref, sha, check=False)
        if r.returncode == 0:
            self._refs.append(ref)

    # --- undo --------------------------------------------------------------
    def undo_last(self) -> str | None:
        refs = self.list_refs()
        if not refs:
            return None
        ref = refs[-1]
        desc = self._restore(ref)
        _git(self.workspace, "update-ref", "-d", ref, check=False)
        return desc

    def undo_all(self) -> list[str]:
        done: list[str] = []
        for ref in reversed(self.list_refs()):
            done.append(self._restore(ref))
            _git(self.workspace, "update-ref", "-d", ref, check=False)
        return done

    def _restore(self, ref: str) -> str:
        """Restore the working tree to the snapshot at `ref`.

        Two moves: (1) reset TRACKED files to the snapshot's tree, and (2) remove
        UNTRACKED files/dirs that appeared after the snapshot (e.g. a run_bash
        artifact). Ignored files are left alone so build outputs aren't nuked.
        """
        sha = self._ref_sha(ref)
        if not sha:
            return f"skipped {ref} (missing)"
        # (1) Tracked files back to the snapshot state.
        _git(self.workspace, "checkout", sha, "--", ".", check=False)
        # (2) Drop untracked additions (shell side-effects). -d dirs, -f force;
        # ignored files are intentionally NOT removed (no -x).
        _git(self.workspace, "clean", "-fd", check=False)
        return f"restored working tree to snapshot {sha[:8]}"

    # --- refs --------------------------------------------------------------
    def list_refs(self) -> list[str]:
        """All undo refs for this workspace, oldest→newest (sorted by name)."""
        r = _git(self.workspace, "for-each-ref", "--format=%(refname)",
                 _UNDO_REF_PREFIX, check=False)
        if r.returncode != 0:
            return []
        refs = [line.strip() for line in r.stdout.splitlines() if line.strip()]
        return sorted(refs)

    def has_snapshots(self) -> bool:
        return bool(self.list_refs())

    def _ref_sha(self, ref: str) -> str:
        r = _git(self.workspace, "rev-parse", ref, check=False)
        return r.stdout.strip() if r.returncode == 0 else ""

    def _head_sha(self) -> str:
        r = _git(self.workspace, "rev-parse", "HEAD", check=False)
        return r.stdout.strip() if r.returncode == 0 else ""

    def clear(self) -> None:
        """Delete all undo refs (e.g. after a successful, accepted run)."""
        for ref in self.list_refs():
            _git(self.workspace, "update-ref", "-d", ref, check=False)
