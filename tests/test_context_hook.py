"""Tests for the H2 CLI-tier after_tool wiring (ADR-0013).

`make_context_hook` composes the pure `nerva_agent.context_inject` helpers into
an after_tool(tool, args, observation) callable; `compose_after_tool_hooks`
chains it with the H1 verify hook. These tests exercise the composition and
config-flag plumbing, not the extraction regexes themselves (covered in
tests/test_context_inject.py).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nerva_agent.code_graph.indexer import build_index
from revenant_cli.context_hook import compose_after_tool_hooks, make_context_hook


@pytest.fixture
def graph(tmp_path: Path):
    (tmp_path / "a.py").write_text(
        "def top():\n    return mid()\n\ndef mid():\n    return 1\n"
    )
    return build_index(tmp_path)


# --- make_context_hook -------------------------------------------------------


def test_make_context_hook_none_when_graph_absent():
    assert make_context_hook(None) is None


def test_make_context_hook_none_when_both_disabled(graph):
    assert make_context_hook(graph, inject_on_edit=False, resolve_errors=False) is None


def test_hook_injects_def_and_callers_on_edit(graph):
    hook = make_context_hook(graph, resolve_errors=False)
    args = {"path": "a.py", "old": "def mid():\n    return 1", "new": "def mid():\n    return 2"}
    out = hook("edit_file", args, "edited a.py (1 replacement)")
    assert out is not None
    assert "Definition:" in out
    assert "Called by" in out and "top" in out


def test_hook_returns_none_when_edit_untargeted(graph):
    hook = make_context_hook(graph, resolve_errors=False)
    out = hook("edit_file", {"path": "a.py", "old": "return 1", "new": "return 2"}, "edited")
    assert out is None


def test_hook_resolves_error_symbols_on_failure_observation(graph):
    hook = make_context_hook(graph, inject_on_edit=False)
    observation = "ERROR: NameError: name 'mid' is not defined"
    out = hook("run_bash", {}, observation)
    assert out is not None
    assert "a.py:4" in out and "mid" in out


def test_hook_ignores_non_error_observation_for_resolution(graph):
    hook = make_context_hook(graph, inject_on_edit=False)
    out = hook("run_bash", {}, "ran successfully, mid() returned 1")
    assert out is None  # no error marker -> H2.2 doesn't even try


def test_hook_inject_on_edit_flag_off(graph):
    hook = make_context_hook(graph, inject_on_edit=False, resolve_errors=False)
    assert hook is None  # both off -> no hook at all, matching make_verify_hook's None convention


def test_hook_respects_max_callers(tmp_path: Path):
    body = "\n".join(f"def caller{i}():\n    return base()\n" for i in range(4))
    (tmp_path / "b.py").write_text("def base():\n    return 1\n\n" + body)
    g = build_index(tmp_path)
    hook = make_context_hook(g, resolve_errors=False, max_callers=1)
    args = {"path": "b.py", "old": "def base():\n    return 1", "new": "def base():\n    return 2"}
    out = hook("edit_file", args, "edited")
    assert "and 3 more" in out


# --- compose_after_tool_hooks -------------------------------------------------


def test_compose_returns_none_with_no_hooks():
    assert compose_after_tool_hooks(None, None) is None


def test_compose_runs_single_hook():
    def h(tool, args, obs):
        return "extra text"
    combined = compose_after_tool_hooks(h, None)
    assert combined("edit_file", {}, "obs") == "extra text"


def test_compose_chains_multiple_hooks_appends_both():
    def verify_hook(tool, args, obs):
        return "VERIFICATION FAILED (x): boom"

    def context_hook(tool, args, obs):
        # Sees the verify hook's contribution if it ran first.
        assert "VERIFICATION FAILED" in obs
        return "[code-graph: definitions]"

    combined = compose_after_tool_hooks(verify_hook, context_hook)
    out = combined("edit_file", {}, "edited a.py")
    assert "VERIFICATION FAILED" in out
    assert "[code-graph: definitions]" in out


def test_compose_swallows_one_hooks_exception():
    def broken(tool, args, obs):
        raise RuntimeError("boom")

    def working(tool, args, obs):
        return "ok"

    combined = compose_after_tool_hooks(broken, working)
    assert combined("edit_file", {}, "obs") == "ok"


def test_compose_all_none_returns_none_result():
    def noop(tool, args, obs):
        return None
    combined = compose_after_tool_hooks(noop, noop)
    assert combined("edit_file", {}, "obs") is None
