from __future__ import annotations

import uuid
from pathlib import Path

_CATEGORY_TO_KIND: dict[str, str] = {
    "identity_fact": "fact",
    "preference": "preference",
    "boundary": "fact",
    "relationship_fact": "fact",
    "emotional_state": "fact",
    "voice_preference": "preference",
    "desire": "preference",
    "history_fact": "fact",
}

# Stable session-node ID prefix for companion memory index sessions.
# We derive a deterministic ID so rebuild can delete the old session by ID.
_SESSION_ID_PREFIX = "s_companion_"


def _companion_session_id(companion_id: str) -> str:
    """Deterministic session node ID for a companion's memory index."""
    safe = companion_id.replace(" ", "_").replace("-", "_")[:32]
    return f"{_SESSION_ID_PREFIX}{safe}"


class NervaPackMemory:
    """
    Conversation memory adapter backed by NervaPack 0.4.4.

    Structured facts (personal memories, summaries) use the nervapack.memory
    MemoryStore (SQLite + FTS5, bi-temporal, scored recall). Raw turns continue
    to use the graph-layer VectorStore (ChromaDB) for dense semantic search.

    0.4.4 additions used:
      - MemoryStore.list_sessions() — session inventory
      - MemoryStore.delete_session(session_id, purge) — clean cascade-delete on rebuild
    """

    def __init__(self, data_dir: Path | str):
        from nervapack.graph.vector_store import VectorStore
        from nervapack.memory.store import MemoryStore

        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Graph-layer store (ChromaDB) — raw turn indexing
        self.store = VectorStore(db_path=str(self.data_dir / "nervapack_chroma"))

        # Agent memory store (SQLite + FTS5) — structured facts and summaries
        self._ms = MemoryStore(db_path=str(self.data_dir / "agent_memory.db"))

    def _ms_for(self, companion_id: str):
        """Return MemoryStore with namespace set to companion_id."""
        self._ms.namespace = companion_id.strip() or "default"
        return self._ms

    def _ensure_companion_session(self, ms, companion_id: str) -> str:
        """
        Return the session node ID for this companion, creating it if missing.
        Facts are stored with session_id pointing to this node so
        delete_session() (0.4.4) can cascade-delete the whole index cleanly.
        """
        sid = _companion_session_id(companion_id)
        if ms.get_node(sid) is None:
            ms.add_node(
                kind="session",
                content=f"Memory index session for companion: {companion_id}",
                node_id=sid,
            )
        return sid

    # ── Raw turn indexing (VectorStore) ─────────────────────────────────────────

    def remember_message(self, conversation_id: str, role: str, content: str) -> None:
        content = content.strip()
        if not content:
            return
        chunk = {
            "header": f"{role} message",
            "file_path": f"conversation/{conversation_id}/{role}/{uuid.uuid4()}",
            "content": content,
        }
        self.store.ingest_chunks([chunk])

    # ── Structured fact indexing (MemoryStore) ───────────────────────────────────

    def remember_note(self, companion_id: str, bucket: str, content: str) -> None:
        content = content.strip()
        if not content:
            return
        ms = self._ms_for(companion_id)
        kind = _CATEGORY_TO_KIND.get(bucket, "fact")
        prefix = "Boundary: " if bucket == "boundary" else ""
        session_id = self._ensure_companion_session(ms, companion_id)
        ms.add_node(
            kind=kind,
            content=f"{prefix}{content}",
            session_id=session_id,
            data={"category": bucket},
        )

    def remember_summary(self, companion_id: str, conversation_id: str, content: str) -> None:
        """Index a session summary so past sessions are reachable via recall."""
        content = content.strip()
        if not content:
            return
        ms = self._ms_for(companion_id)
        session_id = self._ensure_companion_session(ms, companion_id)
        ms.add_node(
            kind="outcome",
            content=content,
            session_id=session_id,
            data={"conversation_id": conversation_id},
        )

    def rebuild_notes(self, memories: list[dict]) -> None:
        """Re-index all active personal memories. Uses 0.4.4 delete_session for clean cascade."""
        # Group by companion_id
        by_companion: dict[str, list[dict]] = {}
        for memory in memories:
            cid = str(memory.get("companion_id") or "companion")
            by_companion.setdefault(cid, []).append(memory)

        for companion_id, companion_memories in by_companion.items():
            ms = self._ms_for(companion_id)

            # 0.4.4: delete_session cascades to all linked nodes cleanly
            sid = _companion_session_id(companion_id)
            try:
                ms.delete_session(sid, purge=False)  # tombstone; keeps audit trail
            except Exception:
                pass

            # Create fresh session node with same deterministic ID.
            # Use a new unique ID if the old one still exists (tombstoned rows remain in DB).
            existing = ms.get_node(sid)
            if existing is not None:
                # Old node is tombstoned — generate a fresh ID for this rebuild pass
                sid = f"{sid}_{uuid.uuid4().hex[:8]}"
            ms.add_node(
                kind="session",
                content=f"Memory index session for companion: {companion_id}",
                node_id=sid,
            )

            # Re-ingest all active memories linked to the new session
            for memory in companion_memories:
                content = str(memory.get("content") or "").strip()
                if not content:
                    continue
                category = str(memory.get("category") or "preference")
                kind = _CATEGORY_TO_KIND.get(category, "fact")
                prefix = "Boundary: " if category == "boundary" else ""
                memory_id = str(memory.get("id") or uuid.uuid4())
                ms.add_node(
                    kind=kind,
                    content=f"{prefix}{content}",
                    session_id=sid,
                    data={"source_id": memory_id, "category": category},
                )

    # ── Recall ───────────────────────────────────────────────────────────────────

    def recall(self, query: str, limit: int = 5, companion_id: str = "") -> list[str]:
        query = query.strip()
        if not query:
            return []
        if companion_id:
            return self._recall_ms(self._ms_for(companion_id), query, limit)
        return self._recall_chroma(query, limit)

    def _recall_ms(self, ms, query: str, limit: int) -> list[str]:
        try:
            from nervapack.memory.recall import recall as ms_recall
            result_md = ms_recall(ms, query, budget_tokens=400, hops=1)
            lines = [
                ln.lstrip("- ").split(" — ", 1)[-1].strip()
                for ln in result_md.splitlines()
                if ln.strip().startswith("-")
            ]
            return [l for l in lines if l][:limit]
        except Exception:
            return self._recall_chroma(query, limit)

    def _recall_chroma(self, query: str, limit: int) -> list[str]:
        results = self.store.search(query, n_results=limit)
        documents = results.get("documents") or []
        if not documents:
            return []
        return [doc for doc in documents[0] if doc]

    # ── 0.4.4: Session inventory (useful for debugging / admin) ─────────────────

    def list_companion_sessions(self, companion_id: str) -> list[dict]:
        """List all sessions for a companion namespace (0.4.4 API)."""
        return self._ms_for(companion_id).list_sessions()


def format_memory_context(memories: list[str]) -> str:
    if not memories:
        return ""
    lines = ["Relevant local memory:"]
    for index, memory in enumerate(memories, start=1):
        lines.append(f"{index}. {memory}")
    return "\n".join(lines)
