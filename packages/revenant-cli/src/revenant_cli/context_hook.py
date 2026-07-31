"""Wire proactive context injection into the loop's after_tool hook (H2, ADR-0013).

The engine logic (`nerva_agent.context_inject`) is pure: it takes a `CodeGraph`
(or `None`) and produces short observation text. This module is the CLI-tier
seam (ADR-0002) that composes it into something `AgentLoop` can call, following
the exact shape of `revenant_cli.verify_hook.make_verify_hook`:

  - H2.1 (`inject_on_edit`): after a mutating edit tool runs, append the target
    symbol's definition + immediate callers to the SAME observation the model
    reads on its very next turn. `before_tool` can't do this (its return value
    is discarded by `AgentLoop` — see agent_loop.py — and by the time it fires
    the model has already committed to the edit), so `after_tool` is the seam
    that actually lands the text in front of the model, exactly like H1's
    verify-repair feedback. This still satisfies the ADR's intent: the model
    sees the callers before its *next* edit to the same neighborhood, without
    having to call a graph tool itself.
  - H2.2 (`resolve_errors`): when the observation looks like a failure (an
    "ERROR:" tool error, or a H1 "VERIFICATION FAILED" note already appended),
    extract candidate symbol names and attach their definitions.

Both pieces degrade to nothing when the graph is absent, the config flag is
off, or nothing resolves — additive, never a behavior change to today's loop.
"""
from __future__ import annotations

from typing import Callable

from nerva_agent.code_graph.indexer import CodeGraph
from nerva_agent.context_inject import pre_edit_context, resolve_error_symbols

# Substrings that mark an observation as error-shaped, worth trying H2.2 on.
# Covers a raw ToolError ("ERROR: ...") and the H1 verify-repair feedback
# (format_failure in nerva_agent/verify.py starts with "VERIFICATION FAILED").
_ERROR_MARKERS = ("ERROR:", "VERIFICATION FAILED", "Traceback (most recent call last)")


def make_context_hook(
    graph: "CodeGraph | None",
    *,
    inject_on_edit: bool = True,
    resolve_errors: bool = True,
    max_callers: int = 5,
):
    """Return an after_tool(tool, args, observation) hook for H2, or None if
    there's nothing to do (no graph, or both sub-features disabled).

    Composes with `verify_hook`'s hook via `compose_after_tool_hooks` — this
    function only returns the H2 half.
    """
    if graph is None or not (inject_on_edit or resolve_errors):
        return None

    def hook(tool: str, args: dict, observation: str) -> "str | None":
        parts: list[str] = []
        if inject_on_edit:
            block = pre_edit_context(graph, tool, args, max_callers=max_callers)
            if block:
                parts.append(block)
        if resolve_errors and any(marker in observation for marker in _ERROR_MARKERS):
            block = resolve_error_symbols(graph, observation)
            if block:
                parts.append(block)
        return "\n\n".join(parts) if parts else None

    return hook


def compose_after_tool_hooks(*hooks: "Callable | None"):
    """Chain several after_tool hooks into one, appending each non-empty result.

    Runs every non-None hook (even if an earlier one raises or returns nothing)
    so, e.g., a verify-hook failure and H2 error-resolution can both contribute
    to the same observation. A single hook's exception is swallowed here too,
    mirroring AgentLoop's own after_tool error-swallowing, so hook order can
    never make a working hook's contribution disappear because a later one
    misbehaves. Returns None if there are no hooks (so callers can skip wiring
    `after_tool` entirely, matching today's behavior when nothing is configured).
    """
    active = [h for h in hooks if h is not None]
    if not active:
        return None

    def combined(tool: str, args: dict, observation: str) -> "str | None":
        extras: list[str] = []
        current_observation = observation
        for h in active:
            try:
                extra = h(tool, args, current_observation)
            except Exception:  # noqa: BLE001 - one hook's failure must not skip the rest
                extra = None
            if extra:
                extras.append(extra)
                current_observation = f"{current_observation}\n\n{extra}"
        return "\n\n".join(extras) if extras else None

    return combined
