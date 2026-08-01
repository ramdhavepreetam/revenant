"""Sub-agent spawn tool: the loop calling itself (F15.1, ADR-0009).

Delegate a self-contained sub-goal to a fresh, budgeted `AgentLoop` with its own
scoped tool set, and get back a short *summary* — not the full transcript — so
the parent's context stays small. This is the composition layer: the router was
written to be "reusable by the future coding agent loop", and sub-agents are the
loop instantiating itself.

To keep `nerva_agent` free of CLI concerns, the tool takes a `loop_factory`:
    loop_factory(goal, tools, depth) -> an AgentLoop ready to .run(goal)
The CLI supplies this (it knows how to build a loop with a scoped registry,
budget, and role). This module only orchestrates: depth-guard, run, summarize.

Guardrails (ADR-0009):
- **Max spawn depth** prevents infinite recursion; the tool refuses beyond it.
- The tool is `mutating=True` (a sub-agent may edit) ⇒ approval-gated, and the
  parent's checkpointer wraps the whole sub-run as one undo boundary.
- Offline invariant preserved — no new model backend, just another local loop.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, Protocol

from nerva_agent.agent_loop import AgentEvent, EventSink
from nerva_agent.agent_tools import Tool, ToolParam


class _LoopLike(Protocol):
    on_event: "EventSink | None"
    def run(self, goal: str, history: "list[dict] | None" = None): ...


# loop_factory(goal, tools, depth) -> a loop ready to run `goal`.
LoopFactory = Callable[[str, "list[str] | None", int], _LoopLike]

DEFAULT_MAX_DEPTH = 2


def _label_for(goal: str, max_len: int = 32) -> str:
    """A short, stable slug for a sub-agent, derived from its goal.

    Used to tag every event the sub-agent emits so a UI can group them in a lane
    and non-TUI consoles can prefix them (`[sub:<label>] …`).
    """
    words = (goal or "").strip().split()
    slug = "-".join(words)[:max_len].strip("-").lower()
    return slug or "subagent"


def _relay_sink(parent_sink: "EventSink | None", label: str) -> "EventSink | None":
    """Wrap the parent's sink so a child loop's events flow up, stamped with `label`.

    Preserves an already-set `agent` (so a grandchild keeps its own label rather
    than being re-tagged by its parent), giving correct nesting at any depth.
    Returns None when there's no parent sink — the child then behaves as before.
    """
    if parent_sink is None:
        return None

    def sink(ev: AgentEvent) -> None:
        parent_sink(replace(ev, agent=ev.agent or label))

    return sink


def summarize_result(result) -> str:
    """A compact observation from a sub-run's AgentResult (not the transcript)."""
    answer = (getattr(result, "answer", "") or "").strip()
    steps = getattr(result, "steps", 0)
    reason = getattr(result, "stopped_reason", "")
    status = {
        "final": "completed",
        "max_steps": "hit its step budget",
        "error": "errored",
    }.get(reason, reason or "finished")
    head = f"Sub-agent {status} after {steps} step(s)."
    if answer:
        # Keep the summary bounded so a chatty sub-agent can't bloat the parent.
        if len(answer) > 1200:
            answer = answer[:1200] + " […]"
        return f"{head}\n{answer}"
    return head


def build_spawn_tool(
    loop_factory: LoopFactory,
    *,
    depth: int = 0,
    max_depth: int = DEFAULT_MAX_DEPTH,
    parent_sink: "EventSink | None" = None,
) -> Tool:
    """A `spawn_subagent` Tool bound to `loop_factory` at the current `depth`.

    `tools` (optional, comma-separated) scopes the sub-agent's registry; the
    factory decides how to apply it (reusing the skill tool-filter). A run at or
    beyond `max_depth` is refused rather than recursing without bound.

    `parent_sink` is the parent loop's `on_event`. When given (V2, ADR-0017), the
    sub-agent's events are relayed up stamped with a goal-derived label, bracketed
    by `agent_start`/`agent_end`, so a UI can show the sub-agent working live.
    When None, sub-agents run silently exactly as before.
    """
    def run(goal: str, tools: str = "") -> str:
        if depth >= max_depth:
            return (f"Refused: sub-agent spawn depth limit ({max_depth}) reached. "
                    "Do this work directly instead of delegating further.")
        goal = (goal or "").strip()
        if not goal:
            return "Refused: spawn_subagent needs a non-empty goal."
        tool_list = [t.strip() for t in tools.split(",") if t.strip()] or None
        label = _label_for(goal)
        try:
            loop = loop_factory(goal, tool_list, depth + 1)
        except Exception as exc:  # noqa: BLE001 - surface as an observation, not a crash
            return f"ERROR: could not build sub-agent: {exc}"
        # Route the child's events up to the parent's UI, tagged with `label`.
        relay = _relay_sink(parent_sink, label)
        if relay is not None:
            loop.on_event = relay
            parent_sink(AgentEvent("agent_start", text=goal, agent=label))
        try:
            result = loop.run(goal)
        except Exception as exc:  # noqa: BLE001
            if relay is not None:
                parent_sink(AgentEvent("agent_end", text=f"errored: {exc}", agent=label))
            return f"ERROR: sub-agent run failed: {exc}"
        summary = summarize_result(result)
        if relay is not None:
            parent_sink(AgentEvent("agent_end", text=summary, agent=label))
        return summary

    return Tool(
        name="spawn_subagent",
        description=(
            "Delegate a self-contained sub-task to a fresh agent with its own "
            "budget, and get back a short summary. Use for a well-scoped chunk of "
            "work you can describe in one goal; you stay in control of the plan."
        ),
        params=[
            ToolParam("goal", "string",
                      "The complete, self-contained goal for the sub-agent."),
            ToolParam("tools", "string",
                      "Optional comma-separated tool names to restrict the "
                      "sub-agent to (e.g. 'read_file,run_bash').", required=False),
        ],
        run=run,
        mutating=True,   # a sub-agent may edit ⇒ approval-gated (ADR-0009)
    )
