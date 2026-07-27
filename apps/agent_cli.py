#!/usr/bin/env python3
"""revenant -- the local coding agent CLI (P2).

A Claude-Code-style agent powered entirely by your local Ollama model. It reads
and searches your codebase and answers a goal by running a real tool-calling loop
(read_file / list_dir / glob / grep). Mutating tools (write/edit/bash) arrive in P3.

Usage:
    revenant "summarize what core/ does"
    revenant --workspace ~/proj --model qwen2.5:7b "where is auth handled?"

Offline: talks only to the local model server (Ollama by default). No cloud.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "core", _ROOT / "tts", _ROOT / "apps"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from aibot_profiles import load_profiles
from aibot_storage import default_data_dir
from agent_router import config_for_role
from agent_tools import ToolRegistry
from agent_fs_tools import build_fs_tools
from agent_edit_tools import build_edit_tools
from agent_bash_tool import build_bash_tool
from agent_loop import AgentLoop, AgentEvent
from agent_capacity import recommend
from local_llm_writer import ChatConfig

CODING_PREAMBLE = (
    "You are Revenant, a precise local coding assistant working inside a code "
    "repository. Investigate before answering — never guess a file's purpose from "
    "its name. To understand what a file does, use read_file to read it (start with "
    "its top docstring and key definitions), not grep for a comment. Use glob/grep to "
    "locate things, then read_file to confirm. Take ONE action at a time. Base your "
    "final answer only on what the tools actually returned; if you did not read "
    "something, do not assert what it contains. Take ONE action at a time. "
    "You can also make changes: write_file, edit_file, and run_bash. When editing an "
    "existing file, prefer edit_file with enough surrounding context that `old` "
    "matches exactly once; read the file first. Making a change requires the user's "
    "approval, so keep changes minimal and to what was asked. When you have enough "
    "information, give a clear, concrete final answer."
)

# ANSI colors (skipped when not a TTY).
_C = {
    "dim": "\033[2m", "cyan": "\033[36m", "green": "\033[32m",
    "yellow": "\033[33m", "red": "\033[31m", "bold": "\033[1m", "reset": "\033[0m",
}


def _color(enabled: bool):
    return _C if enabled else {k: "" for k in _C}


def make_printer(color: dict):
    def on_event(ev: AgentEvent) -> None:
        c = color
        if ev.kind == "assistant" and ev.text:
            print(f"{c['dim']}{ev.text}{c['reset']}")
        elif ev.kind == "action":
            args = ", ".join(f"{k}={v!r}" for k, v in ev.args.items())
            print(f"{c['cyan']}→ {ev.tool}({args}){c['reset']}")
        elif ev.kind == "observation":
            body = ev.text if len(ev.text) <= 800 else ev.text[:800] + " …"
            indented = "\n".join("  " + line for line in body.splitlines())
            print(f"{c['dim']}{indented}{c['reset']}")
        elif ev.kind == "final":
            print(f"\n{c['green']}{c['bold']}{ev.text}{c['reset']}")
        elif ev.kind == "error":
            print(f"{c['red']}error: {ev.text}{c['reset']}", file=sys.stderr)
        elif ev.kind == "limit":
            print(f"{c['yellow']}[{ev.text}]{c['reset']}", file=sys.stderr)
        elif ev.kind == "compact":
            print(f"{c['dim']}[context: {ev.text}]{c['reset']}", file=sys.stderr)
        # "approval" events are handled by the approver prompt, not printed here.
    return on_event


def _preview_args(tool: str, args: dict) -> str:
    """A readable, truncated preview of what a mutating call will do."""
    if tool == "write_file":
        content = str(args.get("content", ""))
        head = content if len(content) <= 400 else content[:400] + " …"
        return f"path={args.get('path')!r}\n--- content ---\n{head}"
    if tool == "edit_file":
        old = str(args.get("old", "")); new = str(args.get("new", ""))
        clip = lambda s: s if len(s) <= 300 else s[:300] + " …"
        return f"path={args.get('path')!r}\n--- old ---\n{clip(old)}\n--- new ---\n{clip(new)}"
    if tool == "run_bash":
        return f"$ {args.get('command')}"
    return ", ".join(f"{k}={v!r}" for k, v in args.items())


def make_approver(color: dict):
    """Interactive y/N approval for mutating tools. Deny on anything but yes."""
    c = color

    def approve(tool: str, args: dict) -> bool:
        print(f"\n{c['yellow']}{c['bold']}APPROVAL NEEDED: {tool}{c['reset']}")
        print(f"{c['yellow']}{_preview_args(tool, args)}{c['reset']}")
        try:
            answer = input(f"{c['bold']}Run this? [y/N] {c['reset']}").strip().lower()
        except EOFError:
            answer = ""
        ok = answer in ("y", "yes")
        print(f"{c['dim']}{'approved' if ok else 'declined'}{c['reset']}")
        return ok

    return approve


def build_config(profiles: dict, base_url: str, model: str | None) -> ChatConfig:
    """Resolve the 'code' role via the router; fall back to a sane default config
    if model_roles isn't configured. An explicit --model overrides the role model."""
    base = ChatConfig(
        backend="ollama", base_url=base_url, model="qwen2.5:7b",
        temperature=0.2, top_p=0.9, repeat_penalty=1.05,
        min_tokens=64, max_tokens=1024, context_messages=24, system_prompt="",
    )
    routed = config_for_role("code", base_url, profiles, base=base)
    config = routed or base
    if model:
        config.model = model
    return config


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="revenant", description="Local coding agent (offline, Ollama-backed)."
    )
    parser.add_argument("goal", help="What you want the agent to do.")
    parser.add_argument("--workspace", default=".", help="Repo root the agent may read (default: cwd).")
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--model", default="", help="Override the coding model (default: 'code' role).")
    parser.add_argument("--max-steps", type=int, default=0,
                        help="Step cap (0 = auto from detected hardware).")
    parser.add_argument("--max-context-tokens", type=int, default=0,
                        help="Compact once the transcript exceeds this budget (0 = auto from hardware).")
    parser.add_argument("--no-native-tools", action="store_true",
                        help="Force the prompt-based protocol even on tool-capable models.")
    parser.add_argument("--read-only", action="store_true",
                        help="Disable mutating tools (write/edit/bash); investigate only.")
    parser.add_argument("--yolo", action="store_true",
                        help="Auto-approve mutating tools (skips the y/N prompt; footgun guards still apply).")
    parser.add_argument("--no-color", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"error: workspace is not a directory: {workspace}", file=sys.stderr)
        return 2

    color = _color(sys.stdout.isatty() and not args.no_color)
    profiles = load_profiles(default_data_dir() / "profiles.json")
    config = build_config(profiles, args.base_url, args.model or None)

    # Hardware-aware defaults (overridden by any explicit flag).
    rec = recommend(config.model, base_url=config.base_url)
    max_steps = args.max_steps or rec.max_steps
    max_context = args.max_context_tokens or rec.max_context_tokens

    tools = build_fs_tools(workspace)
    if not args.read_only:
        tools += build_edit_tools(workspace)
        tools.append(build_bash_tool(workspace))
    registry = ToolRegistry(tools)

    mode = "read-only" if args.read_only else ("yolo" if args.yolo else "approval-gated")
    print(f"{color['dim']}revenant · model={config.model} · workspace={workspace} · {mode}{color['reset']}")
    print(f"{color['dim']}capacity: {rec.note}{color['reset']}")

    loop = AgentLoop(
        config, registry,
        system_preamble=CODING_PREAMBLE,
        max_steps=max_steps,
        # None -> auto-detect native tool support per model; --no-native-tools forces off.
        use_native_tools=False if args.no_native_tools else None,
        on_event=make_printer(color),
        approve=make_approver(color),
        auto_approve=args.yolo,
        max_context_tokens=max_context,
    )
    result = loop.run(args.goal)
    if result.stopped_reason == "final":
        return 0
    if result.stopped_reason == "max_steps":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
