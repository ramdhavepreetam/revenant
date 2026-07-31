"""Tests for file checkpointing / undo (F8, ADR-0010).

Covers the Checkpointer's before_tool snapshot behavior, restore semantics
(restore edited files, delete newly-created ones), the run_bash / unknown-tool
skip, undo ordering, cross-process persistence via load(), and the graceful
degradation paths where the filesystem misbehaves.

Pure filesystem + tmp_path; no model or network is touched.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from revenant_cli.checkpoint import Checkpointer, _Snapshot


def _cp(tmp_path: Path, persist: bool = False) -> Checkpointer:
    store = (tmp_path / ".aibot" / "checkpoints.json") if persist else None
    return Checkpointer(workspace=tmp_path, store_path=store)


# --- snapshot capture --------------------------------------------------------

def test_snapshot_existing_file_then_restore(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("original\n")
    cp = _cp(tmp_path)

    cp.snapshot("edit_file", {"path": "a.txt"})
    target.write_text("mutated\n")  # simulate the tool editing it

    desc = cp.undo_last()
    assert "restored a.txt" in desc
    assert target.read_text() == "original\n"


def test_snapshot_new_file_undo_deletes_it(tmp_path):
    cp = _cp(tmp_path)
    # File does not exist yet at snapshot time.
    cp.snapshot("write_file", {"path": "new.txt"})
    (tmp_path / "new.txt").write_text("agent-created\n")

    desc = cp.undo_last()
    assert "removed new.txt" in desc
    assert not (tmp_path / "new.txt").exists()


def test_snapshot_records_existed_flag(tmp_path):
    (tmp_path / "here.txt").write_text("x")
    cp = _cp(tmp_path)
    cp.snapshot("edit_file", {"path": "here.txt"})
    cp.snapshot("write_file", {"path": "absent.txt"})
    assert [s.existed for s in cp.snapshots] == [True, False]


# --- what is / isn't snapshotted --------------------------------------------

def test_run_bash_is_not_snapshotted(tmp_path):
    cp = _cp(tmp_path)
    cp.snapshot("run_bash", {"command": "rm -rf /"})
    assert cp.snapshots == []


def test_unknown_tool_is_not_snapshotted(tmp_path):
    cp = _cp(tmp_path)
    cp.snapshot("search", {"query": "x"})
    assert cp.snapshots == []


def test_missing_or_bad_path_arg_is_ignored(tmp_path):
    cp = _cp(tmp_path)
    cp.snapshot("edit_file", {})              # no path
    cp.snapshot("edit_file", {"path": ""})    # empty path
    cp.snapshot("edit_file", {"path": 123})   # non-str path
    assert cp.snapshots == []


# --- ordering ----------------------------------------------------------------

def test_undo_all_reverts_newest_first(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("a0\n")
    b.write_text("b0\n")
    cp = _cp(tmp_path)

    cp.snapshot("edit_file", {"path": "a.txt"})
    a.write_text("a1\n")
    cp.snapshot("edit_file", {"path": "b.txt"})
    b.write_text("b1\n")

    done = cp.undo_all()
    # newest (b) reported first
    assert done[0].startswith("restored b.txt")
    assert done[1].startswith("restored a.txt")
    assert a.read_text() == "a0\n"
    assert b.read_text() == "b0\n"
    assert cp.snapshots == []


def test_undo_last_on_empty_returns_none(tmp_path):
    assert _cp(tmp_path).undo_last() is None


def test_undo_all_on_empty_returns_empty_list(tmp_path):
    assert _cp(tmp_path).undo_all() == []


# --- persistence across "processes" -----------------------------------------

def test_persist_and_load_round_trip(tmp_path):
    (tmp_path / "a.txt").write_text("original\n")
    store = tmp_path / ".aibot" / "checkpoints.json"

    cp = Checkpointer(workspace=tmp_path, store_path=store)
    cp.snapshot("edit_file", {"path": "a.txt"})
    (tmp_path / "a.txt").write_text("mutated\n")
    assert store.is_file()  # persisted to disk

    # Fresh instance, as a separate `revenant undo` invocation would build.
    reloaded = Checkpointer.load(tmp_path, store)
    assert len(reloaded.snapshots) == 1
    desc = reloaded.undo_last()
    assert "restored a.txt" in desc
    assert (tmp_path / "a.txt").read_text() == "original\n"


def test_load_missing_store_returns_empty(tmp_path):
    cp = Checkpointer.load(tmp_path, tmp_path / "nope.json")
    assert cp.snapshots == []


def test_load_corrupt_store_returns_empty(tmp_path):
    store = tmp_path / "corrupt.json"
    store.write_text("{ not json")
    cp = Checkpointer.load(tmp_path, store)
    assert cp.snapshots == []


def test_persist_updates_after_undo(tmp_path):
    (tmp_path / "a.txt").write_text("o\n")
    store = tmp_path / ".aibot" / "checkpoints.json"
    cp = Checkpointer(workspace=tmp_path, store_path=store)
    cp.snapshot("edit_file", {"path": "a.txt"})
    cp.undo_last()
    # After undo, the persisted store should reflect an empty snapshot list.
    reloaded = Checkpointer.load(tmp_path, store)
    assert reloaded.snapshots == []


# --- graceful degradation ----------------------------------------------------

def test_unreadable_file_records_nothing(tmp_path, monkeypatch):
    target = tmp_path / "a.txt"
    target.write_text("x")
    cp = _cp(tmp_path)

    def boom(*_a, **_k):
        raise OSError("cannot read")

    monkeypatch.setattr(Path, "read_text", boom)
    cp.snapshot("edit_file", {"path": "a.txt"})
    # No bogus restore point recorded when the file can't be read.
    assert cp.snapshots == []


def test_persist_oserror_is_swallowed(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    store = tmp_path / ".aibot" / "checkpoints.json"
    cp = Checkpointer(workspace=tmp_path, store_path=store)

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    # Persistence fails, but the in-memory snapshot still lands (no raise).
    cp.snapshot("edit_file", {"path": "a.txt"})
    assert len(cp.snapshots) == 1


def test_restore_missing_created_file_is_noop(tmp_path):
    """undo of a 'newly created' file that was already removed shouldn't raise."""
    cp = _cp(tmp_path)
    cp.snapshots.append(_Snapshot("ghost.txt", existed=False, content="", tool="write_file"))
    # File never existed; restore (=delete) must be a clean no-op.
    desc = cp.undo_last()
    assert "removed ghost.txt" in desc
