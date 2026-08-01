"""Tests for the code-graph indexer (F14.1, ADR-0008).

Builds a small fixture repo in tmp_path and asserts the ast-based indexer
extracts defs/imports/calls for Python, resolves callers/importers, respects
ignore globs, uses the regex fallback for non-Python, and never crashes on a
syntax error.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nerva_agent.code_graph.indexer import (
    build_index, load_or_build_index, index_signature, CodeGraph,
)


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


# --- incremental re-index (F14.4) -------------------------------------------

def test_reindex_file_picks_up_new_symbol(tmp_path):
    g = build_index(_repo(tmp_path))
    assert g.resolve("added") == []
    # Add a new function and re-index just that file.
    (tmp_path / "pkg" / "util.py").write_text(
        "def helper(x):\n    return x\n\ndef added():\n    return helper(1)\n")
    g.reindex_file("pkg/util.py")
    assert [s.qualname for s in g.resolve("added")] == ["added"]
    # The new call edge is present too.
    assert "helper" in g.symbols["added"].calls


def test_reindex_removes_stale_symbols(tmp_path):
    g = build_index(_repo(tmp_path))
    assert g.resolve("main")  # present initially
    # Rewrite core.py without main().
    (tmp_path / "pkg" / "core.py").write_text("def only():\n    return 1\n")
    g.reindex_file("pkg/core.py")
    assert g.resolve("main") == []       # stale symbol gone
    assert g.resolve("Registry") == []   # and its class
    assert [s.qualname for s in g.resolve("only")] == ["only"]


def test_remove_file_drops_all_its_symbols(tmp_path):
    g = build_index(_repo(tmp_path))
    g.remove_file("pkg/util.py")
    assert "pkg/util.py" not in g.files
    assert g.resolve("helper") == []


def test_reindex_deleted_file_removes_it(tmp_path):
    g = build_index(_repo(tmp_path))
    (tmp_path / "pkg" / "util.py").unlink()
    g.reindex_file("pkg/util.py")  # file gone on disk
    assert "pkg/util.py" not in g.files
    assert g.resolve("helper") == []


def test_reindex_matches_full_rebuild(tmp_path):
    g = build_index(_repo(tmp_path))
    (tmp_path / "pkg" / "core.py").write_text("def fresh():\n    return 2\n")
    g.reindex_file("pkg/core.py")
    rebuilt = build_index(tmp_path)
    assert set(g.symbols) == set(rebuilt.symbols)


# --- W3 (ADR-0020): persistence + incremental load ---------------------------

def test_to_dict_from_dict_round_trips(tmp_path):
    g = build_index(_repo(tmp_path))
    restored = CodeGraph.from_dict(tmp_path, g.to_dict())
    assert set(restored.symbols) == set(g.symbols)
    assert set(restored.files) == set(g.files)
    # The name index is rebuilt so lookups still work after a round-trip.
    assert restored.resolve("helper")
    assert restored.callers_of("helper")  # main/dispatch still resolve


def test_from_dict_rejects_version_mismatch(tmp_path):
    g = build_index(_repo(tmp_path))
    data = g.to_dict()
    data["version"] = 999
    with pytest.raises(ValueError):
        CodeGraph.from_dict(tmp_path, data)


def test_load_or_build_creates_cache_first_time(tmp_path):
    cache = tmp_path / ".aibot" / "code_graph.json"
    g = load_or_build_index(_repo(tmp_path), cache)
    assert cache.is_file()                       # cache written
    assert "main" in g.symbols                   # graph is correct
    # Second load reads the cache and yields an equal graph.
    g2 = load_or_build_index(tmp_path, cache)
    assert set(g2.symbols) == set(g.symbols)


def test_load_or_build_reindexes_only_changed_file(tmp_path):
    import os, time
    cache = tmp_path / ".aibot" / "code_graph.json"
    load_or_build_index(_repo(tmp_path), cache)          # seed the cache
    # Change one file (bump mtime deterministically) and reload.
    core = tmp_path / "pkg" / "core.py"
    core.write_text("def only_this():\n    return 1\n")
    os.utime(core, (time.time() + 10, time.time() + 10))
    g = load_or_build_index(tmp_path, cache)
    assert "only_this" in g.symbols                       # picked up the change
    assert "main" not in g.symbols                        # old symbol gone
    assert g.resolve("helper")                            # unchanged file untouched


def test_load_or_build_drops_deleted_file(tmp_path):
    import os, time
    cache = tmp_path / ".aibot" / "code_graph.json"
    load_or_build_index(_repo(tmp_path), cache)
    (tmp_path / "pkg" / "util.py").unlink()
    g = load_or_build_index(tmp_path, cache)
    assert "pkg/util.py" not in g.files
    assert g.resolve("helper") == []


def test_load_or_build_falls_back_on_corrupt_cache(tmp_path):
    cache = tmp_path / ".aibot" / "code_graph.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("{ not valid json ]")               # corrupt
    g = load_or_build_index(_repo(tmp_path), cache)       # must not raise
    assert "main" in g.symbols                            # full rebuild happened
    # And the corrupt cache was overwritten with a valid one.
    import json
    assert json.loads(cache.read_text())["version"] == CodeGraph.CACHE_VERSION


def test_index_signature_lists_indexable_files(tmp_path):
    sig = index_signature(_repo(tmp_path))
    assert "pkg/core.py" in sig and "pkg/util.py" in sig
    assert all(isinstance(v, float) for v in sig.values())
