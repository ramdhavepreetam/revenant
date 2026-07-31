"""Tests for the graph retrieval tools (F14.2, ADR-0008).

Builds a fixture repo, indexes it, and drives defn_of / who_calls / neighbors /
impact_of through the registry Tool wrappers. Asserts read-only flags and the
graceful "unknown symbol" degrade.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nerva_agent.code_graph.indexer import build_index
from nerva_agent.code_graph.tools import build_code_graph_tools


@pytest.fixture
def tools_and_graph(tmp_path):
    (tmp_path / "a.py").write_text(
        "from b import base\n"
        "\n"
        "def top():\n"
        "    return mid()\n"
        "\n"
        "def mid():\n"
        "    return base()\n"
    )
    (tmp_path / "b.py").write_text("def base():\n    return 1\n")
    graph = build_index(tmp_path)
    tools = {t.name: t for t in build_code_graph_tools(graph)}
    return tools, graph


def test_tools_are_read_only(tools_and_graph):
    tools, _ = tools_and_graph
    for t in tools.values():
        assert t.parallel_safe is True
        assert t.mutating is False
        assert t.requires_approval is False


def test_defn_of_returns_location_and_snippet(tools_and_graph):
    tools, _ = tools_and_graph
    out = tools["defn_of"].invoke({"symbol": "base"})
    assert "b.py:1" in out
    assert "def base" in out


def test_defn_of_unknown_symbol(tools_and_graph):
    tools, _ = tools_and_graph
    out = tools["defn_of"].invoke({"symbol": "ghost"})
    assert "No symbol named" in out
    assert "search" in out


def test_who_calls_lists_call_sites(tools_and_graph):
    tools, _ = tools_and_graph
    out = tools["who_calls"].invoke({"symbol": "base"})
    assert "mid" in out  # mid() calls base()


def test_who_calls_known_but_uncalled(tools_and_graph):
    tools, _ = tools_and_graph
    out = tools["who_calls"].invoke({"symbol": "top"})  # nothing calls top()
    assert "No indexed callers" in out


def test_neighbors_shows_imports_and_symbols(tools_and_graph):
    tools, _ = tools_and_graph
    out = tools["neighbors"].invoke({"path": "a.py"})
    assert "imports: b" in out
    assert "top" in out and "mid" in out


def test_neighbors_unknown_file(tools_and_graph):
    tools, _ = tools_and_graph
    out = tools["neighbors"].invoke({"path": "nope.py"})
    assert "not in the code graph" in out


def test_impact_of_transitive_callers(tools_and_graph):
    tools, _ = tools_and_graph
    # base <- mid <- top : changing base impacts mid (1 hop) and top (2 hops).
    out = tools["impact_of"].invoke({"symbol": "base"})
    assert "mid" in out
    assert "top" in out
    assert "[1]" in out and "[2]" in out


def test_impact_of_leaf_symbol(tools_and_graph):
    tools, _ = tools_and_graph
    out = tools["impact_of"].invoke({"symbol": "top"})  # nothing calls top
    assert "no indexed callers" in out.lower()


def test_impact_of_unknown(tools_and_graph):
    tools, _ = tools_and_graph
    assert "No symbol named" in tools["impact_of"].invoke({"symbol": "ghost"})


# --- structure-aware packing (F14.3) ----------------------------------------

from nerva_agent.code_graph.tools import pack_symbol_context


def test_pack_symbol_context_includes_def_and_callers(tools_and_graph):
    _tools, graph = tools_and_graph
    out = pack_symbol_context(graph, "base")
    assert "Definition:" in out
    assert "b.py:1" in out
    assert "Called by" in out
    assert "mid" in out  # mid() calls base()


def test_pack_symbol_context_unknown_is_empty(tools_and_graph):
    _tools, graph = tools_and_graph
    assert pack_symbol_context(graph, "ghost") == ""
