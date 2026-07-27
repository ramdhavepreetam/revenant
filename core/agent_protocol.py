"""The tool-call protocol for the Revenant agent harness.

Two wire formats, auto-selected per model:

1. **Native** -- tool-capable models (qwen2.5) return
   `message.tool_calls = [{"function": {"name", "arguments"}}]`. `arguments` is a
   dict (Ollama) or a JSON string (OpenAI-compatible). We read it directly.

2. **Prompt-based (universal fallback)** -- any model, including ones with no tool
   template (Stheno). The system prompt instructs the model to emit ONE action as
   a fenced block:

       ```action
       {"tool": "read_file", "args": {"path": "core/agent_loop.py"}}
       ```

   When the model is done, it replies normally with no action block.

`parse_action(text, raw_message)` returns a `ToolCall` or a `FinalAnswer`.
Precedence: native tool_calls -> fenced ```action block -> lenient JSON scan ->
treat the whole reply as the final answer. Weak 8B models are sloppy, so the
prompt-based path tolerates missing fences, trailing prose, and single quotes.

Pure Python, no LLM dependency.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from agent_tools import ToolRegistry


@dataclass
class ToolCall:
    """A parsed request to run one tool."""

    tool: str
    args: dict[str, Any]


@dataclass
class FinalAnswer:
    """The model produced a normal reply with no action -> the loop ends."""

    text: str


Action = ToolCall | FinalAnswer


# --- System-prompt rendering ----------------------------------------------

_PROTOCOL_INSTRUCTIONS = """\
You can take actions by calling tools. To call a tool, emit EXACTLY ONE action \
as a fenced code block and nothing else:

```action
{{"tool": "<tool_name>", "args": {{ ... }}}}
```

Rules:
- One action per reply. After you emit an action, stop and wait for the result.
- Use ONLY the tools listed below, with the exact argument names shown.
- The `args` object must be valid JSON (double-quoted keys and string values).
- When the task is complete, reply normally with your final answer and NO action \
block.

Available tools:
{tool_docs}"""


def render_system_block(registry: ToolRegistry) -> str:
    """Render the tool catalogue + protocol instructions for the system prompt
    (prompt-based path). Native-tool models don't strictly need this, but it does
    no harm and keeps behavior consistent across models."""
    return _PROTOCOL_INSTRUCTIONS.format(tool_docs=registry.render_docs())


# --- Parsing ---------------------------------------------------------------

# A ```action ... ``` fenced block (the language tag is optional/loose).
_FENCE_RE = re.compile(
    r"```(?:action|json)?\s*(?P<body>\{.*?\})\s*```",
    re.DOTALL | re.IGNORECASE,
)


def _coerce_args(raw: Any) -> dict[str, Any]:
    """Native arguments may be a dict (Ollama) or a JSON string (OpenAI-compat)."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _from_native(raw_message: dict[str, Any]) -> ToolCall | None:
    """Read a native tool_calls block if present. Returns the FIRST call (the loop
    is one-action-per-step by design)."""
    if not isinstance(raw_message, dict):
        return None
    calls = raw_message.get("tool_calls")
    if not calls:
        return None
    fn = (calls[0] or {}).get("function") or {}
    name = fn.get("name")
    if not name:
        return None
    args = _coerce_args(fn.get("arguments"))
    # Reuse the same double-wrap unwrap as the text path.
    unwrapped = _tool_call_from_obj({"tool": name, "args": args})
    return unwrapped or ToolCall(tool=str(name), args=args)


def _is_tool_object(obj: dict[str, Any]) -> bool:
    """Does a bare JSON object look like a tool call rather than data?

    `{"tool": ...}` always qualifies. `{"name": ...}` qualifies only when paired
    with `args`/`arguments`, so ordinary data objects that merely have a "name"
    field (e.g. `{"name": "Alice", "age": 30}`) are left as final-answer text.
    """
    if "tool" in obj:
        return True
    return "name" in obj and ("args" in obj or "arguments" in obj)


def _lenient_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort extraction of a {"tool":..., "args":...} object from loose text.

    Handles the common weak-model deviations: no fence, trailing prose after the
    JSON, single quotes instead of double. Returns None if nothing usable.
    """
    # Find the first balanced-looking {...} that mentions "tool".
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    obj = _try_load(candidate)
                    if isinstance(obj, dict) and _is_tool_object(obj):
                        return obj
                    break  # this block didn't parse/qualify; look for the next {
        start = text.find("{", start + 1)
    return None


def _try_load(candidate: str) -> Any:
    """Parse a JSON-ish object from a weak model, tolerating common deviations.

    Strategy order:
      1. Strict json.loads (the happy path).
      2. Naive single->double quote swap for the all-single-quotes case.
      3. ast.literal_eval, which handles Python-dict-style output: single-quoted
         string values that themselves contain double quotes (e.g.
         {"old": ' "hi "'}) and Python literals (True/False/None). It only
         evaluates literals -- no code execution, no names -- so it's safe on
         untrusted model output.
    """
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # Swap single quotes only if there are no double quotes to corrupt.
    if "'" in candidate and '"' not in candidate:
        try:
            return json.loads(candidate.replace("'", '"'))
        except json.JSONDecodeError:
            pass
    # Python-literal fallback (mixed quotes, single-quoted values, True/None...).
    try:
        import ast

        value = ast.literal_eval(candidate)
        return value if isinstance(value, dict) else None
    except (ValueError, SyntaxError):
        return None


def _tool_call_from_obj(obj: dict[str, Any]) -> ToolCall | None:
    name = obj.get("tool") or obj.get("name")
    if not name:
        return None
    args = obj.get("args")
    if args is None:
        args = obj.get("arguments")
    if not isinstance(args, dict):
        args = {}
    # Some weak models double-wrap: they emit the tool-call object as the *args*
    # of another call, e.g. {"tool":"grep","args":{"tool":"grep","args":{...}}}.
    # Unwrap one level when the inner object is itself a well-formed call for the
    # same tool.
    inner = args.get("args") if isinstance(args.get("args"), dict) else None
    if inner is not None and args.get("tool") in (name, None):
        args = inner
    return ToolCall(tool=str(name), args=args)


def parse_action(text: str, raw_message: dict[str, Any] | None = None) -> Action:
    """Parse a model reply into a ToolCall or a FinalAnswer.

    Precedence:
      1. Native `tool_calls` on raw_message (tool-capable models).
      2. A fenced ```action {json}``` block in the text.
      3. A lenient scan for a {"tool":..., "args":...} object (sloppy output).
      4. Otherwise the whole reply is the final answer.
    """
    # 1. Native tool call.
    native = _from_native(raw_message or {})
    if native is not None:
        return native

    text = text or ""

    # 2. Fenced action block.
    match = _FENCE_RE.search(text)
    if match:
        obj = _try_load(match.group("body"))
        if isinstance(obj, dict):
            call = _tool_call_from_obj(obj)
            if call is not None:
                return call

    # 3. Lenient bare-object scan (no fence / trailing prose / single quotes).
    obj = _lenient_json_object(text)
    if obj is not None:
        call = _tool_call_from_obj(obj)
        if call is not None:
            return call

    # 4. No action -> final answer.
    return FinalAnswer(text=text.strip())


# One-line nudge the loop can re-send when a reply LOOKED like an attempted action
# but didn't parse (e.g. it mentioned a tool name but emitted no valid JSON). The
# loop applies this at most once per step before falling back to FinalAnswer.
MALFORMED_ACTION_NUDGE = (
    "Your action was not valid. If you want to call a tool, resend ONLY a fenced "
    "```action block containing a JSON object with \"tool\" and \"args\" keys. "
    "Otherwise, give your final answer with no action block."
)


def looks_like_attempted_action(text: str, registry: ToolRegistry) -> bool:
    """Heuristic: did the model seem to *try* to call a tool but fail to produce a
    parseable action? Used by the loop to decide whether the one-shot nudge is
    worth it vs. just accepting the text as final. True when the reply names a known
    tool or uses action/tool-call vocabulary but parse_action fell through to text.
    """
    lowered = (text or "").lower()
    if "```action" in lowered or '"tool"' in lowered or "'tool'" in lowered:
        return True
    return any(name.lower() in lowered for name in registry.names())
