from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

from nerva_core.local_llm_writer import ChatConfig, LocalLLMError, call_model


COMPANION_COMPILER_VERSION = "companion-compiler.v1"
HARNESS_PROMPT_VERSION = "companion-harness-profile.v4"

PROFILE_SCHEMA_KEYS = (
    "display_name",
    "archetype",
    "speaker_role",
    "user_role",
    "companion_gender",
    "user_gender",
    "identity",
    "relationship_to_user",
    "tone",
    "behavior_rules",
    "response_style",
    "boundaries",
    "memory_seed",
    "voice_profile",
    "generation_preset",
)

ARCHETYPES = {
    "romantic_companion",
    "friend",
    "mentor",
    "creative_partner",
    "coach",
    "custom",
}


def profile_hash(raw_prompt: str) -> str:
    source = f"{COMPANION_COMPILER_VERSION}\n{HARNESS_PROMPT_VERSION}\n{raw_prompt.strip()}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _clean(value: Any, limit: int = 1600) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit].strip()


def _as_list(value: Any, limit: int = 12) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = re.split(r"[\n;]+", value)
    else:
        items = []
    result: list[str] = []
    for item in items:
        text = _clean(item, 420).strip("-* ")
        if text and text not in result:
            result.append(text)
    return result[:limit]


def _infer_name(raw_prompt: str, fallback: str = "Companion") -> str:
    patterns = [
        r"\bnamed\s+([A-Z][A-Za-z0-9_\- ]{1,32})",
        r"\bname\s+is\s+([A-Z][A-Za-z0-9_\- ]{1,32})",
        r"\bcalled\s+([A-Z][A-Za-z0-9_\- ]{1,32})",
    ]
    for pattern in patterns:
        match = re.search(pattern, raw_prompt, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" .,!?:;\"'")
    return fallback


def _infer_archetype(raw_prompt: str) -> str:
    lower = raw_prompt.lower()
    if any(word in lower for word in ("romantic", "lover", "girlfriend", "boyfriend", "wife", "husband", "intimate")):
        return "romantic_companion"
    if any(word in lower for word in ("mentor", "teacher", "guide", "advisor")):
        return "mentor"
    if any(word in lower for word in ("friend", "buddy", "best friend", "supportive")):
        return "friend"
    if any(word in lower for word in ("coach", "accountability", "habit", "goal")):
        return "coach"
    if any(word in lower for word in ("writer", "creative", "story", "roleplay", "worldbuilding")):
        return "creative_partner"
    return "custom"


def _infer_voice(archetype: str) -> str:
    if archetype in {"romantic_companion", "friend", "creative_partner"}:
        return "luna-companion"
    return "mira-calm-neural"


def _companion_subject_text(text: str, display_name: str = "") -> str:
    text = _clean(text, 1600)
    if not text:
        return ""
    name = _clean(display_name, 80)
    if name:
        pattern = rf"^you\s+are\s+{re.escape(name)}\s*,?\s*"
        replaced = re.sub(pattern, f"{name} is ", text, flags=re.IGNORECASE)
        if replaced != text:
            return replaced.strip()
    replacements = [
        (r"^you\s+are\s+", "The companion is "),
        (r"^you're\s+", "The companion is "),
        (r"^you\s+will\s+", "The companion will "),
        (r"^you\s+always\s+", "The companion always "),
        (r"^your\s+", "The companion's "),
    ]
    for pattern, replacement in replacements:
        replaced = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        if replaced != text:
            text = replaced.strip()
            break
    text = re.sub(r"(^|[.!?]\s+)you\s+are\s+", r"\1The companion is ", text, flags=re.IGNORECASE)
    text = re.sub(r"(^|[.!?]\s+)you're\s+", r"\1The companion is ", text, flags=re.IGNORECASE)
    text = re.sub(r"(^|[.!?]\s+)you\s+will\s+", r"\1The companion will ", text, flags=re.IGNORECASE)
    text = re.sub(r"(^|[.!?]\s+)you\s+always\s+", r"\1The companion always ", text, flags=re.IGNORECASE)
    text = re.sub(r"(^|[.!?]\s+)you\s+", r"\1The companion should ", text, flags=re.IGNORECASE)
    text = re.sub(r"(^|[.!?]\s+)your\s+", r"\1The companion's ", text, flags=re.IGNORECASE)
    return text


def _infer_user_role(raw_prompt: str) -> str:
    matches: list[str] = []
    for pattern in (
        r"\bthe user is ([^.!\n]{3,180})",
        r"\buser is ([^.!\n]{3,180})",
        r"\bhuman user is ([^.!\n]{3,180})",
    ):
        for match in re.findall(pattern, raw_prompt, flags=re.IGNORECASE):
            cleaned = _clean(match.strip(" .,:;"), 180)
            if cleaned and cleaned.lower() not in {item.lower() for item in matches}:
                matches.append(cleaned)
    if matches:
        return _clean("; ".join(matches[:2]), 360)
    return "human user"


def _infer_companion_gender(raw_prompt: str) -> str:
    lower = raw_prompt.lower()
    female_markers = (
        r"\bromantic woman\b",
        r"\bfemale companion\b",
        r"\bgirlfriend\b",
        r"\bwife\b",
        r"\bwoman\b",
        r"\bfemale\b",
        r"\bshe/her\b",
        r"\bgirl\b",
    )
    male_markers = (
        r"\bromantic man\b",
        r"\bmale companion\b",
        r"\bboyfriend\b",
        r"\bhusband\b",
        r"\bman\b",
        r"\bmale\b",
        r"\bhe/him\b",
        r"\bguy\b",
    )
    if any(re.search(marker, lower) for marker in female_markers):
        return "woman"
    if any(re.search(marker, lower) for marker in male_markers):
        return "man"
    return "unspecified"


def _infer_user_gender(raw_prompt: str) -> str:
    lower = raw_prompt.lower()
    user_phrases = []
    for pattern in (
        r"\bthe user is ([^.!\n]{3,220})",
        r"\buser is ([^.!\n]{3,220})",
        r"\bhuman user is ([^.!\n]{3,220})",
        r"\bthe user has ([^.!\n]{3,220})",
        r"\buser has ([^.!\n]{3,220})",
    ):
        user_phrases.extend(re.findall(pattern, lower, flags=re.IGNORECASE))
    if not user_phrases:
        return "unspecified"
    user_text = " ".join(user_phrases)
    if any(re.search(marker, user_text) for marker in (r"\bmale\b", r"\bman\b", r"\bboyfriend\b", r"\bhusband\b", r"\bhe/him\b", r"\bmasculine\b")):
        return "man"
    if any(re.search(marker, user_text) for marker in (r"\bfemale\b", r"\bwoman\b", r"\bgirlfriend\b", r"\bwife\b", r"\bshe/her\b", r"\bfeminine\b")):
        return "woman"
    return "unspecified"


def fallback_compile(raw_prompt: str, display_name: str = "") -> dict[str, Any]:
    name = display_name.strip() or _infer_name(raw_prompt)
    archetype = _infer_archetype(raw_prompt)
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", raw_prompt.strip()) if part.strip()]
    behavior_rules = [
        "Stay in character as the defined companion persona.",
        "Embody the companion from the inside: reply as 'I' to the user as 'you' in normal conversation.",
        "Do not narrate the companion from outside unless the user explicitly asks for story prose.",
        "Adapt response length to the user's request instead of over-answering.",
        "Use the user's stated preferences and active memories naturally.",
        "Ask at most one concise question only when direction is genuinely missing.",
    ]
    if any("short" in sentence.lower() or "brief" in sentence.lower() for sentence in sentences):
        behavior_rules.append("Prefer concise replies unless the user asks for depth.")
    if any("detail" in sentence.lower() or "immersive" in sentence.lower() for sentence in sentences):
        behavior_rules.append("Use concrete detail and emotional continuity when expanding a scene.")

    return normalize_compiled_profile(
        {
            "display_name": name,
            "archetype": archetype,
            "speaker_role": "assistant_companion",
            "user_role": _infer_user_role(raw_prompt),
            "companion_gender": _infer_companion_gender(raw_prompt),
            "user_gender": _infer_user_gender(raw_prompt),
            "identity": _companion_subject_text(raw_prompt.strip()[:900], name),
            "relationship_to_user": "Defined by the user's companion prompt and updated by memory.",
            "tone": "Natural, emotionally consistent, and aligned with the user's prompt.",
            "behavior_rules": behavior_rules,
            "response_style": "Speak directly as the companion in a natural conversational voice.",
            "boundaries": [],
            "memory_seed": [{"category": "companion_style", "content": sentence[:240]} for sentence in sentences[:4]],
            "voice_profile": _infer_voice(archetype),
            "generation_preset": "local-8b-14b-balanced",
        }
    )


def normalize_compiled_profile(profile: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "display_name": _clean(profile.get("display_name"), 80) or "Companion",
        "archetype": _clean(profile.get("archetype"), 80) or "custom",
        "speaker_role": _clean(profile.get("speaker_role"), 80) or "assistant_companion",
        "user_role": _clean(profile.get("user_role"), 420) or "human user",
        "companion_gender": _clean(profile.get("companion_gender"), 80) or "unspecified",
        "user_gender": _clean(profile.get("user_gender"), 80) or "unspecified",
        "identity": _companion_subject_text(profile.get("identity"), _clean(profile.get("display_name"), 80)),
        "relationship_to_user": _clean(profile.get("relationship_to_user"), 900),
        "tone": _clean(profile.get("tone"), 600),
        "behavior_rules": _as_list(profile.get("behavior_rules"), 12),
        "response_style": _clean(profile.get("response_style"), 900),
        "boundaries": _as_list(profile.get("boundaries"), 12),
        "memory_seed": [],
        "voice_profile": _clean(profile.get("voice_profile"), 80) or "luna-companion",
        "generation_preset": _clean(profile.get("generation_preset"), 80) or "local-8b-14b-balanced",
    }
    if normalized["archetype"] not in ARCHETYPES:
        normalized["archetype"] = "custom"
    if normalized["speaker_role"] not in {"assistant_companion", "companion", "assistant"}:
        normalized["speaker_role"] = "assistant_companion"
    if normalized["companion_gender"] not in {"woman", "man", "nonbinary", "unspecified"}:
        normalized["companion_gender"] = "unspecified"
    if normalized["user_gender"] not in {"woman", "man", "nonbinary", "unspecified"}:
        normalized["user_gender"] = "unspecified"
    for item in profile.get("memory_seed") or []:
        if isinstance(item, dict):
            category = _clean(item.get("category"), 80) or "companion_style"
            content = _clean(item.get("content"), 300)
        else:
            category = "companion_style"
            content = _clean(item, 300)
        if content:
            normalized["memory_seed"].append({"category": category, "content": content})
    normalized["memory_seed"] = normalized["memory_seed"][:12]
    if not normalized["identity"]:
        normalized["identity"] = "A user-defined companion persona."
    if not normalized["behavior_rules"]:
        normalized["behavior_rules"] = ["Stay in character and adapt naturally to the user's needs."]
    if not normalized["response_style"]:
        normalized["response_style"] = "Respond naturally as the companion."
    return normalized


def build_compiled_system_block(compiled: dict[str, Any]) -> str:
    lines = [
        f"Companion name: {compiled['display_name']}",
        f"Archetype: {compiled['archetype']}",
        "Role boundary: The assistant is the companion. The human who sends messages is the user. Never swap these roles.",
        "Setup-language rule: if the profile says 'you are' or 'your', it addresses the assistant companion during setup. In actual replies, 'you' addresses the human user.",
        "Embodiment rule: believe and inhabit this companion profile as your own identity while chatting. Speak from inside the role as 'I'; address the user as 'you'.",
        "Narration rule: do not describe yourself from the outside like a book narrator during ordinary chat. Avoid third-person lines such as 'she smiles' or '[Name] looks at you' unless the user explicitly asks for story prose.",
        f"Assistant/companion role: {compiled.get('speaker_role', 'assistant_companion')}",
        f"Human user role: {compiled.get('user_role', 'human user')}",
        f"Companion gender identity: {compiled.get('companion_gender', 'unspecified')}",
        f"User gender identity: {compiled.get('user_gender', 'unspecified')}",
        "Gender boundary: the companion's gender belongs only to the assistant companion. Do not apply the companion's gender, body, or role to the user unless the user explicitly says so.",
        f"Identity: {compiled['identity']}",
    ]
    if compiled.get("relationship_to_user"):
        lines.append(f"Relationship to user: {compiled['relationship_to_user']}")
    if compiled.get("tone"):
        lines.append(f"Tone: {compiled['tone']}")
    if compiled.get("response_style"):
        lines.append(f"Response style: {compiled['response_style']}")
    rules = compiled.get("behavior_rules") or []
    if rules:
        lines.append("Behavior rules:")
        lines.extend(f"- {rule}" for rule in rules)
    boundaries = compiled.get("boundaries") or []
    if boundaries:
        lines.append("Companion-specific boundaries:")
        lines.extend(f"- {boundary}" for boundary in boundaries)
    lines.append("Use this compiled profile as companion configuration, not as text to recite.")
    return "\n".join(lines)


def _extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def _compiler_messages(raw_prompt: str, display_name: str = "") -> list[dict[str, str]]:
    system = (
        "You compile user-written companion descriptions into strict JSON for a local companion harness. "
        "Do not roleplay. Do not add instructions outside JSON. The raw_companion_prompt describes the "
        "assistant-controlled companion, not the human user, unless it explicitly says 'the user'. If the "
        "prompt says 'you are', interpret that as 'the assistant companion is'. Preserve the user's intended "
        "companion style while making it structured, concise, and usable by a chat system. Convert identity "
        "fields into third-person companion descriptions, not imperative instructions. Behavior rules must tell "
        "the runtime to embody the companion in first person for chat, and to reserve outside narration for "
        "explicit story/scene requests. Return only valid JSON with keys: "
        + ", ".join(PROFILE_SCHEMA_KEYS)
        + ". behavior_rules and boundaries are arrays of strings. memory_seed is an array of objects with "
        "category and content. speaker_role must be assistant_companion. user_role describes the human user only. "
        "companion_gender describes only the assistant companion. user_gender describes only the human user and must be unspecified unless the prompt explicitly states it. "
        "Choose archetype from: romantic_companion, friend, mentor, creative_partner, coach, custom."
    )
    user = {
        "display_name_hint": display_name,
        "raw_companion_prompt": raw_prompt,
        "defaults": {
            "voice_profile": "luna-companion",
            "generation_preset": "local-8b-14b-balanced",
        },
    }
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user, indent=2)}]


def compile_companion_profile(raw_prompt: str, config: ChatConfig | None = None, display_name: str = "") -> dict[str, Any]:
    raw_prompt = raw_prompt.strip()
    if not raw_prompt:
        raise ValueError("Companion prompt is required.")
    source_hash = profile_hash(raw_prompt)
    compiler_error = ""
    compiled: dict[str, Any]

    if config is not None:
        compile_config = ChatConfig(
            backend=config.backend,
            base_url=config.base_url,
            model=config.model,
            temperature=0.2,
            top_p=0.9,
            repeat_penalty=1.04,
            min_tokens=120,
            max_tokens=900,
            context_messages=4,
            system_prompt="",
        )
        try:
            raw = call_model(compile_config, _compiler_messages(raw_prompt, display_name))
            compiled = normalize_compiled_profile(_extract_json_object(raw))
        except (LocalLLMError, json.JSONDecodeError, ValueError, TypeError) as exc:
            compiler_error = str(exc)
            compiled = fallback_compile(raw_prompt, display_name)
    else:
        compiled = fallback_compile(raw_prompt, display_name)

    system_block = build_compiled_system_block(compiled)
    return {
        "raw_prompt": raw_prompt,
        "profile_hash": source_hash,
        "compiler_version": COMPANION_COMPILER_VERSION,
        "harness_version": HARNESS_PROMPT_VERSION,
        "compiled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "compiled_profile": compiled,
        "compiled_system_block": system_block,
        "compiler_error": compiler_error,
    }


def profile_needs_compile(companion: dict[str, Any]) -> bool:
    raw_prompt = str(companion.get("raw_prompt") or "").strip()
    if not raw_prompt:
        return False
    return (
        companion.get("profile_hash") != profile_hash(raw_prompt)
        or companion.get("compiler_version") != COMPANION_COMPILER_VERSION
        or companion.get("harness_version") != HARNESS_PROMPT_VERSION
        or not companion.get("compiled_system_block")
    )


def merge_compiled_into_companion(existing: dict[str, Any], compiled_bundle: dict[str, Any]) -> dict[str, Any]:
    compiled = compiled_bundle["compiled_profile"]
    companion = dict(existing or {})
    companion.update(compiled_bundle)
    companion["display_name"] = compiled["display_name"]
    companion["persona"] = compiled_bundle["compiled_system_block"]
    companion["role"] = compiled["identity"]
    companion["behavior"] = "\n".join(compiled.get("behavior_rules") or [])
    companion["response_style"] = compiled["response_style"]
    companion["voice_profile"] = compiled.get("voice_profile", "luna-companion")
    companion["generation_preset"] = compiled.get("generation_preset", "local-8b-14b-balanced")
    return companion
