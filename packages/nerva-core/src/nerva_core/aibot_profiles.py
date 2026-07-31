from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_PROFILES: dict[str, Any] = {
    "models": {
        "qwen2.5-7b": {
            "backend": "ollama",
            "base_url": "http://localhost:11434",
            "model": "qwen2.5:7b",
            "notes": "Native tool-calling; router role + fast/light coding.",
        },
        "qwen2.5-coder-14b": {
            "backend": "ollama",
            "base_url": "http://localhost:11434",
            "model": "huihui_ai/qwen2.5-coder-abliterate:14b",
            "notes": "Code-specialized, abliterated (uncensored) Qwen2.5-Coder. "
                     "Native tool-calling. Primary coding model.",
        },
        "qwen2.5-14b": {
            "backend": "ollama",
            "base_url": "http://localhost:11434",
            "model": "qwen2.5:14b",
            "notes": "Language / reasoning role. Pull before use.",
        },
    },
    # Task -> model-profile-name. The router (agent_router.py) resolves a role
    # to a model via this map + the "models" section above, so endpoint data is
    # never duplicated. "fallback" names the role used when classification fails.
    "model_roles": {
        "code": "qwen2.5-coder-14b",
        "language": "qwen2.5-14b",
        "router": "qwen2.5-7b",
        # Small/fast model for context compaction (F5); reuses the 7b already
        # pulled for routing so no extra download is required.
        "summary": "qwen2.5-7b",
        "fallback": "language",
    },
    "generation_presets": {
        "local-14b-balanced": {
            "temperature": 0.2,
            "top_p": 0.9,
            "repeat_penalty": 1.05,
            "min_tokens": 64,
            "max_tokens": 1024,
            "context_messages": 24,
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
