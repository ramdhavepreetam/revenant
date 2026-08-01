"""Pre-flight checks: is Ollama up and the model pulled? (U1, ADR-0016).

The #1 first-run failure is a cryptic error deep in the first model call because
Ollama isn't running or the model isn't pulled. This runs BEFORE the run starts
and returns an actionable verdict — "start Ollama with `ollama serve`" or "run
`ollama pull <model>`" with the list of what IS available.

Pure/offline and never raises: it reuses `nerva_core.local_llm_writer
.list_local_models` (a single `/api/tags` GET). The CLI hard-fails on a bad
verdict (returns None from `_build_agent` → exit 2), with `--skip-preflight` to
bypass.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from nerva_core.local_llm_writer import list_local_models


@dataclass
class PreflightResult:
    ok: bool
    reachable: bool
    model_present: bool
    available: list[str] = field(default_factory=list)
    message: str = ""


def _model_matches(model: str, available: list[str]) -> bool:
    """Ollama lists `qwen2.5-coder:7b` but a bare `qwen2.5-coder` implies `:latest`.
    Match with and without an implicit `:latest` tag so both forms resolve."""
    if model in available:
        return True
    if ":" not in model and f"{model}:latest" in available:
        return True
    # Configured with an explicit :latest but listed bare (rare) — accept too.
    if model.endswith(":latest") and model[: -len(":latest")] in available:
        return True
    return False


def check(base_url: str, model: str, timeout: int = 3) -> PreflightResult:
    """Verify Ollama is reachable and `model` is pulled."""
    available = list_local_models(base_url, timeout=timeout)

    if available is None:
        return PreflightResult(
            ok=False, reachable=False, model_present=False, available=[],
            message=(
                f"Ollama isn't reachable at {base_url}.\n"
                "  → Start it with:  ollama serve\n"
                "  → Or point Revenant elsewhere:  --base-url URL  (or set OLLAMA_HOST)"
            ),
        )

    if not model or not _model_matches(model, available):
        listed = ", ".join(available) if available else "(none pulled)"
        return PreflightResult(
            ok=False, reachable=True, model_present=False, available=available,
            message=(
                f"Model {model!r} isn't pulled.\n"
                f"  → Pull it with:  ollama pull {model}\n"
                f"  → Available now: {listed}"
            ),
        )

    return PreflightResult(
        ok=True, reachable=True, model_present=True, available=available,
        message=f"Ollama OK · model {model!r} present",
    )
