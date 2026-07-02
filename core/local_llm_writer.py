#!/usr/bin/env python3
"""
Local LLM writing/chat CLI tuned for 8B/14B quantized models.

Supported backends:
- Ollama: http://localhost:11434
- OpenAI-compatible local servers: llama.cpp, LM Studio, vLLM, etc.
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from aibot_memory import NervaPackMemory, format_memory_context
from aibot_profiles import apply_profile, load_profiles
from aibot_storage import ConversationStore, default_data_dir


DEFAULT_SYSTEM_PROMPT = """\
You are a long-form interactive storytelling assistant.
Write immersive, sensory-rich prose with strong emotional continuity.
Keep replies coherent for local 8B/14B quantized models by targeting 400-800 tokens.
Do not rush scenes. Preserve character names, motivations, relationships, and prior events.
Ask at most one concise clarifying question only when the user's request is impossible to continue without it.
"""


@dataclass
class ChatConfig:
    backend: str
    base_url: str
    model: str
    temperature: float
    top_p: float
    repeat_penalty: float
    min_tokens: int
    max_tokens: int
    context_messages: int
    system_prompt: str


class LocalLLMError(RuntimeError):
    pass


def post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LocalLLMError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LocalLLMError(f"Could not connect to {url}: {exc.reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise LocalLLMError(f"Invalid JSON response from {url}: {body[:500]}") from exc


def trim_messages(messages: list[dict[str, str]], keep_last: int) -> list[dict[str, str]]:
    system = [message for message in messages if message["role"] == "system"][:1]
    rest = [message for message in messages if message["role"] != "system"]
    return system + rest[-keep_last:]


def call_ollama(config: ChatConfig, messages: list[dict[str, str]]) -> str:
    payload = {
        "model": config.model,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": config.temperature,
            "top_p": config.top_p,
            "repeat_penalty": config.repeat_penalty,
            "num_predict": config.max_tokens,
        },
    }
    result = post_json(f"{config.base_url.rstrip('/')}/api/chat", payload, timeout=300)
    try:
        return result["message"]["content"].strip()
    except KeyError as exc:
        raise LocalLLMError(f"Unexpected Ollama response: {result}") from exc


def call_openai_compatible(config: ChatConfig, messages: list[dict[str, str]]) -> str:
    payload = {
        "model": config.model,
        "messages": messages,
        "temperature": config.temperature,
        "top_p": config.top_p,
        "max_tokens": config.max_tokens,
        "stream": False,
    }
    result = post_json(f"{config.base_url.rstrip('/')}/v1/chat/completions", payload, timeout=300)
    try:
        return result["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as exc:
        raise LocalLLMError(f"Unexpected OpenAI-compatible response: {result}") from exc


def call_model(config: ChatConfig, messages: list[dict[str, str]]) -> str:
    if config.backend == "ollama":
        return call_ollama(config, messages)
    if config.backend == "openai":
        return call_openai_compatible(config, messages)
    raise LocalLLMError(f"Unknown backend: {config.backend}")


def stream_model(config: ChatConfig, messages: list[dict[str, str]]):
    """Yield incremental text deltas as the model generates (Ollama + OpenAI-compat).

    Caller is responsible for buffering the full text and applying the same
    post-processing (trim_to_last_sentence, save, learn, summarize) as call_model.
    """
    if config.backend == "ollama":
        url = f"{config.base_url.rstrip('/')}/api/chat"
        payload = {
            "model": config.model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": config.temperature,
                "top_p": config.top_p,
                "repeat_penalty": config.repeat_penalty,
                "num_predict": config.max_tokens,
            },
        }
        key_path = ("message", "content")
    elif config.backend == "openai":
        url = f"{config.base_url.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": config.model,
            "messages": messages,
            "temperature": config.temperature,
            "top_p": config.top_p,
            "max_tokens": config.max_tokens,
            "stream": True,
        }
        key_path = None  # SSE "data: {...}" with choices[0].delta.content
    else:
        raise LocalLLMError(f"Unknown backend: {config.backend}")

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            for raw in response:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                if config.backend == "openai":
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line == "[DONE]":
                        break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if config.backend == "ollama":
                    delta = (obj.get("message") or {}).get("content", "")
                    if delta:
                        yield delta
                    if obj.get("done"):
                        break
                else:
                    choices = obj.get("choices") or []
                    if choices:
                        delta = (choices[0].get("delta") or {}).get("content", "")
                        if delta:
                            yield delta
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise LocalLLMError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise LocalLLMError(f"Could not connect to {url}: {exc.reason}") from exc


def estimate_tokens(text: str) -> int:
    # Cheap approximation that is good enough for local CLI guidance.
    return max(1, round(len(text.split()) * 1.33))


def trim_to_last_sentence(text: str) -> str:
    """Drop a trailing partial sentence so replies never end mid-thought.

    When generation stops because it hit max_tokens (not a natural end), the last
    sentence is usually cut off. If the text doesn't already end on terminal
    punctuation (optionally followed by a closing quote/asterisk), trim back to the
    last complete sentence. Only trims if a complete sentence remains, so short
    one-liners and deliberate cliffhangers ("...") are left intact.
    """
    import re

    stripped = text.rstrip()
    if not stripped:
        return text
    # Already ends cleanly (., !, ?, … possibly + closing " ' ) * ).
    if re.search(r"[.!?…][\"'’)\*]*$", stripped):
        return stripped
    # Find the last sentence-ending punctuation and cut just after it.
    matches = list(re.finditer(r"[.!?…][\"'’)\*]*", stripped))
    if not matches:
        return stripped  # no complete sentence to fall back to — leave as-is
    end = matches[-1].end()
    trimmed = stripped[:end].rstrip()
    return trimmed or stripped


def build_length_hint(config: ChatConfig, user_text: str) -> str:
    return (
        f"{user_text.strip()}\n\n"
        f"[Generation target: write approximately {config.min_tokens}-{config.max_tokens} tokens. "
        "Prioritize continuity, concrete sensory detail, and clean scene progression.]"
    )


def build_user_prompt(config: ChatConfig, user_text: str, memories: list[str]) -> str:
    memory_context = format_memory_context(memories)
    if memory_context:
        user_text = f"{memory_context}\n\nCurrent user request:\n{user_text}"
    return build_length_hint(config, user_text)


def print_wrapped(prefix: str, text: str) -> None:
    print(prefix)
    print(textwrap.fill(text, width=96, replace_whitespace=False))
    print()


def iter_stdin() -> Iterable[str]:
    while True:
        try:
            text = input("you> ").strip()
        except EOFError:
            print()
            return
        if text:
            yield text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chat with a local LLM using reply settings tuned for 8B/14B quantized models."
    )
    parser.add_argument("--backend", choices=["ollama", "openai"], default="ollama")
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--model", default="llama3.1:8b")
    parser.add_argument("--temperature", type=float, default=0.85)
    parser.add_argument("--top-p", type=float, default=0.9)
    parser.add_argument("--repeat-penalty", type=float, default=1.08)
    parser.add_argument("--min-tokens", type=int, default=400)
    parser.add_argument("--max-tokens", type=int, default=800)
    parser.add_argument("--context-messages", type=int, default=18)
    parser.add_argument("--system-prompt-file")
    parser.add_argument("--data-dir", default=str(default_data_dir()))
    parser.add_argument("--profiles-file", default=str(default_data_dir() / "profiles.json"))
    parser.add_argument("--model-profile", default="")
    parser.add_argument("--style-profile", default="")
    parser.add_argument("--generation-preset", default="")
    parser.add_argument("--conversation-id", default="")
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument("--list-conversations", action="store_true")
    parser.add_argument("--export-conversation", default="")
    parser.add_argument("--export-format", choices=["md", "json", "txt"], default="md")
    parser.add_argument("--export-path", default="")
    return parser.parse_args()


def load_system_prompt(path: str | None) -> str:
    if not path:
        return DEFAULT_SYSTEM_PROMPT
    with open(path, "r", encoding="utf-8") as file:
        return file.read().strip()


def main() -> int:
    args = parse_args()
    store = ConversationStore(args.data_dir)

    if args.list_conversations:
        for conversation in store.list_conversations():
            print(f"{conversation.id}  {conversation.updated_at}  {conversation.title}")
        return 0

    if args.export_conversation:
        export_path = args.export_path or str(
            Path(args.data_dir) / "exports" / f"{args.export_conversation}.{args.export_format}"
        )
        output = store.export_conversation(args.export_conversation, export_path, args.export_format)
        print(f"exported {output}")
        return 0

    config = ChatConfig(
        backend=args.backend,
        base_url=args.base_url,
        model=args.model,
        temperature=args.temperature,
        top_p=args.top_p,
        repeat_penalty=args.repeat_penalty,
        min_tokens=args.min_tokens,
        max_tokens=args.max_tokens,
        context_messages=args.context_messages,
        system_prompt=load_system_prompt(args.system_prompt_file),
    )

    profiles = load_profiles(args.profiles_file)
    if args.model_profile or args.style_profile or args.generation_preset:
        config = apply_profile(
            config,
            profiles,
            model_profile=args.model_profile,
            style_profile=args.style_profile,
            preset=args.generation_preset,
        )

    if args.conversation_id:
        conversation = store.get_conversation(args.conversation_id)
        if not conversation:
            print(f"error: unknown conversation id: {args.conversation_id}", file=sys.stderr)
            return 1
    else:
        conversation = store.create_conversation(title=f"{config.model} session")

    saved_messages = store.get_messages(conversation.id)
    messages = [{"role": "system", "content": config.system_prompt}] + saved_messages
    memory = None if args.no_memory else NervaPackMemory(Path(args.data_dir) / "memory")

    print("Local LLM writer")
    print(f"backend={config.backend} model={config.model} target={config.min_tokens}-{config.max_tokens} tokens")
    print(f"conversation={conversation.id}")
    print("Commands: /quit, /reset, /system\n")

    for user_text in iter_stdin():
        if user_text in {"/quit", "/exit"}:
            return 0
        if user_text == "/reset":
            messages = [{"role": "system", "content": config.system_prompt}]
            conversation = store.create_conversation(title=f"{config.model} session")
            print(f"new conversation={conversation.id}")
            print("conversation reset\n")
            continue
        if user_text == "/system":
            print(config.system_prompt)
            print()
            continue

        memories = memory.recall(user_text) if memory else []
        messages.append({"role": "user", "content": build_user_prompt(config, user_text, memories)})
        store.add_message(conversation.id, "user", user_text)
        if memory:
            memory.remember_message(conversation.id, "user", user_text)
        active_messages = trim_messages(messages, config.context_messages)

        try:
            reply = call_model(config, active_messages)
        except LocalLLMError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        messages.append({"role": "assistant", "content": reply})
        store.add_message(conversation.id, "assistant", reply)
        if memory:
            memory.remember_message(conversation.id, "assistant", reply)
        token_count = estimate_tokens(reply)
        print_wrapped(f"assistant> ~{token_count} tokens", reply)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
