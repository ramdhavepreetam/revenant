"""M1 (ADR-0022): the remember/recall agent tools — model-free."""
from __future__ import annotations

from nerva_agent.agent_tools import ToolRegistry
from nerva_agent.memory_store import MemoryStore
from nerva_agent.memory_tools import build_memory_tools


def _reg():
    return ToolRegistry(build_memory_tools(MemoryStore(":memory:")))


def test_remember_then_recall_round_trips():
    reg = _reg()
    reg.dispatch("remember", {"note": "this project uses ruff for linting"})
    out = reg.dispatch("recall", {"query": "linting"})
    assert "ruff" in out


def test_remember_reports_id():
    out = _reg().dispatch("remember", {"note": "a durable fact"})
    assert "#1" in out and "future runs" in out


def test_recall_empty_is_graceful():
    out = _reg().dispatch("recall", {"query": "nothing stored yet"})
    assert "No project memories" in out


def test_remember_empty_note():
    out = _reg().dispatch("remember", {"note": "   "})
    assert "Nothing saved" in out


def test_tools_are_not_mutating_and_parallel_safe():
    reg = _reg()
    for name in ("remember", "recall"):
        t = reg.get(name)
        assert t.mutating is False           # writes to own memory, not workspace
        assert t.requires_approval is False  # so no approval gate
        assert t.parallel_safe is True


def test_kind_is_optional():
    reg = _reg()
    # `remember` works without a kind (defaults to fact).
    out = reg.dispatch("remember", {"note": "no kind given"})
    assert "Remembered" in out


def test_build_memory_tools_returns_both():
    names = {t.name for t in build_memory_tools(MemoryStore(":memory:"))}
    assert names == {"remember", "recall"}
