"""Project knowledge-doc loading for the agent preamble (F6, tier a).

A coding agent grounds far better when it knows the project's conventions up front
instead of guessing. This loads a project instruction file — `REVENANT.md` by
preference, falling back to the `CLAUDE.md` / `AGENTS.md` conventions many repos
already have — from the workspace root and folds it into the system preamble.

Kept deliberately simple: a single file, size-capped so a runaway doc can't blow
the context budget. Intelligent multi-doc retrieval (indexing docs/ into a vector
store and injecting only relevant chunks) is tier (b), layered on later.
"""
from __future__ import annotations

from pathlib import Path

# Checked in order; the first that exists wins. REVENANT.md is the project's own
# convention; the others are common cross-tool instruction files worth honoring.
PROJECT_DOC_NAMES = ("REVENANT.md", "CLAUDE.md", "AGENTS.md")

# Cap the injected doc so a large file can't dominate the window. ~6k chars is a
# few hundred tokens — plenty for conventions, small enough to always fit.
MAX_DOC_CHARS = 6_000


def find_project_doc(workspace: Path) -> Path | None:
    """Return the first existing project instruction file at the workspace root."""
    for name in PROJECT_DOC_NAMES:
        candidate = workspace / name
        if candidate.is_file():
            return candidate
    return None


def load_project_doc(workspace: Path) -> str:
    """Read the project doc's text (size-capped), or '' if none / unreadable."""
    doc = find_project_doc(workspace)
    if doc is None:
        return ""
    try:
        text = doc.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    if len(text) > MAX_DOC_CHARS:
        text = text[:MAX_DOC_CHARS] + "\n\n[... project doc truncated ...]"
    return text


def compose_preamble(base_preamble: str, workspace: Path) -> str:
    """Append the project doc (if any) to the base coding preamble.

    Returns base_preamble unchanged when there's no project doc, so behavior is
    identical in repos without one.
    """
    doc = load_project_doc(workspace)
    if not doc:
        return base_preamble
    return (
        f"{base_preamble}\n\n"
        "--- Project conventions (from the repo's instruction file; follow these) ---\n"
        f"{doc}"
    )
