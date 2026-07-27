"""Native tool-calling detection for the Revenant harness (P6).

Some local models ship a tool template and return structured `message.tool_calls`
(qwen2.5); others (Stheno, the old gemma) have no template and return empty content
when a `tools` array is passed. The harness works on both via the dual protocol, but
it should PREFER the native path when it's available and reliable.

`supports_native_tools(config)` probes the model ONCE with a trivial tool request
and caches the answer per (base_url, model), so the agent loop can auto-resolve
`use_native_tools` instead of relying on a hard-coded flag.
"""
from __future__ import annotations

from nerva_core.local_llm_writer import ChatConfig, call_model_message, LocalLLMError

# (base_url, model) -> bool. Process-lifetime cache; probing is a network call.
_CACHE: dict[tuple[str, str], bool] = {}

_PROBE_TOOL = [{
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Return pong.",
        "parameters": {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
    },
}]


def supports_native_tools(config: ChatConfig, *, force: bool = False) -> bool:
    """Does this model reliably emit native tool_calls? Probed once, then cached.

    Returns False on any error (unreachable server, unexpected shape) — the caller
    then falls back to the prompt-based protocol, which works everywhere.
    """
    key = (config.base_url, config.model)
    if not force and key in _CACHE:
        return _CACHE[key]

    # A tiny, cheap request that a tool-capable model answers with a tool_call.
    probe = ChatConfig(
        backend=config.backend, base_url=config.base_url, model=config.model,
        temperature=0.0, top_p=0.9, repeat_penalty=1.0,
        min_tokens=1, max_tokens=32, context_messages=1, system_prompt="",
    )
    messages = [{"role": "user", "content": "Call the ping tool with value 'x'."}]
    result = False
    try:
        message = call_model_message(probe, messages, tools=_PROBE_TOOL)
        calls = message.get("tool_calls") if isinstance(message, dict) else None
        result = bool(calls) and bool((calls[0] or {}).get("function", {}).get("name"))
    except (LocalLLMError, Exception):  # noqa: BLE001 - unknown -> assume no native
        result = False

    _CACHE[key] = result
    return result


def clear_cache() -> None:
    """Forget cached probe results (e.g. after pulling/replacing a model)."""
    _CACHE.clear()
