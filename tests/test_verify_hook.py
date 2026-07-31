"""Tests for the verify after_tool hook + repair budget (H1.3, ADR-0012)."""
from __future__ import annotations

from pathlib import Path

import pytest

from revenant_cli.verify_hook import build_verifier, make_verify_hook
from nerva_agent.verify import VerifyResult


class _FakeVerifier:
    """Returns a scripted sequence of ok/fail results."""
    def __init__(self, oks):
        self._oks = iter(oks)
    def check(self, changed_paths):
        ok = next(self._oks)
        return VerifyResult.passed("x") if ok else VerifyResult.failed("pytest", "boom")


class _FakeCheckpointer:
    def __init__(self):
        self.undone = 0
    def undo_last(self):
        self.undone += 1
        return "restored a.py"


def test_build_verifier_none_when_disabled(tmp_path):
    assert build_verifier(tmp_path, {"enabled": False}) is None


def test_build_verifier_composes_pycompile_and_commands(tmp_path):
    v = build_verifier(tmp_path, {"enabled": True, "pycompile": True,
                                  "commands": ["true"]})
    assert v is not None
    assert len(v.verifiers) == 2


def test_hook_passes_appends_nothing(tmp_path):
    hook = make_verify_hook(tmp_path, _FakeVerifier([True]))
    assert hook("write_file", {"path": "a.py"}, "wrote it") is None


def test_hook_failure_appends_repair_message(tmp_path):
    hook = make_verify_hook(tmp_path, _FakeVerifier([False]))
    out = hook("write_file", {"path": "a.py"}, "wrote it")
    assert "VERIFICATION FAILED" in out
    assert "boom" in out


def test_budget_exhaustion_reverts_and_stops(tmp_path):
    cp = _FakeCheckpointer()
    # 3 consecutive failures on the same target, budget = 3 -> 3rd reverts.
    hook = make_verify_hook(tmp_path, _FakeVerifier([False, False, False]),
                            max_repair_attempts=3, checkpointer=cp)
    a = hook("write_file", {"path": "a.py"}, "obs")   # fail 1
    b = hook("write_file", {"path": "a.py"}, "obs")   # fail 2
    c = hook("write_file", {"path": "a.py"}, "obs")   # fail 3 -> revert
    assert "VERIFICATION FAILED" in a and "VERIFICATION FAILED" in b
    assert "still failing after 3 attempts" in c
    assert "Reverted" in c
    assert cp.undone == 1


def test_pass_resets_the_counter(tmp_path):
    # fail, fail, PASS, fail -> the pass resets so we're not near the budget.
    hook = make_verify_hook(tmp_path, _FakeVerifier([False, False, True, False]),
                            max_repair_attempts=3)
    hook("write_file", {"path": "a.py"}, "o")   # fail 1
    hook("write_file", {"path": "a.py"}, "o")   # fail 2
    reset = hook("write_file", {"path": "a.py"}, "o")  # PASS -> None
    after = hook("write_file", {"path": "a.py"}, "o")  # fail 1 again (not 3)
    assert reset is None
    assert "VERIFICATION FAILED" in after
    assert "still failing" not in after


def test_new_target_resets_counter(tmp_path):
    hook = make_verify_hook(tmp_path, _FakeVerifier([False, False]),
                            max_repair_attempts=3)
    hook("write_file", {"path": "a.py"}, "o")          # a.py fail 1
    other = hook("write_file", {"path": "b.py"}, "o")  # b.py fail 1 (fresh count)
    assert "still failing" not in other
