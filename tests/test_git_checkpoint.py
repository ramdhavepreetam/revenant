"""Tests for git-native checkpointing (F16.1, ADR-0009).

Runs against a real temporary git repo: asserts a shadow-commit captures the
tree (including a run_bash-style untracked file), undo restores it, refs live
under refs/revenant/undo/*, and a non-git workspace is detected so the caller
can fall back to file-snapshots.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from revenant_cli.git_checkpoint import GitCheckpointer, is_git_repo


def _git(ws, *args):
    return subprocess.run(["git", *args], cwd=str(ws),
                          capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "tracked.txt").write_text("v1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "initial")
    return tmp_path


# --- detection ---------------------------------------------------------------

def test_is_git_repo(repo, tmp_path):
    assert is_git_repo(repo) is True
    non = tmp_path / "plain"
    non.mkdir()
    assert is_git_repo(non) is False


# --- capture + restore -------------------------------------------------------

def test_snapshot_and_undo_restores_tracked_edit(repo):
    cp = GitCheckpointer(repo)
    cp.snapshot("edit_file", {"path": "tracked.txt"})
    (repo / "tracked.txt").write_text("MUTATED\n")

    desc = cp.undo_last()
    assert desc is not None and "restored" in desc
    assert (repo / "tracked.txt").read_text() == "v1\n"


def test_snapshot_captures_run_bash_untracked_file(repo):
    cp = GitCheckpointer(repo)
    # Simulate run_bash creating a new untracked file after the snapshot.
    cp.snapshot("run_bash", {"command": "touch generated.txt"})
    (repo / "generated.txt").write_text("from shell\n")
    (repo / "tracked.txt").write_text("also changed\n")

    cp.undo_all()
    # Tracked file reverts; the untracked shell artifact is gone.
    assert (repo / "tracked.txt").read_text() == "v1\n"
    assert not (repo / "generated.txt").exists()


def test_refs_live_under_private_namespace(repo):
    cp = GitCheckpointer(repo)
    (repo / "tracked.txt").write_text("changed\n")
    cp.snapshot("edit_file", {"path": "tracked.txt"})
    refs = cp.list_refs()
    assert refs
    assert all(r.startswith("refs/revenant/undo/") for r in refs)
    # User-facing branches are untouched.
    branches = _git(repo, "branch", "--format=%(refname)").stdout
    assert "revenant" not in branches


def test_undo_deletes_the_ref(repo):
    cp = GitCheckpointer(repo)
    (repo / "tracked.txt").write_text("x\n")
    cp.snapshot("edit_file", {"path": "tracked.txt"})
    assert cp.has_snapshots()
    cp.undo_last()
    assert not cp.has_snapshots()


def test_undo_last_empty_returns_none(repo):
    assert GitCheckpointer(repo).undo_last() is None


def test_clear_removes_all_refs(repo):
    cp = GitCheckpointer(repo)
    for i in range(2):
        (repo / "tracked.txt").write_text(f"v{i}\n")
        cp.snapshot("edit_file", {"path": "tracked.txt"})
    assert len(cp.list_refs()) == 2
    cp.clear()
    assert cp.list_refs() == []
