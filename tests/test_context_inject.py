"""Tests for proactive context injection (H2, ADR-0013).

Covers the pure engine-tier helpers in `nerva_agent.context_inject`:
  - H2.1 pre_edit_context: def+callers for the symbol an edit touches, capped.
  - H2.2 resolve_error_symbols / extract_candidate_symbols: symbol resolution
    from a fake traceback, deduped and capped.
Both must no-op (return "") when the graph is None or nothing resolves.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nerva_agent.code_graph.indexer import build_index
from nerva_agent.context_inject import (
    extract_candidate_symbols,
    pre_edit_context,
    resolve_error_symbols,
)


@pytest.fixture
def graph(tmp_path: Path):
    (tmp_path / "a.py").write_text(
        "from b import base\n"
        "\n"
        "def top():\n"
        "    return mid()\n"
        "\n"
        "def mid():\n"
        "    return base()\n"
        "\n"
        "def uncalled():\n"
        "    return base()\n"
    )
    (tmp_path / "b.py").write_text(
        "def base():\n"
        "    return 1\n"
        "\n"
        "def other():\n"
        "    return 2\n"
    )
    return build_index(tmp_path)


# --- H2.1 — pre-edit context -------------------------------------------------


def test_pre_edit_context_includes_def_and_callers(graph):
    args = {"path": "b.py", "old": "def base():\n    return 1", "new": "def base():\n    return 2"}
    out = pre_edit_context(graph, "edit_file", args)
    assert "Definition:" in out
    assert "b.py:1" in out
    assert "Called by" in out
    assert "mid" in out  # mid() calls base()


def test_pre_edit_context_caps_at_max_callers(tmp_path: Path):
    # base() is called by five distinct functions; cap at 2.
    callers = "\n".join(f"def caller{i}():\n    return base()\n" for i in range(5))
    (tmp_path / "callers.py").write_text(callers)
    (tmp_path / "b.py").write_text("def base():\n    return 1\n")
    g = build_index(tmp_path)
    args = {"path": "b.py", "old": "def base():\n    return 1", "new": "def base():\n    return 2"}
    out = pre_edit_context(g, "edit_file", args, max_callers=2)
    assert "Called by 5 site(s)" in out
    # Exactly 2 caller bullet lines rendered, plus a "… and N more" line.
    caller_lines = [ln for ln in out.splitlines() if ln.strip().startswith("- caller")]
    assert len(caller_lines) == 2
    assert "and 3 more" in out


def test_pre_edit_context_empty_when_symbol_unknown(graph):
    args = {"path": "z.py", "old": "def ghost():\n    pass", "new": "def ghost():\n    return 1"}
    out = pre_edit_context(graph, "edit_file", args)
    assert out == ""


def test_pre_edit_context_empty_when_no_symbol_recoverable(graph):
    # `old`/`new` don't contain a def/class line -> nothing to look up.
    args = {"path": "a.py", "old": "return base()", "new": "return base() + 1"}
    out = pre_edit_context(graph, "edit_file", args)
    assert out == ""


def test_pre_edit_context_write_file_uses_content(graph):
    args = {"path": "c.py", "content": "def mid():\n    return base()\n"}
    out = pre_edit_context(graph, "write_file", args)
    assert "Definition:" in out
    assert "mid" in out


def test_pre_edit_context_ignores_non_edit_tools(graph):
    args = {"path": "b.py"}
    assert pre_edit_context(graph, "read_file", args) == ""


def test_pre_edit_context_noop_when_graph_absent():
    args = {"path": "b.py", "old": "def base():\n    return 1", "new": "def base():\n    return 2"}
    assert pre_edit_context(None, "edit_file", args) == ""


# --- H2.2 — error-symbol resolution -----------------------------------------


def test_extract_candidate_symbols_from_traceback():
    trace = (
        'Traceback (most recent call last):\n'
        '  File "a.py", line 4, in top\n'
        '    return mid()\n'
        '  File "a.py", line 7, in mid\n'
        '    return base()\n'
        "NameError: name 'base' is not defined\n"
    )
    names = extract_candidate_symbols(trace)
    assert "top" in names
    assert "mid" in names
    assert "base" in names


def test_extract_candidate_symbols_dedups_order_preserving():
    trace = "call base() then base() again, also 'base'"
    names = extract_candidate_symbols(trace)
    assert names.count("base") == 1


def test_extract_candidate_symbols_empty_on_garbage():
    assert extract_candidate_symbols("") == []
    assert extract_candidate_symbols("   ***  \x00\x01 ") == []


def test_extract_candidate_symbols_caps(graph):
    text = " ".join(f"'name{i}'" for i in range(50))
    names = extract_candidate_symbols(text, max_symbols=5)
    assert len(names) == 5


def test_resolve_error_symbols_attaches_definitions(graph):
    trace = (
        'Traceback (most recent call last):\n'
        '  File "a.py", line 4, in top\n'
        '    return mid()\n'
        '  File "a.py", line 7, in mid\n'
        '    return base()\n'
        "NameError: name 'base' is not defined\n"
    )
    out = resolve_error_symbols(graph, trace)
    assert "a.py:3" in out or "a.py" in out  # top's def line
    assert "b.py:1" in out  # base's def line
    assert "base" in out and "mid" in out and "top" in out


def test_resolve_error_symbols_dedups_repeated_names(graph):
    trace = "base() failed, then base() failed again — 'base' 'base'"
    out = resolve_error_symbols(graph, trace)
    # base's def line appears exactly once even though the name repeats 4x.
    assert out.count("b.py:1") == 1


def test_resolve_error_symbols_caps_per_turn(tmp_path: Path):
    (tmp_path / "many.py").write_text(
        "\n".join(f"def sym{i}():\n    pass\n" for i in range(20))
    )
    g = build_index(tmp_path)
    trace = " ".join(f"'sym{i}'" for i in range(20))
    out = resolve_error_symbols(g, trace, max_symbols=3)
    resolved_lines = [ln for ln in out.splitlines() if "many.py:" in ln]
    assert len(resolved_lines) == 3


def test_resolve_error_symbols_empty_on_unresolvable(graph):
    assert resolve_error_symbols(graph, "totally 'unrelated' 'noise' here") == ""


def test_resolve_error_symbols_empty_on_malformed_trace(graph):
    assert resolve_error_symbols(graph, "\x00\x01***garbage***") == ""


def test_resolve_error_symbols_noop_when_graph_absent():
    assert resolve_error_symbols(None, "File \"a.py\", line 1, in base") == ""


def test_resolve_error_symbols_noop_on_empty_text(graph):
    assert resolve_error_symbols(graph, "") == ""
