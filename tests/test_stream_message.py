"""W2 (ADR-0019): stream_message — streams content deltas via a callback while
accumulating the FULL message (incl. native tool_calls) for whole-call dispatch.

Model-free: mocks urllib.request.urlopen with fake Ollama NDJSON / OpenAI SSE
byte streams, so no server or model is touched.
"""
from __future__ import annotations

import io
import json
from contextlib import contextmanager
from unittest import mock

from nerva_core.local_llm_writer import ChatConfig, stream_message, LocalLLMError


def _cfg(backend="ollama") -> ChatConfig:
    return ChatConfig(
        backend=backend, base_url="http://x", model="m",
        temperature=0.2, top_p=0.9, repeat_penalty=1.05,
        min_tokens=1, max_tokens=64, context_messages=8, system_prompt="",
    )


@contextmanager
def _fake_stream(lines: "list[bytes]"):
    """Patch urlopen to yield `lines` (each a bytes chunk, as urllib iterates)."""
    class _Resp:
        def __enter__(self): return iter(lines)
        def __exit__(self, *a): return False
    with mock.patch("urllib.request.urlopen", return_value=_Resp()):
        yield


def test_ollama_streams_content_deltas_and_calls_on_delta():
    lines = [
        json.dumps({"message": {"content": "Hello, "}}).encode() + b"\n",
        json.dumps({"message": {"content": "world."}}).encode() + b"\n",
        json.dumps({"message": {"content": ""}, "done": True}).encode() + b"\n",
    ]
    seen: list[str] = []
    with _fake_stream(lines):
        msg = stream_message(_cfg(), [{"role": "user", "content": "hi"}],
                             on_delta=seen.append)
    assert seen == ["Hello, ", "world."]
    assert msg["content"] == "Hello, world."
    assert "tool_calls" not in msg


def test_ollama_surfaces_whole_tool_calls_in_final_chunk():
    tc = [{"function": {"name": "read_file", "arguments": {"path": "a.py"}}}]
    lines = [
        json.dumps({"message": {"content": "Let me look. "}}).encode() + b"\n",
        json.dumps({"message": {"content": "", "tool_calls": tc}, "done": True}).encode() + b"\n",
    ]
    seen: list[str] = []
    with _fake_stream(lines):
        msg = stream_message(_cfg(), [{"role": "user", "content": "read a"}],
                             tools=[{"type": "function"}], on_delta=seen.append)
    assert seen == ["Let me look. "]          # content streamed
    assert msg["content"] == "Let me look. "
    assert msg["tool_calls"] == tc            # tool call arrived WHOLE, not partial


def test_openai_sse_content_stream():
    def sse(obj):
        return b"data: " + json.dumps(obj).encode() + b"\n"
    lines = [
        sse({"choices": [{"delta": {"content": "a"}}]}),
        sse({"choices": [{"delta": {"content": "b"}}]}),
        b"data: [DONE]\n",
    ]
    seen: list[str] = []
    with _fake_stream(lines):
        msg = stream_message(_cfg("openai"), [{"role": "user", "content": "x"}],
                             on_delta=seen.append)
    assert seen == ["a", "b"]
    assert msg["content"] == "ab"


def test_on_delta_optional_still_accumulates():
    lines = [json.dumps({"message": {"content": "solo"}, "done": True}).encode() + b"\n"]
    with _fake_stream(lines):
        msg = stream_message(_cfg(), [{"role": "user", "content": "x"}])  # no on_delta
    assert msg["content"] == "solo"


def test_transport_error_raises_local_llm_error():
    import urllib.error
    with mock.patch("urllib.request.urlopen",
                    side_effect=urllib.error.URLError("boom")):
        try:
            stream_message(_cfg(), [{"role": "user", "content": "x"}])
        except LocalLLMError:
            pass
        else:  # pragma: no cover
            raise AssertionError("expected LocalLLMError")
