"""Rolling session summarization for the companion harness (Phase 3).

Keeps long conversations coherent without bloating the context window: every N
turns, the oldest not-yet-summarized messages are folded into a compact running
summary by a SMALL, factual model (gemma) — kept separate from the RP-tuned
companion model so summaries stay clean. The summary is stored on the conversation
row (SQLite, source of truth) and injected as "story so far" each turn; the
summarized raw turns then drop out of the live window via the context budgeter.

Companion-aware: when a companion profile is supplied, the summarizer is told the
companion's name, archetype, and relationship so it preserves character voice and
relationship facts rather than writing generic third-person notes.
"""
from __future__ import annotations

from nerva_core.local_llm_writer import ChatConfig, call_model, LocalLLMError

# Summarize once at least this many messages sit unsummarized beyond the live window.
SUMMARY_TRIGGER = 12
# How many of the oldest unsummarized messages to fold in per pass.
SUMMARY_BATCH = 8
# Always leave at least this many recent messages as raw turns (never summarized).
KEEP_RAW_TAIL = 8


def _summary_config(model: str, base_url: str) -> ChatConfig:
    return ChatConfig(
        backend="ollama",
        base_url=base_url,
        model=model,
        temperature=0.3,        # factual, low-variance
        top_p=0.9,
        repeat_penalty=1.05,
        min_tokens=60,
        max_tokens=300,
        context_messages=64,
        system_prompt="",
    )


def _companion_context_lines(companion: dict) -> list[str]:
    """Extract a concise identity brief from the companion profile dict."""
    if not companion:
        return []
    display_name = str(companion.get("display_name") or "Companion").strip()
    compiled = companion.get("compiled_profile") if isinstance(companion.get("compiled_profile"), dict) else {}
    archetype = str(compiled.get("archetype") or "").strip()
    relationship = str(compiled.get("relationship_to_user") or "").strip()
    user_role = str(compiled.get("user_role") or "the user").strip()
    tone = str(compiled.get("tone") or "").strip()
    lines = [f"Companion name: {display_name}"]
    if archetype:
        lines.append(f"Companion archetype: {archetype}")
    if relationship:
        lines.append(f"Relationship to user: {relationship}")
    if user_role:
        lines.append(f"User role: {user_role}")
    if tone:
        lines.append(f"Companion tone/voice: {tone}")
    return lines


def _build_summary_prompt(
    prior_summary: str,
    batch: list[dict[str, str]],
    companion: dict | None = None,
) -> list[dict[str, str]]:
    companion_lines = _companion_context_lines(companion or {})
    companion_brief = ("\n".join(companion_lines) + "\n\n") if companion_lines else ""

    display_name = str((companion or {}).get("display_name") or "Companion").strip()
    transcript = "\n".join(
        f"{('User' if m.get('role') == 'user' else display_name)}: {m.get('content','').strip()}"
        for m in batch
        if m.get("content", "").strip()
    )

    system = (
        "You compress an ongoing companion conversation into a running memory note. "
        "Write a tight, factual third-person summary preserving: who the characters are, "
        "the relationship dynamic and emotional tone, key facts and events from this batch, "
        "stated preferences, desires, or boundaries, and the current emotional state. "
        "Use the companion's actual name when referring to them — not generic labels like 'the AI'. "
        "Be concise and faithful to their voice. Do not add invented content. "
        "Keep it under 220 words. Merge with the existing note without repeating it."
    )
    user = (
        companion_brief
        + (f"Existing memory note:\n{prior_summary.strip()}\n\n" if prior_summary.strip() else "")
        + f"New conversation since then:\n{transcript}\n\n"
        "Updated memory note:"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def maybe_summarize(
    store,
    conversation_id: str,
    *,
    companion_id: str = "",
    companion: dict | None = None,
    summary_model: str = "gemma:latest",
    base_url: str = "http://localhost:11434",
    memory=None,
) -> bool:
    """If enough new turns have accumulated, fold the oldest into the rolling summary.

    Returns True if a summary pass ran. Best-effort: any failure is swallowed so a
    summary hiccup never breaks the chat turn (the raw turns simply stay in context).
    """
    try:
        messages = store.get_messages(conversation_id)
        already = store.get_summarized_count(conversation_id)
        # Unsummarized messages, excluding the raw tail we always keep verbatim.
        summarizable = len(messages) - already - KEEP_RAW_TAIL
        if summarizable < SUMMARY_TRIGGER:
            return False

        batch = messages[already: already + SUMMARY_BATCH]
        if not batch:
            return False

        prior = store.get_summary(conversation_id)
        config = _summary_config(summary_model, base_url)
        prompt = _build_summary_prompt(prior, batch, companion)
        new_summary = call_model(config, prompt).strip()
        # Strip a leading "Summary:"/"Updated memory note:" label the small model
        # sometimes prepends, and any surrounding markdown emphasis.
        import re
        new_summary = re.sub(
            r"^[\*\s]*(updated memory note|memory note|summary)\s*:?\s*[\*\s]*",
            "", new_summary, flags=re.IGNORECASE,
        ).strip()
        if not new_summary:
            return False

        store.set_summary(conversation_id, new_summary, already + len(batch))
        # Index summary into agent memory so past sessions are reachable via recall.
        if companion_id and memory is not None:
            try:
                memory.remember_summary(companion_id, conversation_id, new_summary)
            except Exception:  # noqa: BLE001
                pass
        return True
    except (LocalLLMError, Exception):  # noqa: BLE001 - never break the chat turn
        return False
