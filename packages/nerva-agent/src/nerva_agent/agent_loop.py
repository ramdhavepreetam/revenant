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

from nerva_core.local_llm_writer import (
    ChatConfig, call_model, call_model_message, stream_message,
    LocalLLMError, estimate_tokens,
)
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
class ContextInfo:
    """A snapshot of the running transcript's size vs. its budget (V1, ADR-0017).

    Carried on `AgentEvent.context` for `kind == "context"` events so a UI can
    show a live "how full is the window" gauge. `folded` is True only on the
    event emitted right after a compaction fold happened this step.
    """

    used_tokens: int
    max_tokens: int
    folded: bool = False


@dataclass
class AgentEvent:
    """One thing that happened in the loop, for streaming/logging."""

    # "assistant" | "action" | "observation" | "final" | "error" | "limit"
    # | "approval" | "compact" | "context" | "agent_start" | "agent_end"
    # | "interrupted" | "token"
    # W1 (ADR-0019): "token" events carry an incremental `text` delta as the model
    # generates the assistant turn, so a UI can render the answer as it streams.
    # They are purely additive — a consumer that ignores "token" still sees the
    # whole text on the subsequent "assistant"/"final" event (byte-parity).
    kind: str
    text: str = ""
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    step: int = 0
    # V0 (ADR-0017): additive, optional fields. `agent` labels which agent an
    # event came from ("" = the root loop; a slug = a sub-agent, so a UI can show
    # multi-agent work in nested lanes). `context` is populated only on
    # kind == "context". Both default empty so every existing consumer is unchanged.
    agent: str = ""
    context: "ContextInfo | None" = None


@dataclass
class AgentResult:
    """The outcome of a run."""

    answer: str
    steps: int
    stopped_reason: str  # "final" | "max_steps" | "error"
    events: list[AgentEvent] = field(default_factory=list)
    # The full message transcript at the point the run ended. A multi-turn driver
    # (the REPL) threads this back into the next run() as `history` so the agent
    # keeps prior context; single-shot callers can ignore it.
    messages: list[dict[str, Any]] = field(default_factory=list)


EventSink = Callable[[AgentEvent], None]
# approve(tool_name, args) -> True to allow the call, False to deny it.
ApproveHook = Callable[[str, dict], bool]
# before_tool(tool_name, args) -> None. Fires right before a mutating tool runs
# (used to snapshot files for undo). Its return value is ignored; exceptions are
# swallowed so a checkpoint failure never aborts the tool call.
BeforeToolHook = Callable[[str, dict], None]
# after_tool(tool_name, args, observation) -> str | None. Fires right AFTER a
# mutating tool runs (used to verify the result — H1, ADR-0012). A returned
# string is APPENDED to the observation the model sees (e.g. a verification
# failure to repair); None appends nothing. Exceptions are swallowed so a
# verifier error never breaks the loop.
AfterToolHook = Callable[[str, dict, str], "str | None"]


class AgentLoop:
    """Drives one goal to completion (or a step cap) using a tool registry."""

    def __init__(
        self,
        config: ChatConfig,
        registry: ToolRegistry,
        *,
        system_preamble: str = "",
        max_steps: int = 25,
        max_bad_parses: int = 3,
        use_native_tools: bool | None = None,
        on_event: EventSink | None = None,
        approve: "ApproveHook | None" = None,
        auto_approve: bool = False,
        max_context_tokens: int = 6000,
        keep_recent_steps: int = 3,
        summarizer_config: ChatConfig | None = None,
        before_tool: "BeforeToolHook | None" = None,
        after_tool: "AfterToolHook | None" = None,
        stream: bool = False,
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
        # Optional small/fast model used to summarize the folded portion during
        # compaction. When None (or the call fails), compaction falls back to a
        # cheap first-line recap so the loop stays robust offline.
        self.summarizer_config = summarizer_config
        # approve(tool_name, args) -> bool. Called before any tool whose
        # requires_approval flag is set. auto_approve (yolo) skips the prompt but
        # NOT the damage guards inside the tools themselves (e.g. bash footgun block).
        self.approve = approve
        self.auto_approve = auto_approve
        # before_tool(tool_name, args) is called just before a mutating tool runs
        # (after approval), so a checkpointer can snapshot files for undo (F8).
        # A hook error is logged as an observation but never blocks the tool.
        self.before_tool = before_tool
        # after_tool(tool_name, args, observation) runs just AFTER a mutating tool
        # and may APPEND to the observation (e.g. a verification failure to repair,
        # H1/ADR-0012). A hook error is swallowed so a verifier can't break a run.
        self.after_tool = after_tool
        # stream (W1/W2, ADR-0019): when True, the assistant turn is streamed via
        # stream_message on BOTH the prompt-based and native tool-calling paths.
        # Each content delta is emitted as a "token" event for a live view; the
        # FULL message (incl. any tool_calls) is accumulated and returned, so tool
        # dispatch is byte-identical to the non-streaming path (the tool call is
        # never parsed partially). Any streaming error falls back to the plain call.
        self.stream = stream

    # --- helpers -----------------------------------------------------------
    def _emit(self, event: AgentEvent, sink_events: list[AgentEvent]) -> None:
        sink_events.append(event)
        if self.on_event is not None:
            self.on_event(event)

    def _next_message(
        self,
        messages: list[dict[str, Any]],
        native_tools: "list[dict] | None",
        step: int,
        events: list[AgentEvent],
    ) -> dict[str, Any]:
        """Get the next assistant message, streaming its content when enabled.

        W1 streamed the prompt-based path; W2 (ADR-0019) streams BOTH paths via
        `stream_message`, which passes each content delta to `on_delta` (emitted as
        a "token" event) while accumulating the FULL message and returning it — so
        `message["tool_calls"]` is available whole for native tool dispatch. The
        tool call itself is never parsed partially: we stream the content prefix
        for a live view, then dispatch from the completed message exactly as the
        non-streaming path does.

        When streaming is off, or on any streaming error, fall back to the
        non-streaming `call_model_message` so a flaky stream never fails a run
        (degrade gracefully, ADR-0001 invariant 4).
        """
        if not self.stream:
            return call_model_message(self.config, messages, tools=native_tools)
        try:
            def on_delta(text: str) -> None:
                if text:
                    self._emit(AgentEvent("token", text=text, step=step), events)
            return stream_message(self.config, messages, tools=native_tools,
                                  on_delta=on_delta)
        except LocalLLMError:
            return call_model_message(self.config, messages, tools=native_tools)

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

        # Summarize the folded portion. Prefer an LLM recap (a small summary model)
        # for real situational awareness; fall back to a cheap first-line recap when
        # no summarizer is configured or the call fails (keeps the loop offline-robust).
        recap = self._summarize_old(old) or self._heuristic_recap(old)
        summary_turn = {
            "role": "user",
            "content": (
                "[Earlier progress compacted to save context — do not repeat work "
                "already done below:]\n" + recap
            ),
        }
        return head + [summary_turn] + recent

    @staticmethod
    def _heuristic_recap(old: list[dict[str, Any]]) -> str:
        """Cheap fallback recap: one line per folded turn (no model call)."""
        lines: list[str] = []
        for msg in old:
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            # Observation turns are prefixed "Result of <tool>:"; keep a one-line gist.
            lines.append(content.splitlines()[0][:200])
        return "\n".join(lines[-40:]) or "(earlier steps)"

    def _summarize_old(self, old: list[dict[str, Any]]) -> str | None:
        """LLM-summarize the folded turns into a terse progress recap.

        Returns None (so the caller uses the heuristic) when no summarizer is
        configured or the model call fails — compaction must never crash the loop.
        """
        if self.summarizer_config is None:
            return None
        transcript = "\n".join(
            f"{m.get('role', '?')}: {(m.get('content') or '').strip()}"
            for m in old
            if (m.get("content") or "").strip()
        )
        if not transcript:
            return None
        prompt = [
            {
                "role": "system",
                "content": (
                    "You compress an AI coding agent's earlier steps into a terse "
                    "progress recap. Preserve concrete facts the agent needs to avoid "
                    "repeating work: files read, findings, edits made, commands run and "
                    "their outcomes. Use short bullet lines. No preamble, no advice."
                ),
            },
            {"role": "user", "content": f"Summarize these steps:\n\n{transcript}"},
        ]
        try:
            recap = call_model(self.summarizer_config, prompt).strip()
        except Exception:  # noqa: BLE001 - any failure (incl. LocalLLMError) -> heuristic fallback
            return None
        return recap or None

    # --- main loop ---------------------------------------------------------
    def run(
        self, goal: str, history: list[dict[str, Any]] | None = None,
        *, should_stop: "Callable[[], bool] | None" = None,
    ) -> AgentResult:
        """Drive `goal` to a final answer or the step cap.

        When `history` is given (a prior run's `AgentResult.messages`), the goal
        continues that transcript so the agent retains earlier context — this is
        how the REPL is multi-turn. When it's None the run starts fresh with a
        system+goal transcript, preserving the original single-shot behavior. The
        returned AgentResult.messages is the transcript to thread into the next turn.

        `should_stop` (V5, ADR-0017) is an optional predicate checked between steps;
        when it returns True the loop stops cooperatively with stopped_reason
        "interrupted" (used by the TUI's ctrl-c to cancel without killing the
        thread). Default None = never interrupted — behavior is exactly as before.
        """
        events: list[AgentEvent] = []
        if history:
            # Continue an existing conversation: reuse system+prior turns, append
            # the new goal as the next user turn.
            messages: list[dict[str, Any]] = list(history)
            messages.append({"role": "user", "content": goal})
        else:
            messages = [
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
            # V5 (ADR-0017): cooperative cancel — stop cleanly between steps rather
            # than killing the worker thread, so the transcript stays consistent.
            if should_stop is not None and should_stop():
                self._emit(AgentEvent("interrupted", text="stopped by user", step=step - 1), events)
                return AgentResult("", step - 1, "interrupted", events, messages)
            before = len(messages)
            messages = self._compact_messages(messages)
            folded = len(messages) < before
            if folded:
                self._emit(
                    AgentEvent("compact", text=f"compacted {before - len(messages)} old turns", step=step),
                    events,
                )
            # V1 (ADR-0017): emit a context snapshot every step so a UI can show a
            # live usage gauge. `folded` marks the step a compaction actually ran.
            self._emit(
                AgentEvent(
                    "context",
                    step=step,
                    context=ContextInfo(
                        used_tokens=self._total_tokens(messages),
                        max_tokens=self.max_context_tokens,
                        folded=folded,
                    ),
                ),
                events,
            )
            try:
                message = self._next_message(messages, native_tools, step, events)
            except LocalLLMError as exc:
                self._emit(AgentEvent("error", text=str(exc), step=step), events)
                return AgentResult("", step - 1, "error", events, messages)

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
                # Record the answer so a follow-up turn (REPL) sees it in context.
                messages.append({"role": "assistant", "content": action.text})
                return AgentResult(action.text, step, "final", events, messages)

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

            # Snapshot for undo before a mutating tool changes anything (F8).
            if self.before_tool is not None and tool is not None and tool.mutating:
                try:
                    self.before_tool(action.tool, action.args)
                except Exception:  # noqa: BLE001 - a checkpoint failure never blocks the tool
                    pass

            try:
                observation = self.registry.dispatch(action.tool, action.args)
            except ToolError as exc:
                observation = f"ERROR: {exc}"

            # Verify the result of a mutating tool and append any feedback (H1).
            # A returned string (e.g. "VERIFICATION FAILED …") is appended so the
            # model's next turn repairs the edit with the exact error in hand.
            if self.after_tool is not None and tool is not None and tool.mutating:
                try:
                    extra = self.after_tool(action.tool, action.args, observation)
                except Exception:  # noqa: BLE001 - a verifier error never blocks the loop
                    extra = None
                if extra:
                    observation = f"{observation}\n\n{extra}"

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
        return AgentResult("", self.max_steps, "max_steps", events, messages)


def _has_valid_json_action(content: str) -> bool:
    """True if parse_action would have returned a ToolCall from this text alone
    (used to distinguish 'genuinely final' from 'tried to act and failed')."""
    return isinstance(parse_action(content, None), ToolCall)
