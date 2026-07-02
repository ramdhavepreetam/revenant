from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MEMORY_FIELDS = (
    "user_name",
    "companion_name",
    "relationship",
    "tone_preferences",
    "important_facts",
    "boundaries",
    "current_dynamic",
    "learned_notes",
)


def default_memory(companion_id: str) -> dict[str, Any]:
    return {
        "companion_id": companion_id,
        "user_name": "",
        "companion_name": "",
        "relationship": "",
        "tone_preferences": [],
        "important_facts": [],
        "boundaries": [],
        "current_dynamic": "",
        "learned_notes": [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip("- \t") for line in value.splitlines() if line.strip("- \t")]
    return []


def normalize_memory(companion_id: str, memory: dict[str, Any] | None) -> dict[str, Any]:
    normalized = default_memory(companion_id)
    if not memory:
        return normalized

    for key in MEMORY_FIELDS:
        if key in {"tone_preferences", "important_facts", "boundaries", "learned_notes"}:
            normalized[key] = _as_list(memory.get(key))
        else:
            normalized[key] = str(memory.get(key) or "").strip()
    normalized["companion_id"] = companion_id
    normalized["updated_at"] = str(memory.get("updated_at") or normalized["updated_at"])
    return normalized


def memory_to_prompt(memory: dict[str, Any]) -> str:
    lines: list[str] = []
    if memory.get("user_name"):
        lines.append(f"User name: {memory['user_name']}")
    if memory.get("companion_name"):
        lines.append(f"Companion name in memory: {memory['companion_name']}")
    if memory.get("relationship"):
        lines.append(f"Relationship: {memory['relationship']}")
    if memory.get("current_dynamic"):
        lines.append(f"Current dynamic: {memory['current_dynamic']}")
    for label, key in (
        ("Tone preferences", "tone_preferences"),
        ("Important facts", "important_facts"),
        ("Boundaries", "boundaries"),
        ("Learned notes", "learned_notes"),
    ):
        values = memory.get(key) or []
        if values:
            lines.append(f"{label}:")
            lines.extend(f"- {value}" for value in values[:12])
    return "\n".join(lines)


class CompanionMemoryStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(json.dumps({"companions": {}}, indent=2), encoding="utf-8")

    def _read(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get(self, companion_id: str) -> dict[str, Any]:
        data = self._read()
        memory = data.setdefault("companions", {}).get(companion_id)
        return normalize_memory(companion_id, memory)

    def save(self, companion_id: str, memory: dict[str, Any]) -> dict[str, Any]:
        data = self._read()
        normalized = normalize_memory(companion_id, memory)
        normalized["updated_at"] = datetime.now(timezone.utc).isoformat()
        data.setdefault("companions", {})[companion_id] = normalized
        self._write(data)
        return normalized

    def append_notes(self, companion_id: str, notes: list[dict[str, str]]) -> dict[str, Any]:
        memory = self.get(companion_id)
        changed = False
        for note in notes:
            bucket = note.get("bucket", "learned_notes")
            text = str(note.get("text") or "").strip()
            if bucket not in {"tone_preferences", "important_facts", "boundaries", "learned_notes"}:
                bucket = "learned_notes"
            if text and text not in memory[bucket]:
                memory[bucket].append(text)
                changed = True
        if changed:
            return self.save(companion_id, memory)
        return memory


def _first_match(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" .,!?:;\"'")
    return ""


def _clean_sentence_fragment(value: str) -> str:
    return re.split(r"[.!?]", value, maxsplit=1)[0].strip(" .,!?:;\"'")


def extract_memory_notes(user_text: str) -> list[dict[str, str]]:
    text = " ".join(user_text.split())
    notes: list[dict[str, str]] = []

    name = _first_match(
        [
            r"\bmy name is ([A-Z][A-Za-z0-9_\- ]{1,40})",
            r"\bcall me ([A-Z][A-Za-z0-9_\- ]{1,40})",
        ],
        text,
    )
    if name:
        notes.append({"bucket": "important_facts", "text": f"User name or preferred address: {name}."})

    preference = _first_match(
        [
            r"\bi prefer ([^.!?]{3,120})",
            r"\bi like ([^.!?]{3,120})",
            r"\bi love ([^.!?]{3,120})",
        ],
        text,
    )
    if preference:
        preference = _clean_sentence_fragment(preference)
        notes.append({"bucket": "tone_preferences", "text": f"User prefers {preference}."})

    dislike = _first_match(
        [
            r"\bi don't like ([^.!?]{3,120})",
            r"\bi do not like ([^.!?]{3,120})",
            r"\bi hate ([^.!?]{3,120})",
        ],
        text,
    )
    if dislike:
        dislike = _clean_sentence_fragment(dislike)
        notes.append({"bucket": "boundaries", "text": f"User dislikes or wants to avoid {dislike}."})

    remember = _clean_sentence_fragment(_first_match([r"\bremember that ([^.!?]{3,160})"], text))
    if remember and not re.match(r"i (prefer|like|love|don't like|do not like|hate)\b", remember, flags=re.IGNORECASE):
        notes.append({"bucket": "important_facts", "text": remember[0].upper() + remember[1:] + "."})

    avoid = _first_match(
        [
            r"\bnever ([^.!?]{3,120})",
            r"\bdon't (?!like\b)([^.!?]{3,120})",
            r"\bdo not (?!like\b)([^.!?]{3,120})",
        ],
        text,
    )
    if avoid:
        avoid = _clean_sentence_fragment(avoid)
        notes.append({"bucket": "boundaries", "text": f"Avoid: {avoid}."})

    return notes[:5]
