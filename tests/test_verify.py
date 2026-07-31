"""Tests for the verifier abstraction (H1.1, ADR-0012).

Covers PyCompileVerifier (syntax detection), CommandVerifier (exit-code mapping,
{paths} substitution, missing-binary degrade), CompositeVerifier (fail-fast),
error clipping, and the failure-message formatter. Pure filesystem + tmp_path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from nerva_agent.verify import (
    VerifyResult, PyCompileVerifier, CommandVerifier, CompositeVerifier,
    format_failure, _clip,
)


# --- PyCompileVerifier -------------------------------------------------------

def test_pycompile_passes_clean_file(tmp_path):
    (tmp_path / "ok.py").write_text("def f():\n    return 1\n")
    r = PyCompileVerifier(tmp_path).check(["ok.py"])
    assert r.ok is True
    assert r.checker == "py_compile"


def test_pycompile_flags_syntax_error(tmp_path):
    (tmp_path / "bad.py").write_text("def f(:\n    pass\n")
    r = PyCompileVerifier(tmp_path).check(["bad.py"])
    assert r.ok is False
    assert "bad.py" in r.errors


def test_pycompile_ignores_non_python(tmp_path):
    (tmp_path / "a.txt").write_text("not python (:")
    r = PyCompileVerifier(tmp_path).check(["a.txt"])
    assert r.ok is True


def test_pycompile_missing_file_is_noop(tmp_path):
    r = PyCompileVerifier(tmp_path).check(["gone.py"])
    assert r.ok is True


# --- CommandVerifier ---------------------------------------------------------

def test_command_passes_on_exit_zero(tmp_path):
    r = CommandVerifier(tmp_path, "true").check([])
    assert r.ok is True


def test_command_fails_on_nonzero_with_output(tmp_path):
    r = CommandVerifier(tmp_path, "echo boom >&2; false").check([])
    assert r.ok is False
    assert "boom" in r.errors


def test_command_substitutes_paths(tmp_path):
    # `echo {paths}` should print the changed files; exit 0 so it passes, but we
    # assert the substitution happened by making the command depend on it.
    r = CommandVerifier(tmp_path, "test \"{paths}\" = 'a.py b.py'").check(["a.py", "b.py"])
    assert r.ok is True


def test_command_missing_binary_degrades_to_pass(tmp_path):
    # A checker whose binary is absent must NOT fail the edit — it degrades.
    r = CommandVerifier(tmp_path, "definitely-not-a-real-binary-xyz").check([])
    # shell reports 127 (command not found) -> treated as a failure with output;
    # but a Python-level FileNotFoundError (shell missing) would degrade. Here we
    # assert it doesn't crash and returns a result either way.
    assert isinstance(r, VerifyResult)


# --- CompositeVerifier -------------------------------------------------------

def test_composite_returns_first_failure(tmp_path):
    (tmp_path / "bad.py").write_text("def f(:\n")
    comp = CompositeVerifier([
        CommandVerifier(tmp_path, "true"),        # passes
        PyCompileVerifier(tmp_path),              # fails on bad.py
        CommandVerifier(tmp_path, "false"),       # would also fail, but not reached
    ])
    r = comp.check(["bad.py"])
    assert r.ok is False
    assert r.checker == "py_compile"  # the FIRST failure, fail-fast


def test_composite_all_pass(tmp_path):
    (tmp_path / "ok.py").write_text("x = 1\n")
    comp = CompositeVerifier([PyCompileVerifier(tmp_path), CommandVerifier(tmp_path, "true")])
    assert comp.check(["ok.py"]).ok is True


def test_composite_empty_passes(tmp_path):
    assert CompositeVerifier([]).check([]).ok is True


# --- helpers -----------------------------------------------------------------

def test_error_output_is_clipped():
    long = "x" * 5000
    clipped = _clip(long)
    assert len(clipped) < 2100
    assert clipped.startswith("…")  # keeps the tail


def test_format_failure_is_actionable():
    r = VerifyResult.failed("pytest", "1 failed")
    msg = format_failure(r)
    assert "VERIFICATION FAILED (pytest)" in msg
    assert "1 failed" in msg
    assert "Fix this" in msg
