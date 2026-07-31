"""Verifiers: deterministic checks that catch broken edits (H1.1, ADR-0012).

The core of "the harness carries the model" (ADR-0011): an edit is a *proposal*,
not a fact. After a mutating tool runs, a Verifier checks the result; a failure is
fed back to the model as the next observation so it repairs — instead of shipping
plausible-but-broken code.

Verifiers are pure and offline. Built-ins:
  - PyCompileVerifier  : byte-compile changed .py files (stdlib; catches syntax).
  - CommandVerifier    : run a configured shell command (typecheck / lint / tests);
                         ok == exit 0, errors == captured output tail.
  - CompositeVerifier  : run several in order, stop at the first failure.

A Verifier reports `VerifyResult(ok, errors, checker)`; `errors` is the exact tool
output, fed back verbatim so the model has the precise thing to fix. Verifiers
never raise on a *tool* failure (that's a result); they only degrade (skip) when
the checker itself can't run (e.g. a missing binary).
"""
from __future__ import annotations

import py_compile
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# Keep fed-back output bounded so a chatty failure can't blow the context budget.
_MAX_ERROR_CHARS = 2000
DEFAULT_TIMEOUT = 120.0


@dataclass
class VerifyResult:
    """Outcome of a check. `errors` is exact tool output for the model to repair."""

    ok: bool
    errors: str = ""
    checker: str = ""

    @staticmethod
    def passed(checker: str) -> "VerifyResult":
        return VerifyResult(True, "", checker)

    @staticmethod
    def failed(checker: str, errors: str) -> "VerifyResult":
        return VerifyResult(False, _clip(errors), checker)


class Verifier(Protocol):
    def check(self, changed_paths: list[str]) -> VerifyResult: ...


def _clip(text: str) -> str:
    text = (text or "").strip()
    if len(text) > _MAX_ERROR_CHARS:
        # Keep the TAIL — the actual error/summary is usually at the end.
        text = "…\n" + text[-_MAX_ERROR_CHARS:]
    return text


@dataclass
class PyCompileVerifier:
    """Byte-compile each changed .py file. Cheap, stdlib, catches syntax errors."""

    workspace: Path

    def check(self, changed_paths: list[str]) -> VerifyResult:
        pyfiles = [p for p in changed_paths if p.endswith(".py")]
        for rel in pyfiles:
            target = self.workspace / rel
            if not target.is_file():
                continue
            try:
                py_compile.compile(str(target), doraise=True)
            except py_compile.PyCompileError as exc:
                return VerifyResult.failed("py_compile", f"{rel}: {exc.msg}")
            except OSError:
                continue  # can't read it -> not our failure to report
        return VerifyResult.passed("py_compile")


@dataclass
class CommandVerifier:
    """Run a shell command; ok == exit 0. `{paths}` / `{tests}` are substituted
    with the changed files (space-joined) so a check can be scoped to the edit."""

    workspace: Path
    command: str
    timeout: float = DEFAULT_TIMEOUT

    def check(self, changed_paths: list[str]) -> VerifyResult:
        paths = " ".join(shlex.quote(p) for p in changed_paths)
        cmd = self.command.replace("{paths}", paths).replace("{tests}", paths)
        try:
            proc = subprocess.run(
                cmd, shell=True, cwd=str(self.workspace),
                capture_output=True, text=True, timeout=self.timeout,
            )
        except FileNotFoundError:
            # The checker binary isn't installed — degrade, don't fail the edit.
            return VerifyResult.passed(f"skipped: {self.command}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return VerifyResult.failed(self.command, f"check could not run: {exc}")
        if proc.returncode == 0:
            return VerifyResult.passed(self.command)
        out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        return VerifyResult.failed(self.command, out or f"exit {proc.returncode}")


@dataclass
class CompositeVerifier:
    """Run verifiers in order; return the first failure, else pass."""

    verifiers: list[Verifier]

    def check(self, changed_paths: list[str]) -> VerifyResult:
        for v in self.verifiers:
            result = v.check(changed_paths)
            if not result.ok:
                return result
        return VerifyResult.passed("all checks")


# The observation appended to the model's turn when verification fails. Written
# as a direct instruction so a weaker model repairs on the very next step.
def format_failure(result: VerifyResult) -> str:
    return (
        f"VERIFICATION FAILED ({result.checker}):\n{result.errors}\n"
        "The change you just made did not pass the project's checks. Fix this "
        "before doing anything else; do not proceed until it passes."
    )
