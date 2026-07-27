"""The Revenant agent loop.

A model-agnostic tool-calling loop over the local LLM:

    build system + messages
    repeat up to max_steps:
        msg = call_model_message(config, messages, tools=native_schema)
        action = parse_action(msg.content, msg)          # native OR prompt-based
        if action is FinalAnswer: done
        else: observation = registry.dispatch(action)     # (approval gate: P3)
              append assistant turn + observation, continue

Reuses the existing local LLM layer (`call_model_message`) -- no cloud. Works on
tool-capable models (qwen2.5, via native tool_calls) AND plain models (via the
prompt-based ```action protocol), because `parse_action` handles both and we pass
the native `tools` schema opportunistically.

Step events are emitted through an optional `on_event` callback so a CLI/UI can
stream the trace (thinking -> action -> observation) live.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from nerva_core.local_llm_writer import ChatConfig, call_model_message, LocalLLMError, estimate_tokens
from nerva_agent.agent_tools import ToolRegistry, ToolError
from nerva_agent.agent_protocol import (
    FinalAnswer,
    ToolCall,
    parse_action,
    render_system_block,
    looks_like_attempted_action,
    MALFORMED_ACTION_NUDGE,
)


@dataclass
class AgentEvent:
    """One thing that happened in the loop, for streaming/logging."""

    kind: str  # "assistant" | "action" | "observation" | "final" | "error" | "limit"
    text: str = ""
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    step: int = 0


@dataclass
class AgentResult:
    """The outcome of a run."""

    answer: str
    steps: int
    stopped_reason: str  # "final" | "max_steps" | "error"
    events: list[AgentEvent] = field(default_factory=list)


EventSink = Callable[[AgentEvent], None]
# approve(tool_name, args) -> True to allow the call, False to deny it.
ApproveHook = Callable[[str, dict], bool]


class AgentLoop:
    """Drives one goal to completion (or a step cap) using a tool registry."""

    def __init__(
        self,
        config: ChatConfig,
        registry: ToolRegistry,
        *,
        system_preamble: str = "",
        max_steps: int = 15,
        max_bad_parses: int = 3,
        use_native_tools: bool | None = None,
        on_event: EventSink | None = None,
        approve: "ApproveHook | None" = None,
        auto_approve: bool = False,
        max_context_tokens: int = 6000,
        keep_recent_steps: int = 3,
    ) -> None:
        self.config = config
        self.registry = registry
        self.system_preamble = system_preamble
        self.max_steps = max_steps
        self.max_bad_parses = max_bad_parses
        # None = auto-detect per model on first run (probe once, cached); True/False
        # force the choice. Auto keeps native for tool-capable models (qwen2.5) and
        # falls back to the prompt-based protocol otherwise (Stheno).
        self.use_native_tools = use_native_tools
        self.on_event = on_event
        # Context management: when the running transcript exceeds max_context_tokens,
        # the oldest step pairs (assistant + observation) are folded into a short
        # summary so long runs don't overflow the local model's window. The system
        # prompt, the goal, and the most recent keep_recent_steps step-pairs are
        # always retained verbatim. This is the loop's local analog of compaction.
        self.max_context_tokens = max_context_tokens
        self.keep_recent_steps = keep_recent_steps
        # approve(tool_name, args) -> bool. Called before any tool whose
        # requires_approval flag is set. auto_approve (yolo) skips the prompt but
        # NOT the damage guards inside the tools themselves (e.g. bash footgun block).
        self.approve = approve
        self.auto_approve = auto_approve

    # --- helpers -----------------------------------------------------------
    def _emit(self, event: AgentEvent, sink_events: list[AgentEvent]) -> None:
        sink_events.append(event)
        if self.on_event is not None:
            self.on_event(event)

    def _system_prompt(self) -> str:
        parts = []
        if self.system_preamble.strip():
            parts.append(self.system_preamble.strip())
        # Always include the prompt-based protocol block: it documents the tools
        # and gives non-native models a way to act. Native models ignore the
        # format hint and use tool_calls instead.
        parts.append(render_system_block(self.registry))
        return "\n\n".join(parts)

    # --- context management ------------------------------------------------
    @staticmethod
    def _total_tokens(messages: list[dict[str, Any]]) -> int:
        return sum(estimate_tokens(m.get("content", "")) for m in messages)

    def _compact_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fold the oldest step-pairs into a summary when over the token budget.

        Layout is always [system, goal, (assistant, observation)*]. We keep
        `messages[0]` (system) and `messages[1]` (goal) verbatim, keep the last
        `keep_recent_steps` pairs verbatim, and replace everything in between with
        one compact summary user-turn listing what was already done. Returns the
        (possibly unchanged) message list; emits a "compact" event when it acts.
        """
        if self._total_tokens(messages) <= self.max_context_tokens:
            return messages
        head = messages[:2]                       # system + goal
        middle = messages[2:]
        keep_tail = self.keep_recent_steps * 2    # each step = assistant + observation
        if len(middle) <= keep_tail:
            return messages                       # nothing old enough to fold
        old, recent = middle[:-keep_tail], middle[-keep_tail:]

        # Summarize the folded portion: pull the tool actions/observations into a
        # terse recap so the model keeps situational awareness without the bulk.
        lines: list[str] = []
        for msg in old:
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            # Observation turns are prefixed "Result of <tool>:"; keep a one-line gist.
            first = content.splitlines()[0]
            lines.append(first[:200])
        recap = "\n".join(lines[-40:]) or "(earlier steps)"
        summary_turn = {
            "role": "user",
            "content": (
                "[Earlier progress compacted to save context — do not repeat work "
                "already done below:]\n" + recap
            ),
        }
        return head + [summary_turn] + recent

    # --- main loop ---------------------------------------------------------
    def run(self, goal: str) -> AgentResult:
        events: list[AgentEvent] = []
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": goal},
        ]
        # Resolve native-tool use: explicit flag, else auto-detect once per model.
        use_native = self.use_native_tools
        if use_native is None:
            from nerva_agent.agent_native_tools import supports_native_tools
            try:
                use_native = supports_native_tools(self.config)
            except Exception:  # noqa: BLE001 - detection failure -> prompt-based
                use_native = False
        native_tools = self.registry.native_schema() if use_native else None
        bad_parses = 0

        for step in range(1, self.max_steps + 1):
            before = len(messages)
            messages = self._compact_messages(messages)
            if len(messages) < before:
                self._emit(
                    AgentEvent("compact", text=f"compacted {before - len(messages)} old turns", step=step),
                    events,
                )
            try:
                message = call_model_message(self.config, messages, tools=native_tools)
            except LocalLLMError as exc:
                self._emit(AgentEvent("error", text=str(exc), step=step), events)
                return AgentResult("", step - 1, "error", events)

            content = (message.get("content") or "").strip()
            action = parse_action(content, message)

            # --- Final answer -------------------------------------------------
            if isinstance(action, FinalAnswer):
                # A reply that LOOKED like a failed action gets one nudge, then
                # we accept it as final to avoid looping on a weak model.
                if (
                    action.text
                    and bad_parses < self.max_bad_parses
                    and looks_like_attempted_action(action.text, self.registry)
                    and not _has_valid_json_action(content)
                ):
                    bad_parses += 1
                    self._emit(AgentEvent("assistant", text=action.text, step=step), events)
                    messages.append({"role": "assistant", "content": content})
                    messages.append({"role": "user", "content": MALFORMED_ACTION_NUDGE})
                    continue
                self._emit(AgentEvent("final", text=action.text, step=step), events)
                return AgentResult(action.text, step, "final", events)

            # --- Tool call ----------------------------------------------------
            assert isinstance(action, ToolCall)
            if content:
                self._emit(AgentEvent("assistant", text=content, step=step), events)
            self._emit(
                AgentEvent("action", tool=action.tool, args=action.args, step=step), events
            )

            # --- Approval gate (mutating / requires_approval tools) -----------
            tool = self.registry.get(action.tool)
            if tool is not None and tool.requires_approval and not self.auto_approve:
                allowed = True
                if self.approve is not None:
                    self._emit(
                        AgentEvent("approval", tool=action.tool, args=action.args, step=step), events
                    )
                    try:
                        allowed = bool(self.approve(action.tool, action.args))
                    except Exception:  # noqa: BLE001 - a hook error denies, never crashes
                        allowed = False
                if not allowed:
                    observation = (
                        f"The user DECLINED to run {action.tool}. Do not retry it; "
                        "adjust your approach or ask what to do instead."
                    )
                    self._emit(
                        AgentEvent("observation", text=observation, tool=action.tool, step=step), events
                    )
                    messages.append({"role": "assistant", "content": content or f"(calling {action.tool})"})
                    messages.append({"role": "user", "content": f"Result of {action.tool}:\n{observation}"})
                    continue

            try:
                observation = self.registry.dispatch(action.tool, action.args)
            except ToolError as exc:
                observation = f"ERROR: {exc}"
            self._emit(
                AgentEvent("observation", text=observation, tool=action.tool, step=step), events
            )

            # Record the turn. Keep the assistant's raw content, then feed the
            # observation back as a user turn (uniform across native + prompt paths).
            messages.append({"role": "assistant", "content": content or f"(calling {action.tool})"})
            messages.append(
                {"role": "user", "content": f"Result of {action.tool}:\n{observation}"}
            )

        self._emit(AgentEvent("limit", text=f"hit max_steps={self.max_steps}", step=self.max_steps), events)
        return AgentResult("", self.max_steps, "max_steps", events)


def _has_valid_json_action(content: str) -> bool:
    """True if parse_action would have returned a ToolCall from this text alone
    (used to distinguish 'genuinely final' from 'tried to act and failed')."""
    return isinstance(parse_action(content, None), ToolCall)
