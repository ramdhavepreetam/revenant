"""Tests for per-workspace session persistence (F3, ADR-0007).

Covers save->load round-trip of the full transcript+metadata, in-place update by
id, list ordering/filtering, latest-session lookup, and graceful handling of
missing/corrupt records. Pure filesystem + tmp_path; no model or network.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from revenant_cli.session_store import (
    save_session, load_session, list_sessions, latest_session_id, Session,
)

_MSGS = [
    {"role": "system", "content": "sys"},
    {"role": "user", "content": "do a thing"},
    {"role": "assistant", "content": "done"},
]


def test_save_then_load_round_trips(tmp_path):
    sid = save_session(tmp_path, goal="do a thing", model="m", messages=_MSGS,
                       turns_covered=2, summary="s")
    assert sid is not None
    loaded = load_session(tmp_path, sid)
    assert loaded is not None
    assert loaded.goal == "do a thing"
    assert loaded.model == "m"
    assert loaded.messages == _MSGS
    assert loaded.turns_covered == 2
    assert loaded.summary == "s"


def test_session_persisted_under_workspace_aibot(tmp_path):
    sid = save_session(tmp_path, goal="g", model="m", messages=_MSGS)
    assert (tmp_path / ".aibot" / "sessions" / f"{sid}.json").is_file()


def test_save_with_existing_id_updates_in_place(tmp_path):
    sid = save_session(tmp_path, goal="first", model="m", messages=_MSGS[:1])
    created = load_session(tmp_path, sid).created_at
    # Re-save with the same id: updates content, keeps created_at, one file only.
    sid2 = save_session(tmp_path, goal="first", model="m", messages=_MSGS,
                        session_id=sid)
    assert sid2 == sid
    loaded = load_session(tmp_path, sid)
    assert loaded.messages == _MSGS
    assert loaded.created_at == created
    assert len(list(( tmp_path / ".aibot" / "sessions").glob("*.json"))) == 1


def test_list_sessions_newest_first(tmp_path):
    import time
    a = save_session(tmp_path, goal="a", model="m", messages=_MSGS)
    time.sleep(0.005)  # ensure distinct ids/timestamps
    b = save_session(tmp_path, goal="b", model="m", messages=_MSGS)
    metas = list_sessions(tmp_path)
    assert [m["id"] for m in metas] == [b, a]  # newest updated first
    # metadata carries goal + message_count, NOT the transcript
    assert metas[0]["goal"] == "b"
    assert metas[0]["message_count"] == len(_MSGS)
    assert "messages" not in metas[0]


def test_latest_session_id(tmp_path):
    assert latest_session_id(tmp_path) is None
    save_session(tmp_path, goal="a", model="m", messages=_MSGS)
    import time
    time.sleep(0.005)
    b = save_session(tmp_path, goal="b", model="m", messages=_MSGS)
    assert latest_session_id(tmp_path) == b


# --- degradation -------------------------------------------------------------

def test_load_missing_session_returns_none(tmp_path):
    assert load_session(tmp_path, "0000000000000") is None


def test_list_empty_when_no_sessions_dir(tmp_path):
    assert list_sessions(tmp_path) == []


def test_corrupt_file_is_skipped_in_list(tmp_path):
    good = save_session(tmp_path, goal="g", model="m", messages=_MSGS)
    bad = tmp_path / ".aibot" / "sessions" / "bad.json"
    bad.write_text("{ not json")
    metas = list_sessions(tmp_path)
    assert [m["id"] for m in metas] == [good]  # bad file skipped, good survives


def test_load_corrupt_returns_none(tmp_path):
    d = tmp_path / ".aibot" / "sessions"
    d.mkdir(parents=True)
    (d / "x.json").write_text("not json")
    assert load_session(tmp_path, "x") is None


def test_from_raw_tolerates_missing_and_extra_keys(tmp_path):
    d = tmp_path / ".aibot" / "sessions"
    d.mkdir(parents=True)
    # Minimal record + an unknown key; must load without crashing.
    (d / "min.json").write_text('{"id": "min", "extra": 1}')
    loaded = load_session(tmp_path, "min")
    assert loaded is not None
    assert loaded.id == "min"
    assert loaded.messages == []
    assert loaded.goal == ""


def test_save_failure_returns_none(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(Path, "write_text", boom)
    assert save_session(tmp_path, goal="g", model="m", messages=_MSGS) is None
