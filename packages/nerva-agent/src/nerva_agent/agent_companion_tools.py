"""Companion tool subset for the Revenant harness (P5).

The companion front-end runs the SAME AgentLoop as the coding CLI, but with a
DIFFERENT registry: no filesystem, no shell. The companion can only touch its own
memory and reminders. This is the "one loop, two front-ends" payoff — the loop,
protocol, and parser are shared; only the tool set differs.

Tools:
  memory_search(query)          -> recall relevant remembered facts (read-only).
  memory_save(category, content)-> record a fact about the user/relationship.
  set_reminder(text)            -> note something to bring up later (an episode).

Safety: `memory_save` routes through PersonalMemoryStore. The `boundary` category
is ALWAYS written as `pending` (human-gated) — a wrongly-admitted boundary in a
companion is a real risk, so it never auto-activates. Everything else is written
active. This mirrors the existing advisor rule in aibot_personal_memory.learn.

`build_companion_tools(memory, personal_memory, store, companion_id)` takes the
already-constructed stores (e.g. from web_app's STATE) so this module has no web
dependency and is unit-testable with fakes.
"""
from __future__ import annotations

from typing import Any

from nerva_agent.agent_tools import Tool, ToolParam

# Categories the companion may write. Kept close to PersonalMemoryStore's known set.
_SAVE_CATEGORIES = {
    "identity_fact", "preference", "voice_preference", "story_fact",
    "companion_style", "relationship_state", "need", "boundary",
}
# Boundaries are never auto-activated — always held for human review.
_GATED_CATEGORIES = {"boundary"}


def _memory_search(memory: Any, companion_id: str, query: str) -> str:
    if not query.strip():
        return "(no query)"
    try:
        hits = memory.recall(query, limit=5, companion_id=companion_id)
    except Exception as exc:  # noqa: BLE001 - a recall failure must not crash the turn
        return f"(memory search unavailable: {exc})"
    if not hits:
        return "(nothing relevant remembered)"
    return "\n".join(f"- {h}" for h in hits)


def _memory_save(personal_memory: Any, memory: Any, companion_id: str,
                 category: str, content: str) -> str:
    content = (content or "").strip()
    if not content:
        return "(nothing to save)"
    cat = category.strip().lower().replace(" ", "_")
    if cat not in _SAVE_CATEGORIES:
        cat = "preference"  # safest general bucket for an unknown category
    gated = cat in _GATED_CATEGORIES
    status = "pending" if gated else "active"
    saved = personal_memory.create_memory(
        companion_id, cat, content,
        status=status, confidence=0.8, source="companion_agent",
    )
    if saved is None:
        return "(could not save — empty or duplicate)"
    # Mirror active memories into the semantic index so future recall finds them.
    if not gated:
        try:
            memory.remember_note(companion_id, cat, content)
        except Exception:  # noqa: BLE001
            pass
    if gated:
        return (
            f"Noted a possible boundary ({content!r}). It is held for the user to "
            "review before it takes effect — do not treat it as confirmed yet."
        )
    return f"Saved to memory ({cat}): {content}"


def _set_reminder(store: Any, companion_id: str, text: str) -> str:
    text = (text or "").strip()
    if not text:
        return "(nothing to remember)"
    try:
        store.add_episode(companion_id, f"Reminder: {text}")
    except Exception as exc:  # noqa: BLE001
        return f"(could not set reminder: {exc})"
    return f"I'll remember to bring this up: {text}"


def build_companion_tools(
    memory: Any,
    personal_memory: Any,
    store: Any,
    companion_id: str,
) -> list[Tool]:
    """Build the companion's memory/reminder tool subset. No fs/shell tools.

    memory_search is read-only (parallel_safe). memory_save and set_reminder
    persist, but they are intentionally NOT approval-gated at the loop level —
    they're low-risk and part of natural companion behavior; the boundary safety
    gate lives inside memory_save (pending status), not in a y/N prompt.
    """
    return [
        Tool(
            "memory_search",
            "Recall what you remember about this person relevant to a query.",
            [ToolParam("query", "string", "What to look up in memory.")],
            run=lambda query: _memory_search(memory, companion_id, query),
            parallel_safe=True,
        ),
        Tool(
            "memory_save",
            "Remember a fact about the user or your relationship. Categories: "
            "identity_fact, preference, voice_preference, story_fact, "
            "companion_style, relationship_state, need, boundary. A 'boundary' is "
            "held for the user to confirm before it takes effect.",
            [
                ToolParam("category", "string", "One of the allowed memory categories."),
                ToolParam("content", "string", "The fact to remember, in one sentence."),
            ],
            run=lambda category, content: _memory_save(
                personal_memory, memory, companion_id, category, content
            ),
        ),
        Tool(
            "set_reminder",
            "Note something to bring up in a later conversation.",
            [ToolParam("text", "string", "What to remember to bring up.")],
            run=lambda text: _set_reminder(store, companion_id, text),
        ),
    ]
