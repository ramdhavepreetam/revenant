from __future__ import annotations

import uuid
from pathlib import Path


class NervaPackMemory:
    """
    Conversation memory adapter backed by NervaPack's Chroma vector store.

    NervaPack is optimized for graph/code retrieval, but its VectorStore gives us
    an offline persistent semantic index. Durable chat records still live in
    SQLite; this class is for recall.
    """

    def __init__(self, data_dir: Path | str):
        from nervapack.graph.vector_store import VectorStore

        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = VectorStore(db_path=str(self.data_dir / "nervapack_chroma"))

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

    def remember_note(self, companion_id: str, bucket: str, content: str) -> None:
        content = content.strip()
        if not content:
            return

        chunk = {
            "header": f"{bucket} companion memory",
            "file_path": f"companion/{companion_id}/{bucket}/{uuid.uuid4()}",
            "content": content,
        }
        self.store.ingest_chunks([chunk])

    def rebuild_notes(self, memories: list[dict]) -> None:
        try:
            self.store.collection.delete(where={"type": "markdown"})
        except Exception:
            pass
        chunks = []
        for memory in memories:
            content = str(memory.get("content") or "").strip()
            if not content:
                continue
            companion_id = str(memory.get("companion_id") or "companion")
            category = str(memory.get("category") or "memory")
            memory_id = str(memory.get("id") or uuid.uuid4())
            chunks.append(
                {
                    "header": f"{category} personal memory",
                    "file_path": f"personal/{companion_id}/{category}/{memory_id}",
                    "content": content,
                }
            )
        if chunks:
            self.store.ingest_chunks(chunks)

    def recall(self, query: str, limit: int = 5) -> list[str]:
        query = query.strip()
        if not query:
            return []

        results = self.store.search(query, n_results=limit)
        documents = results.get("documents") or []
        if not documents:
            return []
        return [doc for doc in documents[0] if doc]


def format_memory_context(memories: list[str]) -> str:
    if not memories:
        return ""

    lines = ["Relevant local memory:"]
    for index, memory in enumerate(memories, start=1):
        lines.append(f"{index}. {memory}")
    return "\n".join(lines)
