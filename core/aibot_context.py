"""Context assembly for the companion harness (Phase 2).

Replaces "inject everything + keep last 18 turns" with a token-budgeted, priority-
ranked assembler so the working context stays relevant and doesn't overflow as a
conversation grows. Two jobs:

  1. rank_memories()  - score structured memories (recency x confidence x pin) and
                        return the most valuable ones under a count cap.
  2. assemble_context() - fill the model's message list by PRIORITY under a token
                        budget, dropping the lowest-priority content first on overflow.

Priority (highest kept first):
  system prompt  >  pinned/structured memory  >  session summary  >  recent raw
  turns (newest first)  >  current user message (always kept).

Everything here is local and deterministic; SQLite stays the source of truth.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from local_llm_writer import estimate_tokens


def _age_days(updated_at: str) -> float:
    """Days since a memory was last updated; large default if unparseable."""
    if not updated_at:
        return 365.0
    try:
        dt = datetime.fromisoformat(updated_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
    except (ValueError, TypeError):
        return 365.0


def _memory_score(memory: dict[str, Any], query: str) -> float:
    """Higher = more worth injecting. Combines pin, confidence, recency, and a
    light lexical-overlap signal with the current user message."""
    score = 0.0
    if memory.get("pinned"):
        score += 5.0
    score += 2.0 * float(memory.get("confidence") or 0.5)
    # Recency: full credit fresh, decaying to ~0 over ~30 days.
    age = _age_days(str(memory.get("updated_at") or ""))
    score += 2.0 * max(0.0, 1.0 - age / 30.0)
    # Lexical overlap with the query (cheap relevance nudge).
    if query:
        q_words = {w for w in query.lower().split() if len(w) > 3}
        m_words = {w for w in str(memory.get("content") or "").lower().split() if len(w) > 3}
        if q_words and m_words:
            overlap = len(q_words & m_words) / len(q_words)
            score += 3.0 * overlap
    # Boundaries always matter for a companion — never let them rank out.
    if memory.get("category") == "boundary":
        score += 4.0
    return score


def rank_memories(memories: list[dict[str, Any]], query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Return the top `limit` structured memories most worth injecting this turn."""
    scored = sorted(memories, key=lambda m: _memory_score(m, query), reverse=True)
    return scored[:limit]


def format_memory_block(memories: list[dict[str, Any]]) -> str:
    """Render ranked structured memories as a compact, grouped block."""
    if not memories:
        return ""
    # Keep boundaries visually distinct; everything else as continuity facts.
    boundaries = [m for m in memories if m.get("category") == "boundary"]
    others = [m for m in memories if m.get("category") != "boundary"]
    lines: list[str] = []
    if others:
        lines.append("What you remember about this person (use as lived continuity, never recite):")
        lines += [f"- {m.get('content','').strip()}" for m in others if m.get("content")]
    if boundaries:
        lines.append("Boundaries to always respect:")
        lines += [f"- {m.get('content','').strip()}" for m in boundaries if m.get("content")]
    return "\n".join(lines)


def sentences_from_deltas(deltas):
    """Turn a stream of token deltas into a stream of complete sentences.

    The keystone of streaming: emitting whole sentences (not raw tokens) keeps the
    Phase-1 truncation guarantee intact (a partial trailing sentence is simply never
    emitted) and matches what the Kokoro voice wants. Yields (sentence, full_so_far).
    The final partial buffer is intentionally NOT emitted — the caller trims it.
    """
    import re

    buf = ""
    full = ""
    # Emit when we hit terminal punctuation followed by space/newline/end, but not
    # mid-decimal or mid-ellipsis-that-keeps-going.
    boundary = re.compile(r".+?[.!?…][\"'’)\*]*(?=\s|$)", re.DOTALL)
    for delta in deltas:
        buf += delta
        full += delta
        while True:
            m = boundary.match(buf)
            if not m:
                break
            sentence = m.group(0).strip()
            buf = buf[m.end():].lstrip()
            if sentence:
                yield sentence, full


def assemble_context(
    *,
    system_prompt: str,
    memory_block: str,
    session_summary: str,
    history: list[dict[str, str]],
    user_message: str,
    max_context_tokens: int = 3072,
    min_recent_turns: int = 4,
) -> list[dict[str, str]]:
    """Build the model message list under a token budget, by priority.

    `history` is the prior conversation (oldest->newest, excluding the current
    user message). `user_message` is this turn's text (already enriched if needed).
    Returns [{role, content}, ...] ready for call_model.
    """
    # System message = persona + memory block + session summary (all high priority).
    system_parts = [system_prompt.strip()] if system_prompt.strip() else []
    if session_summary.strip():
        system_parts.append(
            "Story so far (summary of earlier conversation, treat as memory):\n"
            f"{session_summary.strip()}"
        )
    if memory_block.strip():
        system_parts.append(memory_block.strip())
    system_content = "\n\n".join(system_parts)

    user_turn = {"role": "user", "content": user_message}
    # Reserve budget for the always-kept pieces.
    used = estimate_tokens(system_content) + estimate_tokens(user_message)
    budget = max(0, max_context_tokens - used)

    # Add raw history newest-first until the budget runs out, but always keep at
    # least the most recent `min_recent_turns` (a companion must remember the last
    # few exchanges even under pressure).
    kept_reversed: list[dict[str, str]] = []
    for i, msg in enumerate(reversed(history)):
        cost = estimate_tokens(msg.get("content", ""))
        if i < min_recent_turns or cost <= budget:
            kept_reversed.append(msg)
            budget -= cost
        else:
            break
    kept_history = list(reversed(kept_reversed))

    messages: list[dict[str, str]] = []
    if system_content:
        messages.append({"role": "system", "content": system_content})
    messages.extend(kept_history)
    messages.append(user_turn)
    return messages
