"""Cross-session memory store for the coding agent (M0, ADR-0022).

A lightweight, per-project store of durable knowledge — facts, decisions, and
outcomes the agent learns and recalls across runs ("this project uses pytest",
"editing config.py directly breaks the loader — use write_scalar"). It is NOT
chat history (that's the session store); it's distilled project knowledge that
gets recalled into the system preamble at the start of a later run.

Backend: Python's **stdlib** `sqlite3` + the **FTS5** full-text extension — no new
dependency, fully offline (chromadb's ~150-200MB was rejected; see ADR-0022).
Persists to `<ws>/.aibot/memory.db`, mirroring how sessions/checkpoints/the code
graph already live under `.aibot/`.

Everything degrades: a DB that can't open or a malformed FTS5 query never raises
to the caller — recall returns [] and writes are best-effort — so memory can
never break a run (same discipline as the code-graph cache).
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

# The kinds a memory can be. Free-form is tolerated, but these are the documented
# categories the tools/UI use.
KINDS = ("fact", "decision", "outcome", "note")


@dataclass
class Memory:
    """One stored memory row."""

    id: int
    kind: str
    content: str
    created_at: float
    source: str = ""


def _fts5_available(conn: sqlite3.Connection) -> bool:
    """True if this SQLite build has the FTS5 extension (nearly all do)."""
    try:
        conn.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE _fts5_probe")
        return True
    except sqlite3.Error:
        return False


class MemoryStore:
    """Durable project memory over stdlib SQLite (+ FTS5 when available).

    Open with a db path (usually `<ws>/.aibot/memory.db`) or `:memory:` for tests.
    All methods are best-effort: on any sqlite error they degrade (recall → [],
    writes → no-op) rather than raising, so memory never breaks the agent loop.
    """

    def __init__(self, db_path: "str | Path") -> None:
        self.db_path = str(db_path)
        self._conn: "sqlite3.Connection | None" = None
        self._fts = False
        try:
            if self.db_path != ":memory:":
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._fts = _fts5_available(self._conn)
            self._init_schema()
        except (sqlite3.Error, OSError, ValueError):
            # Unopenable/locked/invalid path → a null store (all ops degrade).
            # Never raises — memory must not break a run.
            self._conn = None

    # --- schema ------------------------------------------------------------
    def _init_schema(self) -> None:
        assert self._conn is not None
        c = self._conn
        c.execute(
            "CREATE TABLE IF NOT EXISTS memories ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, "
            "content TEXT NOT NULL, created_at REAL NOT NULL, source TEXT DEFAULT '')"
        )
        if self._fts:
            # Contentless-linked FTS index over `content`, kept in sync by triggers.
            c.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
                "content, content='memories', content_rowid='id')"
            )
            c.executescript(
                "CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN "
                "  INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content); END;"
                "CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN "
                "  INSERT INTO memories_fts(memories_fts, rowid, content) "
                "  VALUES('delete', old.id, old.content); END;"
            )
        c.commit()

    @property
    def available(self) -> bool:
        return self._conn is not None

    # --- writes ------------------------------------------------------------
    def remember(self, content: str, kind: str = "fact", source: str = "") -> "int | None":
        """Store a memory. Returns its id, or None if the store is unavailable or
        the content is empty. Skips an exact-content duplicate (returns its id)."""
        content = (content or "").strip()
        if not content or self._conn is None:
            return None
        kind = kind if kind in KINDS else "fact"
        try:
            existing = self._conn.execute(
                "SELECT id FROM memories WHERE content = ?", (content,)
            ).fetchone()
            if existing:
                return int(existing[0])
            cur = self._conn.execute(
                "INSERT INTO memories(kind, content, created_at, source) VALUES (?,?,?,?)",
                (kind, content, time.time(), source),
            )
            self._conn.commit()
            return int(cur.lastrowid)
        except sqlite3.Error:
            return None

    def forget(self, memory_id: int) -> bool:
        if self._conn is None:
            return False
        try:
            cur = self._conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            self._conn.commit()
            return cur.rowcount > 0
        except sqlite3.Error:
            return False

    def clear(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute("DELETE FROM memories")
            self._conn.commit()
        except sqlite3.Error:
            pass

    # --- reads -------------------------------------------------------------
    def count(self) -> int:
        if self._conn is None:
            return 0
        try:
            return int(self._conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
        except sqlite3.Error:
            return 0

    def list_all(self, limit: int = 100) -> "list[Memory]":
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                "SELECT id, kind, content, created_at, source FROM memories "
                "ORDER BY created_at DESC, id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [Memory(*r) for r in rows]
        except sqlite3.Error:
            return []

    def recall(self, query: str, limit: int = 5) -> "list[Memory]":
        """Return the memories most relevant to `query` (newest-first tiebreak).

        Uses FTS5 MATCH when available; a malformed FTS query (special chars) or a
        non-FTS build falls back to a LIKE scan. Empty query returns the most
        recent memories. Never raises.
        """
        query = (query or "").strip()
        if self._conn is None:
            return []
        if not query:
            return self.list_all(limit)
        if self._fts:
            try:
                fts_q = _to_fts_query(query)
                if fts_q:
                    rows = self._conn.execute(
                        "SELECT m.id, m.kind, m.content, m.created_at, m.source "
                        "FROM memories_fts f JOIN memories m ON m.id = f.rowid "
                        "WHERE memories_fts MATCH ? "
                        "ORDER BY rank, m.created_at DESC LIMIT ?", (fts_q, limit),
                    ).fetchall()
                    return [Memory(*r) for r in rows]
            except sqlite3.Error:
                pass  # fall through to LIKE
        return self._recall_like(query, limit)

    def _recall_like(self, query: str, limit: int) -> "list[Memory]":
        """LIKE fallback: match any word of the query, newest first."""
        words = [w for w in _tokens(query)][:6]
        if not words:
            return self.list_all(limit)
        try:
            clause = " OR ".join("content LIKE ?" for _ in words)
            params = [f"%{w}%" for w in words] + [limit]
            rows = self._conn.execute(
                f"SELECT id, kind, content, created_at, source FROM memories "
                f"WHERE {clause} ORDER BY created_at DESC, id DESC LIMIT ?", params
            ).fetchall()
            return [Memory(*r) for r in rows]
        except sqlite3.Error:
            return []

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None


# --- query helpers ------------------------------------------------------------

def _tokens(text: str) -> "list[str]":
    """Alphanumeric word tokens, lowercased; keeps dotted/slashed identifiers
    readable by splitting on non-word runs but preserving '.', '_', '/'."""
    out, cur = [], []
    for ch in text.lower():
        if ch.isalnum() or ch in "._/-":
            cur.append(ch)
        elif cur:
            out.append("".join(cur)); cur = []
    if cur:
        out.append("".join(cur))
    return [t for t in out if len(t) > 1]


def _to_fts_query(query: str) -> str:
    """Build a safe FTS5 MATCH string: OR the query's word tokens, each quoted so
    punctuation (paths, `foo.bar`) can't be read as FTS operators."""
    toks = _tokens(query)[:8]
    return " OR ".join(f'"{t}"' for t in toks)
