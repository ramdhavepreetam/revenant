"""Tests for the code-graph indexer (F14.1, ADR-0008).

Builds a small fixture repo in tmp_path and asserts the ast-based indexer
extracts defs/imports/calls for Python, resolves callers/importers, respects
ignore globs, uses the regex fallback for non-Python, and never crashes on a
syntax error.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nerva_agent.code_graph.indexer import build_index


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "core.py").write_text(
        "import os\n"
        "from pkg.util import helper\n"
        "\n"
        "class Registry:\n"
        "    def dispatch(self, name):\n"
        "        return helper(name)\n"
        "\n"
        "def main():\n"
        "    r = Registry()\n"
        "    return r.dispatch('x')\n"
    )
    (tmp_path / "pkg" / "util.py").write_text(
        "def helper(x):\n"
        "    return x\n"
    )
    return tmp_path


def test_indexes_python_symbols_and_kinds(tmp_path):
    g = build_index(_repo(tmp_path))
    assert "pkg/core.py" in g.files
    quals = set(g.symbols)
    assert "Registry" in quals
    assert "Registry.dispatch" in quals
    assert "main" in quals
    assert g.symbols["Registry"].kind == "class"
    assert g.symbols["Registry.dispatch"].kind == "method"
    assert g.symbols["main"].kind == "function"


def test_captures_imports(tmp_path):
    g = build_index(_repo(tmp_path))
    core = g.files["pkg/core.py"]
    assert "os" in core.imports
    assert "pkg.util" in core.imports


def test_captures_call_edges(tmp_path):
    g = build_index(_repo(tmp_path))
    # main() calls Registry() and dispatch(); dispatch() calls helper().
    assert "dispatch" in g.symbols["main"].calls
    assert "Registry" in g.symbols["main"].calls
    assert "helper" in g.symbols["Registry.dispatch"].calls


def test_callers_of(tmp_path):
    g = build_index(_repo(tmp_path))
    callers = {s.qualname for s in g.callers_of("helper")}
    assert "Registry.dispatch" in callers
    callers2 = {s.qualname for s in g.callers_of("dispatch")}
    assert "main" in callers2


def test_importers_of(tmp_path):
    g = build_index(_repo(tmp_path))
    assert "pkg/core.py" in g.importers_of("pkg.util")


def test_resolve_bare_and_qualified(tmp_path):
    g = build_index(_repo(tmp_path))
    assert [s.qualname for s in g.resolve("dispatch")] == ["Registry.dispatch"]
    assert [s.qualname for s in g.resolve("Registry.dispatch")] == ["Registry.dispatch"]
    assert g.resolve("nonexistent") == []


# --- ignore globs ------------------------------------------------------------

def test_respects_ignore_globs(tmp_path):
    _repo(tmp_path)
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "lib.py").write_text("def vendored(): pass\n")
    (tmp_path / ".gitignore").write_text("vendor/\n")
    g = build_index(tmp_path)
    assert "vendor/lib.py" not in g.files
    assert g.resolve("vendored") == []


# --- fallback + degradation --------------------------------------------------

def test_regex_fallback_for_non_python(tmp_path):
    (tmp_path / "app.js").write_text(
        "import { thing } from './thing';\n"
        "export function render() { return thing(); }\n"
    )
    g = build_index(tmp_path)
    assert "app.js" in g.files
    assert g.files["app.js"].language == "other"
    assert "./thing" in g.files["app.js"].imports
    assert any(s.name == "render" for s in g.symbols.values())


def test_syntax_error_is_recorded_not_raised(tmp_path):
    (tmp_path / "broken.py").write_text("def oops(:\n    pass\n")
    g = build_index(tmp_path)  # must not raise
    assert g.files["broken.py"].parse_error
    assert "syntax error" in g.files["broken.py"].parse_error


def test_non_indexable_files_skipped(tmp_path):
    (tmp_path / "README.md").write_text("# docs\n")
    (tmp_path / "data.json").write_text("{}")
    g = build_index(tmp_path)
    assert "README.md" not in g.files
    assert "data.json" not in g.files


def test_stats(tmp_path):
    g = build_index(_repo(tmp_path))
    s = g.stats()
    assert s["files"] == 2
    assert s["symbols"] >= 3
    assert s["parse_errors"] == 0
