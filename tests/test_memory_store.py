"""M0 (ADR-0022): the stdlib SQLite/FTS5 memory store — model-free."""
from __future__ import annotations

from pathlib import Path

from nerva_agent.memory_store import MemoryStore, Memory


def test_remember_and_recall_by_keyword():
    m = MemoryStore(":memory:")
    m.remember("this project uses pytest, not unittest", kind="fact")
    m.remember("the API layer lives in packages/api", kind="fact")
    hits = m.recall("how do I run the tests with pytest")
    assert any("pytest" in h.content for h in hits)


def test_recall_ranks_the_relevant_one_first():
    m = MemoryStore(":memory:")
    m.remember("unrelated note about colors")
    m.remember("editing config.py directly breaks the loader — use write_scalar", kind="outcome")
    hits = m.recall("changing config.py")
    assert hits and "config.py" in hits[0].content


def test_recall_special_chars_does_not_crash():
    m = MemoryStore(":memory:")
    m.remember("call foo(bar) then baz")
    # Punctuation that would be an FTS5 syntax error must be handled, not raised.
    assert isinstance(m.recall("foo(bar) [x] AND OR *"), list)


def test_empty_query_returns_recent():
    m = MemoryStore(":memory:")
    m.remember("first"); m.remember("second")
    recent = m.recall("")
    assert recent and recent[0].content == "second"   # newest first


def test_exact_duplicate_is_deduped():
    m = MemoryStore(":memory:")
    a = m.remember("same fact")
    b = m.remember("same fact")
    assert a == b and m.count() == 1


def test_forget_and_clear():
    m = MemoryStore(":memory:")
    mid = m.remember("temporary")
    assert m.forget(mid) is True and m.count() == 0
    m.remember("a"); m.remember("b")
    m.clear()
    assert m.count() == 0


def test_list_all_newest_first():
    m = MemoryStore(":memory:")
    m.remember("old"); m.remember("new")
    contents = [x.content for x in m.list_all()]
    assert contents == ["new", "old"]


def test_kind_defaults_to_fact_for_unknown():
    m = MemoryStore(":memory:")
    m.remember("x", kind="bogus")
    assert m.list_all()[0].kind == "fact"


def test_empty_content_not_stored():
    m = MemoryStore(":memory:")
    assert m.remember("   ") is None and m.count() == 0


def test_persists_across_reopen(tmp_path):
    db = tmp_path / ".aibot" / "memory.db"
    m = MemoryStore(db)
    m.remember("persisted fact about the build")
    m.close()
    m2 = MemoryStore(db)
    assert any("build" in x.content for x in m2.recall("build"))


def test_unopenable_db_degrades_to_empty():
    # An invalid path (embedded null) → a null store: never raises, all ops empty.
    bad = MemoryStore("/nonexistent\x00/memory.db")
    assert bad.available is False
    assert bad.recall("x") == [] and bad.remember("y") is None and bad.count() == 0


def test_memory_dataclass_shape():
    m = MemoryStore(":memory:")
    m.remember("hello", kind="decision", source="run:1")
    mem = m.list_all()[0]
    assert isinstance(mem, Memory)
    assert mem.kind == "decision" and mem.source == "run:1" and mem.created_at > 0
