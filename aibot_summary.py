"""Rolling session summarization for the companion harness (Phase 3).

Keeps long conversations coherent without bloating the context window: every N
turns, the oldest not-yet-summarized messages are folded into a compact running
summary by a SMALL, factual model (gemma) — kept separate from the RP-tuned
companion model so summaries stay clean. The summary is stored on the conversation
row (SQLite, source of truth) and injected as "story so far" each turn; the
summarized raw turns then drop out of the live window via the context budgeter.
"""
from __future__ import annotations

from local_llm_writer import ChatConfig, call_model, LocalLLMError

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


def _build_summary_prompt(prior_summary: str, batch: list[dict[str, str]]) -> list[dict[str, str]]:
    transcript = "\n".join(
        f"{('User' if m.get('role') == 'user' else 'Companion')}: {m.get('content','').strip()}"
        for m in batch
        if m.get("content", "").strip()
    )
    system = (
        "You compress an ongoing intimate companion roleplay into a running memory note. "
        "Write a tight third-person summary capturing: who the characters are, the "
        "relationship dynamic, key facts and events, stated preferences and boundaries, "
        "and the current emotional state. Be concise and factual. Do not roleplay, do not "
        "add new content, do not include explicit detail beyond what's needed for continuity. "
        "Keep it under 200 words. Merge the new events into the existing note, do not repeat."
    )
    user = (
        (f"Existing memory note:\n{prior_summary.strip()}\n\n" if prior_summary.strip() else "")
        + f"New conversation since then:\n{transcript}\n\n"
        "Updated memory note:"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def maybe_summarize(
    store,
    conversation_id: str,
    *,
    companion_id: str = "",
    summary_model: str = "gemma:latest",
    base_url: str = "http://localhost:11434",
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
        prompt = _build_summary_prompt(prior, batch)
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
        # Phase 5: record this summary pass as a dated episodic memory ("what
        # happened when") for later time/topic recall. Best-effort.
        if companion_id and hasattr(store, "add_episode"):
            try:
                store.add_episode(companion_id, new_summary, conversation_id)
            except Exception:  # noqa: BLE001
                pass
        return True
    except (LocalLLMError, Exception):  # noqa: BLE001 - never break the chat turn
        return False
