"""Wire the verifier into the loop's after_tool hook (H1.3, ADR-0012).

Builds a `CompositeVerifier` from `[verify]` config and returns an `after_tool`
callable for `AgentLoop`. On a mutating tool:

  1. Derive the changed paths (from `path` args; run_bash → verify everything).
  2. Run the verifier. If it passes, append nothing.
  3. If it fails, append the exact error so the model repairs next turn — UNLESS
     the per-boundary repair budget is exhausted, in which case revert the edit
     via the checkpointer and append a "gave up, reverted" message. A model
     mistake is never silently shipped.

The budget is tracked here (the hook is otherwise stateless): consecutive
failures on the *same target* count toward `max_repair_attempts`; a pass resets
the counter. This is a pragmatic per-file approximation of "per edit boundary".
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from nerva_agent.verify import (
    CompositeVerifier, PyCompileVerifier, CommandVerifier, format_failure,
)

# Tools whose target file we can name (for scoping the verifier + revert).
_PATH_TOOLS = {"write_file", "edit_file"}


def build_verifier(workspace: Path, cfg: dict):
    """A CompositeVerifier from the [verify] config, or None if disabled/empty."""
    if not cfg.get("enabled"):
        return None
    verifiers = []
    if cfg.get("pycompile", True):
        verifiers.append(PyCompileVerifier(workspace))
    for cmd in cfg.get("commands", []):
        verifiers.append(CommandVerifier(workspace, cmd))
    return CompositeVerifier(verifiers) if verifiers else None


def make_verify_hook(
    workspace: Path,
    verifier,
    *,
    max_repair_attempts: int = 3,
    checkpointer=None,
    emit: "Callable[[str], None] | None" = None,
):
    """Return an after_tool(tool, args, observation) hook that verifies edits.

    `checkpointer` (if given) provides `undo_last()` to revert when the repair
    budget is exhausted. `emit` optionally reports progress (dim CLI line).
    """
    state = {"target": None, "fails": 0}

    def _paths(tool: str, args: dict) -> list[str]:
        if tool in _PATH_TOOLS:
            p = args.get("path")
            return [p] if isinstance(p, str) and p else []
        return []  # run_bash: no named path; verifier runs project-wide checks

    def hook(tool: str, args: dict, observation: str):
        if verifier is None:
            return None
        changed = _paths(tool, args)
        result = verifier.check(changed)
        if result.ok:
            state["target"], state["fails"] = None, 0
            return None

        # Track consecutive failures on the same target.
        target = changed[0] if changed else "<workspace>"
        if state["target"] != target:
            state["target"], state["fails"] = target, 0
        state["fails"] += 1

        if state["fails"] >= max_repair_attempts:
            # Budget exhausted: revert this edit and stop asking the model to fix it.
            reverted = ""
            if checkpointer is not None:
                try:
                    desc = checkpointer.undo_last()
                    reverted = f" Reverted the change ({desc})." if desc else ""
                except Exception:  # noqa: BLE001 - revert is best-effort
                    pass
            state["target"], state["fails"] = None, 0
            msg = (f"VERIFICATION still failing after {max_repair_attempts} attempts "
                   f"({result.checker}).{reverted} Stop and reconsider the approach; "
                   f"do not keep retrying the same edit.\n{result.errors}")
            if emit:
                emit(f"verify: gave up after {max_repair_attempts} attempts on {target}")
            return msg

        if emit:
            emit(f"verify: failed ({result.checker}) — repair {state['fails']}/{max_repair_attempts}")
        return format_failure(result)

    return hook
