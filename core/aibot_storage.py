from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_data_dir() -> Path:
    return Path(".aibot")


@dataclass
class Conversation:
    id: str
    title: str
    created_at: str
    updated_at: str


class ConversationStore:
    def __init__(self, data_dir: Path | str = default_data_dir()):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "conversations.sqlite3"
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self.lock:
            self.conn.executescript(
                """
                create table if not exists conversations (
                    id text primary key,
                    title text not null,
                    created_at text not null,
                    updated_at text not null
                );

                create table if not exists messages (
                    id text primary key,
                    conversation_id text not null references conversations(id) on delete cascade,
                    role text not null,
                    content text not null,
                    created_at text not null
                );

                create index if not exists idx_messages_conversation_created
                    on messages(conversation_id, created_at);

                create table if not exists episodes (
                    id text primary key,
                    companion_id text not null,
                    conversation_id text,
                    summary text not null,
                    created_at text not null
                );

                create index if not exists idx_episodes_companion_created
                    on episodes(companion_id, created_at);
                """
            )
            # Phase 3 support: rolling session summary + how many turns it covers.
            # Added via migration so existing databases pick the columns up safely.
            existing = {row["name"] for row in self.conn.execute("pragma table_info(conversations)")}
            if "summary" not in existing:
                self.conn.execute("alter table conversations add column summary text not null default ''")
            if "summarized_count" not in existing:
                self.conn.execute("alter table conversations add column summarized_count integer not null default 0")
            self.conn.commit()

    def create_conversation(self, title: str) -> Conversation:
        now = utc_now()
        conversation = Conversation(
            id=str(uuid.uuid4()),
            title=title[:120] or "Untitled conversation",
            created_at=now,
            updated_at=now,
        )
        with self.lock:
            self.conn.execute(
                "insert into conversations (id, title, created_at, updated_at) values (?, ?, ?, ?)",
                (conversation.id, conversation.title, conversation.created_at, conversation.updated_at),
            )
            self.conn.commit()
        return conversation

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        with self.lock:
            row = self.conn.execute(
                "select id, title, created_at, updated_at from conversations where id = ?",
                (conversation_id,),
            ).fetchone()
        if not row:
            return None
        return Conversation(**dict(row))

    def list_conversations(self) -> list[Conversation]:
        with self.lock:
            rows = self.conn.execute(
                "select id, title, created_at, updated_at from conversations order by updated_at desc"
            ).fetchall()
        return [Conversation(**dict(row)) for row in rows]

    def get_summary(self, conversation_id: str) -> str:
        """Rolling session summary for the conversation (empty if none yet)."""
        with self.lock:
            row = self.conn.execute(
                "select summary from conversations where id = ?",
                (conversation_id,),
            ).fetchone()
        return str(row["summary"]) if row and row["summary"] else ""

    def get_summarized_count(self, conversation_id: str) -> int:
        """How many of the oldest messages are already folded into the summary."""
        with self.lock:
            row = self.conn.execute(
                "select summarized_count from conversations where id = ?",
                (conversation_id,),
            ).fetchone()
        return int(row["summarized_count"]) if row and row["summarized_count"] is not None else 0

    def set_summary(self, conversation_id: str, summary: str, summarized_count: int) -> None:
        with self.lock:
            self.conn.execute(
                "update conversations set summary = ?, summarized_count = ? where id = ?",
                (summary, int(summarized_count), conversation_id),
            )
            self.conn.commit()

    def add_episode(self, companion_id: str, summary: str, conversation_id: str = "") -> str:
        """Record an episodic memory ('what happened when') for later time/topic recall."""
        episode_id = str(uuid.uuid4())
        with self.lock:
            self.conn.execute(
                "insert into episodes (id, companion_id, conversation_id, summary, created_at) "
                "values (?, ?, ?, ?, ?)",
                (episode_id, companion_id, conversation_id or None, summary.strip(), utc_now()),
            )
            self.conn.commit()
        return episode_id

    def list_episodes(self, companion_id: str, limit: int = 20) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "select id, conversation_id, summary, created_at from episodes "
                "where companion_id = ? order by created_at desc limit ?",
                (companion_id, int(limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_message(self, conversation_id: str, role: str, content: str) -> str:
        message_id = str(uuid.uuid4())
        now = utc_now()
        with self.lock:
            self.conn.execute(
                "insert into messages (id, conversation_id, role, content, created_at) values (?, ?, ?, ?, ?)",
                (message_id, conversation_id, role, content, now),
            )
            self.conn.execute(
                "update conversations set updated_at = ? where id = ?",
                (now, conversation_id),
            )
            self.conn.commit()
        return message_id

    def get_messages(self, conversation_id: str) -> list[dict[str, str]]:
        with self.lock:
            rows = self.conn.execute(
                "select role, content from messages where conversation_id = ? order by created_at asc",
                (conversation_id,),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    def export_conversation(self, conversation_id: str, output_path: Path | str, fmt: str) -> Path:
        conversation = self.get_conversation(conversation_id)
        if not conversation:
            raise ValueError(f"Unknown conversation: {conversation_id}")

        messages = self.get_messages(conversation_id)
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        fmt = fmt.lower()

        if fmt == "json":
            payload: dict[str, Any] = {
                "conversation": conversation.__dict__,
                "messages": messages,
            }
            output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        elif fmt == "txt":
            output.write_text(self._plain_text(conversation, messages), encoding="utf-8")
        elif fmt == "md":
            output.write_text(self._markdown(conversation, messages), encoding="utf-8")
        else:
            raise ValueError(f"Unsupported export format: {fmt}")

        return output

    def _plain_text(self, conversation: Conversation, messages: list[dict[str, str]]) -> str:
        lines = [conversation.title, f"Conversation ID: {conversation.id}", ""]
        for message in messages:
            lines.extend([message["role"].upper(), message["content"], ""])
        return "\n".join(lines)

    def _markdown(self, conversation: Conversation, messages: list[dict[str, str]]) -> str:
        lines = [f"# {conversation.title}", "", f"`{conversation.id}`", ""]
        for message in messages:
            lines.extend([f"## {message['role'].title()}", "", message["content"], ""])
        return "\n".join(lines)
