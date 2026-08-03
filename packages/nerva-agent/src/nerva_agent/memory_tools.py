"""Agent tools for cross-session memory (M1, ADR-0022).

`remember` / `recall` let the coding agent deliberately save and look up durable
project knowledge mid-task. Both are `mutating=False` — writing to the agent's own
memory (under `.aibot/`) is not a workspace mutation, so they need no approval
gate, exactly like the read-only code-graph tools. Bound to a `MemoryStore`; wired
into the registry in every mode.
"""
from __future__ import annotations

from nerva_agent.agent_tools import Tool, ToolParam
from nerva_agent.memory_store import KINDS, MemoryStore


def _fmt(mem) -> str:
    return f"[{mem.id}] ({mem.kind}) {mem.content}"


def build_memory_tools(store: MemoryStore) -> "list[Tool]":
    """Read/write memory tools bound to `store` (mutating=False — no approval)."""

    def remember(note: str, kind: str = "fact") -> str:
        mid = store.remember(note, kind=kind, source="agent")
        if mid is None:
            return "Nothing saved (empty note or memory unavailable)."
        return (f"Remembered as memory #{mid}. It will be recalled in future runs "
                "on this project.")

    def recall(query: str) -> str:
        hits = store.recall(query, limit=5)
        if not hits:
            return "No project memories match that. (Use `remember` to save one.)"
        return "Project memory:\n" + "\n".join("  " + _fmt(h) for h in hits)

    return [
        Tool(
            "remember",
            "Save a durable fact about THIS project to long-term memory so future "
            "runs recall it (e.g. conventions, where things live, a pitfall to "
            "avoid). Use for stable knowledge, not step-by-step chatter.",
            [
                ToolParam("note", "string", "The fact to remember (one clear sentence)."),
                ToolParam("kind", "string",
                          f"One of: {', '.join(KINDS)} (default: fact).", required=False),
            ],
            run=remember,
            mutating=False,
            parallel_safe=True,
        ),
        Tool(
            "recall",
            "Look up what's already known about this project from long-term "
            "memory (facts saved in earlier runs). Search by keywords.",
            [ToolParam("query", "string", "What to look up (keywords).")],
            run=recall,
            mutating=False,
            parallel_safe=True,
        ),
    ]
