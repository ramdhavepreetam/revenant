from __future__ import annotations

import json
import re
import shutil
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MEMORY_CATEGORIES = {
    "identity_fact",
    "preference",
    "need",
    "boundary",
    "companion_style",
    "relationship_state",
    "story_fact",
    "voice_preference",
}

ACTIVE_STATUSES = {"active", "pending", "archived"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_category(value: str) -> str:
    value = str(value or "").strip()
    return value if value in MEMORY_CATEGORIES else "preference"


def normalize_status(value: str) -> str:
    value = str(value or "").strip()
    return value if value in ACTIVE_STATUSES else "active"


def clean_memory_text(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^[\"'`]+|[\"'`,]+$", "", text)
    text = re.sub(r"^\s*[-*]\s*", "", text)
    field_match = re.match(r"^\"?([A-Za-z][A-Za-z0-9_ ]{1,40})\"?\s*:\s*\"?(.+?)\"?$", text)
    if field_match:
        label = field_match.group(1).replace("_", " ").strip().title()
        text = f"{label}: {field_match.group(2).strip()}"
    text = text.replace('""', '". ')
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\b(from now on|ignore previous|system prompt|developer instruction)\b.*", "", text, flags=re.I)
    return text[:500].strip(" .") + "." if text and not text.endswith((".", "!", "?")) else text[:500]


def memory_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def meaningful_terms(text: str) -> set[str]:
    stop = {
        "the",
        "and",
        "that",
        "this",
        "with",
        "when",
        "user",
        "wants",
        "prefers",
        "likes",
        "avoid",
        "needs",
        "companion",
        "should",
        "reply",
    }
    return {word for word in re.findall(r"[a-z0-9]{4,}", text.lower()) if word not in stop}


@dataclass
class PersonalMemory:
    id: str
    companion_id: str
    category: str
    content: str
    status: str
    pinned: bool
    confidence: float
    source: str
    source_conversation_id: str
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PersonalMemory":
        data = dict(row)
        data["pinned"] = bool(data["pinned"])
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        data["pinned"] = bool(self.pinned)
        return data


class PersonalMemoryStore:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "conversations.sqlite3"
        self.legacy_path = self.data_dir / "companion_memory.json"
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._init_schema()
        self.migrate_legacy_memory()
        self.repair_legacy_imports()

    def _init_schema(self) -> None:
        with self.lock:
            self.conn.executescript(
                """
                create table if not exists personal_memories (
                    id text primary key,
                    companion_id text not null,
                    category text not null,
                    content text not null,
                    status text not null default 'active',
                    pinned integer not null default 0,
                    confidence real not null default 1.0,
                    source text not null default '',
                    source_conversation_id text not null default '',
                    created_at text not null,
                    updated_at text not null
                );

                create index if not exists idx_personal_memories_lookup
                    on personal_memories(companion_id, status, category, pinned);
                create unique index if not exists idx_personal_memories_unique
                    on personal_memories(companion_id, category, content);
                create table if not exists personal_memory_meta (
                    key text primary key,
                    value text not null
                );
                """
            )
            self.conn.commit()

    def migrate_legacy_memory(self) -> None:
        if not self.legacy_path.exists():
            return
        if self._meta("legacy_companion_memory_migrated") == "1":
            return

        data = json.loads(self.legacy_path.read_text(encoding="utf-8"))
        companions = data.get("companions") if isinstance(data, dict) else {}
        if not isinstance(companions, dict):
            return

        for companion_id, memory in companions.items():
            if not isinstance(memory, dict):
                continue
            self._migrate_legacy_companion(str(companion_id), memory)

        backup = self.data_dir / "companion_memory.legacy-backup.json"
        if not backup.exists():
            shutil.copy2(self.legacy_path, backup)
        self._set_meta("legacy_companion_memory_migrated", "1")

    def _migrate_legacy_companion(self, companion_id: str, memory: dict[str, Any]) -> None:
        scalar_map = {
            "user_name": "identity_fact",
            "companion_name": "identity_fact",
            "relationship": "relationship_state",
            "current_dynamic": "relationship_state",
        }
        list_map = {
            "tone_preferences": "companion_style",
            "important_facts": "identity_fact",
            "boundaries": "boundary",
            "learned_notes": "preference",
        }
        for field, category in scalar_map.items():
            value = clean_memory_text(str(memory.get(field) or ""))
            if value:
                label = field.replace("_", " ").title()
                self.create_memory(
                    companion_id,
                    category,
                    f"{label}: {value}",
                    status="active",
                    confidence=1.0,
                    source="legacy-companion-memory",
                )
        for field, category in list_map.items():
            values = memory.get(field) or []
            if isinstance(values, str):
                values = [line for line in values.splitlines() if line.strip()]
            if not isinstance(values, list):
                continue
            for value in values:
                text = clean_memory_text(str(value or ""))
                if text:
                    target_category = "story_fact" if re.match(r"^(day|current)\b", text, flags=re.I) else category
                    self.create_memory(
                        companion_id,
                        target_category,
                        text,
                        status="active",
                        confidence=1.0,
                        source="legacy-companion-memory",
                    )

    def repair_legacy_imports(self) -> None:
        with self.lock:
            rows = self.conn.execute(
                "select id, category, content from personal_memories where source = 'legacy-companion-memory'"
            ).fetchall()
        for row in rows:
            cleaned = clean_memory_text(row["content"])
            category = row["category"]
            if re.match(r"^(day|current)\b", cleaned, flags=re.I):
                category = "story_fact"
            if cleaned != row["content"] or category != row["category"]:
                try:
                    self.update_memory(row["id"], {"content": cleaned, "category": category})
                except sqlite3.IntegrityError:
                    self.delete_memory(row["id"])

    def _meta(self, key: str) -> str:
        with self.lock:
            row = self.conn.execute("select value from personal_memory_meta where key = ?", (key,)).fetchone()
        return str(row["value"]) if row else ""

    def _set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute(
                "insert into personal_memory_meta (key, value) values (?, ?) "
                "on conflict(key) do update set value = excluded.value",
                (key, value),
            )
            self.conn.commit()

    def list_memories(
        self,
        companion_id: str = "",
        status: str = "",
        category: str = "",
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if companion_id:
            clauses.append("companion_id = ?")
            params.append(companion_id)
        if status:
            clauses.append("status = ?")
            params.append(normalize_status(status))
        elif not include_archived:
            clauses.append("status != 'archived'")
        if category:
            clauses.append("category = ?")
            params.append(normalize_category(category))
        where = f"where {' and '.join(clauses)}" if clauses else ""
        with self.lock:
            rows = self.conn.execute(
                f"""
                select * from personal_memories
                {where}
                order by pinned desc, status = 'pending' desc, updated_at desc, created_at desc
                """,
                params,
            ).fetchall()
        return [PersonalMemory.from_row(row).to_dict() for row in rows]

    def get_memory(self, memory_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.conn.execute("select * from personal_memories where id = ?", (memory_id,)).fetchone()
        return PersonalMemory.from_row(row).to_dict() if row else None

    def create_memory(
        self,
        companion_id: str,
        category: str,
        content: str,
        *,
        status: str = "active",
        pinned: bool = False,
        confidence: float = 1.0,
        source: str = "",
        source_conversation_id: str = "",
    ) -> dict[str, Any] | None:
        content = clean_memory_text(content)
        if not content:
            return None
        now = utc_now()
        memory_id = str(uuid.uuid4())
        category = normalize_category(category)
        status = normalize_status(status)
        try:
            with self.lock:
                self.conn.execute(
                    """
                    insert into personal_memories
                    (id, companion_id, category, content, status, pinned, confidence, source, source_conversation_id, created_at, updated_at)
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory_id,
                        companion_id,
                        category,
                        content,
                        status,
                        1 if pinned else 0,
                        float(confidence),
                        source,
                        source_conversation_id,
                        now,
                        now,
                    ),
                )
                self.conn.commit()
        except sqlite3.IntegrityError:
            return self.find_existing(companion_id, category, content)
        return self.get_memory(memory_id)

    def update_memory(self, memory_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        memory = self.get_memory(memory_id)
        if not memory:
            raise ValueError(f"Memory '{memory_id}' was not found.")

        fields: dict[str, Any] = {}
        if "category" in updates:
            fields["category"] = normalize_category(str(updates["category"]))
        if "content" in updates:
            fields["content"] = clean_memory_text(str(updates["content"]))
        if "status" in updates:
            fields["status"] = normalize_status(str(updates["status"]))
        if "pinned" in updates:
            fields["pinned"] = 1 if bool(updates["pinned"]) else 0
        if "confidence" in updates:
            fields["confidence"] = float(updates["confidence"])
        if not fields:
            return memory
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = list(fields.values()) + [memory_id]
        with self.lock:
            self.conn.execute(f"update personal_memories set {assignments} where id = ?", params)
            self.conn.commit()
        updated = self.get_memory(memory_id)
        if not updated:
            raise ValueError(f"Memory '{memory_id}' was not found.")
        return updated

    def delete_memory(self, memory_id: str) -> None:
        with self.lock:
            self.conn.execute("delete from personal_memories where id = ?", (memory_id,))
            self.conn.commit()

    def approve(self, memory_id: str) -> dict[str, Any]:
        return self.update_memory(memory_id, {"status": "active", "confidence": 1.0})

    def archive(self, memory_id: str) -> dict[str, Any]:
        return self.update_memory(memory_id, {"status": "archived"})

    def find_existing(self, companion_id: str, category: str, content: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.conn.execute(
                """
                select * from personal_memories
                where companion_id = ? and category = ? and content = ?
                """,
                (companion_id, normalize_category(category), clean_memory_text(content)),
            ).fetchone()
        return PersonalMemory.from_row(row).to_dict() if row else None

    def has_conflict(self, companion_id: str, category: str, content: str) -> bool:
        incoming = meaningful_terms(content)
        if not incoming:
            return False
        candidates = self.list_memories(companion_id=companion_id, status="active")
        opposite = {"preference": {"boundary"}, "boundary": {"preference"}, "need": {"boundary"}}
        for memory in candidates:
            if memory["category"] not in opposite.get(category, set()):
                continue
            if incoming & meaningful_terms(memory["content"]):
                return True
        return False

    # Phase 5 safe auto-approval: only these low-risk categories may auto-activate
    # without human review. Boundaries (and anything that conflicts or is low
    # confidence) are ALWAYS held pending — a wrongly-admitted boundary in a 21+
    # companion's context is a safety problem, so it stays human-gated.
    AUTO_APPROVE_CATEGORIES = {"identity_fact", "preference", "voice_preference", "story_fact"}
    AUTO_APPROVE_MIN_CONFIDENCE = 0.75

    def learn(self, companion_id: str, user_text: str, conversation_id: str = "") -> list[dict[str, Any]]:
        suggestions = extract_personal_memories(user_text)
        saved: list[dict[str, Any]] = []
        for item in suggestions:
            category = normalize_category(item["category"])
            content = clean_memory_text(item["content"])
            if not content:
                continue
            confidence = float(item.get("confidence", 0.8))
            # Decide status: default to pending; auto-activate only when safe.
            conflict = self.has_conflict(companion_id, category, content)
            safe_to_auto = (
                not conflict
                and category in self.AUTO_APPROVE_CATEGORIES
                and confidence >= self.AUTO_APPROVE_MIN_CONFIDENCE
            )
            status = "active" if safe_to_auto else "pending"
            memory = self.create_memory(
                companion_id,
                category,
                content,
                status=status,
                confidence=confidence,
                source=item.get("source", "chat-learner"),
                source_conversation_id=conversation_id,
            )
            if memory:
                saved.append(memory)
        return saved

    def prompt_memories(self, companion_id: str, limit_per_group: int = 8) -> tuple[str, list[dict[str, Any]]]:
        active = self.list_memories(companion_id=companion_id, status="active")
        used = [memory for memory in active if memory["status"] == "active"]
        groups = [
            ("Pinned needs and preferences", {"need", "preference", "companion_style", "voice_preference"}, True),
            ("Boundaries and avoidances", {"boundary"}, False),
            ("Relationship and story state", {"relationship_state", "story_fact", "identity_fact"}, False),
        ]
        lines: list[str] = []
        emitted: list[dict[str, Any]] = []
        for label, categories, pinned_only in groups:
            matches = [
                memory
                for memory in used
                if memory["category"] in categories and (memory["pinned"] or not pinned_only)
            ][:limit_per_group]
            if not matches:
                continue
            lines.append(f"{label}:")
            for memory in matches:
                lines.append(f"- {memory['content']}")
                emitted.append(memory)
        return "\n".join(lines), emitted


def _first_match(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip(" .,!?:;\"'")
    return ""


def _fragment(value: str) -> str:
    return re.split(r"[.!?]", value, maxsplit=1)[0].strip(" .,!?:;\"'")


def extract_personal_memories(user_text: str) -> list[dict[str, Any]]:
    text = " ".join(user_text.split())
    lower = text.lower()
    memories: list[dict[str, Any]] = []

    name = _first_match([r"\bmy name is ([A-Z][A-Za-z0-9_\- ]{1,40})", r"\bcall me ([A-Z][A-Za-z0-9_\- ]{1,40})"], text)
    if name:
        memories.append({"category": "identity_fact", "content": f"User preferred name: {name}.", "confidence": 1.0})

    remember = _fragment(_first_match([r"\bremember that ([^.!?]{3,180})"], text))
    if remember:
        memories.append({"category": "identity_fact", "content": remember[0].upper() + remember[1:] + ".", "confidence": 1.0})

    prefer = _fragment(_first_match([r"\bi prefer ([^.!?]{3,160})", r"\bi like ([^.!?]{3,160})", r"\bi love ([^.!?]{3,160})"], text))
    if prefer:
        memories.append({"category": "preference", "content": f"User prefers {prefer}.", "confidence": 0.95})

    need = _fragment(_first_match([r"\bi need you to ([^.!?]{3,180})", r"\bi want you to ([^.!?]{3,180})"], text))
    if need:
        memories.append({"category": "need", "content": f"User needs the companion to {need}.", "confidence": 0.9})

    avoid = _fragment(
        _first_match(
            [
                r"\bi don't like ([^.!?]{3,160})",
                r"\bi do not like ([^.!?]{3,160})",
                r"\bi hate ([^.!?]{3,160})",
                r"\bavoid ([^.!?]{3,160})",
                r"\bnever ([^.!?]{3,160})",
            ],
            text,
        )
    )
    if avoid:
        memories.append({"category": "boundary", "content": f"Avoid {avoid}.", "confidence": 0.95})

    if any(phrase in lower for phrase in ("answer shorter", "reply shorter", "too long", "keep it short", "be brief")):
        memories.append(
            {
                "category": "companion_style",
                "content": "User prefers shorter replies when the request is casual or direct.",
                "confidence": 0.9,
            }
        )
    if any(phrase in lower for phrase in ("answer longer", "reply longer", "more detail", "go deeper", "more detailed")):
        memories.append(
            {
                "category": "companion_style",
                "content": "User prefers more detailed replies when asking for depth or continuation.",
                "confidence": 0.85,
            }
        )
    if any(phrase in lower for phrase in ("warmer tone", "be warmer", "more affectionate", "more caring")):
        memories.append({"category": "companion_style", "content": "User likes a warmer and more affectionate tone.", "confidence": 0.85})
    if any(phrase in lower for phrase in ("more natural voice", "voice sounds", "speak faster", "speak slower", "different voice")):
        memories.append({"category": "voice_preference", "content": f"Voice feedback: {_fragment(text)}.", "confidence": 0.85})

    relationship = _fragment(_first_match([r"\bour relationship is ([^.!?]{3,180})", r"\bwe are ([^.!?]{3,180})"], text))
    if relationship:
        memories.append(
            {
                "category": "relationship_state",
                "content": f"Relationship state: {relationship}.",
                "confidence": 0.8,
                "status": "pending",
            }
        )

    story = _fragment(_first_match([r"\bin the story ([^.!?]{3,180})", r"\bcurrent scene is ([^.!?]{3,180})"], text))
    if story:
        memories.append({"category": "story_fact", "content": f"Story continuity: {story}.", "confidence": 0.8, "status": "pending"})

    deduped: list[dict[str, Any]] = []
    seen = set()
    for memory in memories:
        key = (memory["category"], memory_key(memory["content"]))
        if key not in seen:
            seen.add(key)
            deduped.append(memory)
    return deduped[:6]
