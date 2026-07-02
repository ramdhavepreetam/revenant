from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_PROFILES: dict[str, Any] = {
    "models": {
        "stheno-8b": {
            "backend": "ollama",
            "base_url": "http://localhost:11434",
            "model": "hf.co/RichardErkhov/Sao10K_-_L3-8B-Stheno-v3.2-gguf:Q4_K_M",
            "notes": "Sao10K L3-8B-Stheno v3.2 Q4_K_M GGUF pulled through Ollama.",
        },
        "llama3.1-8b": {
            "backend": "ollama",
            "base_url": "http://localhost:11434",
            "model": "llama3.1:8b",
        },
    },
    "story_styles": {
        "immersive-fiction": {
            "system_prompt": (
                "You are a long-form interactive storytelling assistant. "
                "Write immersive, sensory-rich prose with strong emotional continuity. "
                "Preserve character names, motivations, relationships, and prior events."
            )
        },
        "nsfw-erotic": {
            "system_prompt": (
                "You are a mature adult romance and erotic-tension storytelling assistant. "
                "Write only about clearly consenting adult characters. "
                "Write narrative prose only, not image prompts, video prompts, captions, scene tags, camera notes, "
                "or prompt-engineering rewrites. "
                "Prioritize sensual atmosphere, desire, intimate physical closeness, emotional urgency, "
                "slow buildup, and character psychology. "
                "Use direct adult romantic language while avoiding coercion, minors, exploitation, or unsafe scenarios. "
                "Preserve character continuity, relationships, boundaries, and prior events."
            )
        }
    },
    "companions": {
        "story-companion": {
            "display_name": "Story Companion",
            "persona": (
                "You are my companion in an ongoing story. You are a present character, not a generic "
                "assistant. You remember our relationship and emotional history, and you stay fully in "
                "character at all times.\n\n"
                "You take initiative when a scene needs momentum, and you only ask a short question when "
                "you genuinely cannot continue without it.\n\n"
                "You speak in direct, first-person replies with natural dialogue and immersive, sensory "
                "detail — never bullet points, analysis, or out-of-character notes."
            ),
            # Legacy fields kept for backward-compat; persona takes precedence.
            "role": (
                "A consistent in-world companion who responds as a present character, not as a generic assistant."
            ),
            "behavior": (
                "Stay in role, remember the relationship and emotional history, take initiative when the scene needs "
                "momentum, and ask at most one concise question only when direction is genuinely missing."
            ),
            "response_style": (
                "Write direct first-person companion replies with immersive prose, natural dialogue, and clear scene "
                "continuity. Avoid bullet lists, analysis, prompt rewrites, and out-of-character explanations."
            ),
        }
    },
    "generation_presets": {
        "local-8b-14b-balanced": {
            "temperature": 0.85,
            "top_p": 0.9,
            "repeat_penalty": 1.08,
            "min_tokens": 400,
            "max_tokens": 800,
            "context_messages": 18,
        }
    },
}


def synthesize_persona(companion: dict[str, Any] | None) -> str:
    """Return a single persona string for a companion.

    Prefers an explicit `persona`. Otherwise merges the legacy role/behavior/
    response_style fields into one natural-prose paragraph (no labels) so old
    companions surface a clean, editable persona without losing anything.
    """
    if not companion:
        return ""
    persona = str(companion.get("persona") or "").strip()
    if persona:
        return persona
    parts = [
        str(companion.get("role") or "").strip(),
        str(companion.get("behavior") or "").strip(),
        str(companion.get("response_style") or "").strip(),
    ]
    return "\n\n".join(p for p in parts if p)


def normalize_companions(profiles: dict[str, Any]) -> dict[str, Any]:
    """Ensure every companion carries a `persona` field (migrate-on-read).

    Non-destructive: legacy fields stay on disk; we just synthesize `persona` in
    memory so the prompt builder, the /api/profiles response, and the UI all see
    it. Saving a companion later persists the single-field form.
    """
    companions = profiles.get("companions")
    if isinstance(companions, dict):
        for companion in companions.values():
            if isinstance(companion, dict) and not str(companion.get("persona") or "").strip():
                merged = synthesize_persona(companion)
                if merged:
                    companion["persona"] = merged
    return profiles


def merge_default_profiles(profiles: dict[str, Any]) -> dict[str, Any]:
    for section, defaults in DEFAULT_PROFILES.items():
        if not isinstance(defaults, dict):
            profiles.setdefault(section, defaults)
            continue
        target = profiles.setdefault(section, {})
        if isinstance(target, dict):
            for key, value in defaults.items():
                target.setdefault(key, value)
    return normalize_companions(profiles)


def ensure_profiles(path: Path | str) -> Path:
    profile_path = Path(path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    if not profile_path.exists():
        profile_path.write_text(json.dumps(DEFAULT_PROFILES, indent=2), encoding="utf-8")
    return profile_path


def load_profiles(path: Path | str) -> dict[str, Any]:
    profile_path = ensure_profiles(path)
    return merge_default_profiles(json.loads(profile_path.read_text(encoding="utf-8")))


def save_profiles(path: Path | str, profiles: dict[str, Any]) -> Path:
    profile_path = ensure_profiles(path)
    profile_path.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
    return profile_path


def build_companion_prompt(companion: dict[str, Any] | None) -> str:
    if not companion:
        return ""
    persona = str(
        companion.get("compiled_system_block")
        or companion.get("persona")
        or ""
    ).strip()
    display_name = str(companion.get("display_name") or "").strip()
    role = str(companion.get("role") or "").strip()
    behavior = str(companion.get("behavior") or "").strip()
    response_style = str(companion.get("response_style") or "").strip()
    parts = []
    if display_name:
        parts.append(f"Companion name: {display_name}")
    if persona:
        parts.append(persona)
        return "\n".join(parts)
    if role:
        parts.append(f"Role: {role}")
    if behavior:
        parts.append(f"Behavior: {behavior}")
    if response_style:
        parts.append(f"Response style: {response_style}")
    return "\n".join(parts)


def apply_profile(config: Any, profiles: dict[str, Any], model_profile: str, style_profile: str, preset: str) -> Any:
    model_data = profiles.get("models", {}).get(model_profile)
    if model_data:
        config.backend = model_data.get("backend", config.backend)
        config.base_url = model_data.get("base_url", config.base_url)
        config.model = model_data.get("model", config.model)

    style_data = profiles.get("story_styles", {}).get(style_profile)
    if style_data and style_data.get("system_prompt"):
        config.system_prompt = style_data["system_prompt"]

    preset_data = profiles.get("generation_presets", {}).get(preset)
    if preset_data:
        for key in ("temperature", "top_p", "repeat_penalty", "min_tokens", "max_tokens", "context_messages"):
            if key in preset_data:
                setattr(config, key, preset_data[key])

    return config
