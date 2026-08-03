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
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

from nerva_core.aibot_profiles import load_profiles
from nerva_core.aibot_storage import default_data_dir
from nerva_core.local_llm_writer import ChatConfig
from nerva_agent.agent_router import config_for_role
from nerva_agent.agent_tools import ToolRegistry
from nerva_agent.agent_fs_tools import build_fs_tools
from nerva_agent.agent_edit_tools import build_edit_tools
from nerva_agent.agent_bash_tool import build_bash_tool
from nerva_agent.agent_loop import AgentLoop, AgentEvent
from nerva_agent.agent_capacity import recommend

from nerva_agent.mcp_tools import build_mcp_tools
from nerva_agent.skills import (
    discover_skills, render_skill_index, compose_skill_body, scope_registry,
)
from nerva_agent.loop_driver import (
    loop_until, Budget, model_final_predicate, command_predicate,
    file_exists_predicate,
)
from nerva_agent.code_graph.indexer import build_index, load_or_build_index
from nerva_agent.code_graph.tools import build_code_graph_tools
from nerva_agent.subagent import build_spawn_tool

from revenant_cli.config import (
    load_config, resolve, mcp_server_specs, user_config_path, verify_config,
    context_config, memory_config, write_model_choice, write_mcp_server, write_scalar,
)
from revenant_cli.config import _SCALAR_KEYS
from revenant_cli import session_store, preflight
from revenant_cli.console import make_console
from revenant_cli.git_checkpoint import GitCheckpointer, is_git_repo
from revenant_cli.verify_hook import build_verifier, make_verify_hook
from revenant_cli.context_hook import make_context_hook, compose_after_tool_hooks
from revenant_cli.project_context import compose_preamble, find_project_doc
from revenant_cli.checkpoint import Checkpointer

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


def _color_enabled(args) -> bool:
    """Colors on when stdout is a TTY, --no-color not set, and NO_COLOR unset
    (the no-color.org convention: presence of NO_COLOR disables color)."""
    return (sys.stdout.isatty()
            and not getattr(args, "no_color", False)
            and not os.environ.get("NO_COLOR"))


def _tui_enabled(args) -> bool:
    """Whether to launch the full-screen TUI for `chat` (V3, ADR-0017).

    On when: not --no-tui, textual importable, stdout is a TTY, and NO_COLOR unset.
    `--tui` still requires textual + a TTY (it can't render into a pipe); if forced
    without them we fall back to the REPL with a note. Env REVENANT_TUI=1 opts in."""
    if getattr(args, "no_tui", False):
        return False
    from revenant_cli.tui import tui_available
    tty = sys.stdout.isatty()
    want = getattr(args, "tui", False) or bool(os.environ.get("REVENANT_TUI"))
    # Default policy: auto-on when available + interactive (like rich), unless the
    # user is piping or NO_COLOR is set. An explicit --tui also honors TTY/textual.
    auto = tty and not os.environ.get("NO_COLOR")
    return tui_available() and tty and (want or auto)


def _make_approver(console):
    """A console-backed approval callback (U4). Renders via the console (rich shows
    a real diff / panels; plain reproduces the legacy prompt), blocks on confirm.
    The loop still calls this injected callback — the loop itself is unchanged."""
    def approve(tool: str, args: dict) -> bool:
        console.approval(tool, args)
        return console.confirm("Run this?")
    return approve


def _loop_console(loop):
    """The console stashed on the loop by _build_agent, or a plain fallback (so
    tests with a fake loop, and any path that didn't set one, still work)."""
    con = getattr(loop, "_console", None)
    if con is not None:
        return con
    from revenant_cli.console import PlainConsole
    return PlainConsole(color=False)


def _on_event(console):
    """Wrap the console's event renderer to make error text actionable (U1/U4).

    The Console renders raw event text; here we append the `ollama serve` /
    `ollama pull` hints to `error` events before they're shown, keeping that
    CLI-specific phrasing out of the (engine-agnostic) console module."""
    def sink(ev: AgentEvent) -> None:
        if ev.kind == "error":
            # Preserve the other fields (agent/context) while rewriting the text.
            ev = replace(ev, text=_actionable(ev.text))
        console.event(ev)
    return sink


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


_SUBCOMMANDS = ("run", "chat", "loop", "undo", "mcp", "skills", "doctor",
                "models", "config", "resume")


_DEFAULT_BASE_URL = "http://localhost:11434"


def _normalize_base_url(url: str) -> str:
    """Ollama's OLLAMA_HOST is often a bare `host:port` with no scheme; add one."""
    url = (url or "").strip()
    if url and "://" not in url:
        url = "http://" + url
    return url or _DEFAULT_BASE_URL


def _default_base_url() -> str:
    """Base URL default: OLLAMA_HOST env if set, else localhost. (Flag/config still
    win via resolve(); this only supplies the default they fall back to.)"""
    return _normalize_base_url(os.environ.get("OLLAMA_HOST") or _DEFAULT_BASE_URL)


def _actionable(text: str) -> str:
    """Append a next-step hint to a raw model error (mid-run failures; the
    pre-flight covers most cases before a run starts)."""
    low = text.lower()
    if "could not connect" in low or "connection refused" in low:
        return text + "\n  → Is Ollama running? Start it with:  ollama serve"
    if "http 404" in low or "not found" in low:
        return text + "\n  → The model may not be pulled. Run:  ollama pull <model>"
    return text


def _add_common_flags(p: argparse.ArgumentParser) -> None:
    """Flags shared by `run` and `chat` (agent configuration)."""
    p.add_argument("--workspace", default=".", help="Repo root the agent may read (default: cwd).")
    # base_url/model default to "" (a sentinel meaning "unset") so a .revenant.toml
    # value can fill in; the real default is applied in _build_agent via config.resolve.
    p.add_argument("--base-url", default="", help=f"Model server URL (default: {_DEFAULT_BASE_URL}).")
    p.add_argument("--model", default="", help="Override the coding model (default: 'code' role).")
    p.add_argument("--max-steps", type=int, default=0,
                   help="Step cap (0 = auto from detected hardware).")
    p.add_argument("--max-context-tokens", type=int, default=0,
                   help="Compact once the transcript exceeds this budget (0 = auto from hardware).")
    p.add_argument("--no-native-tools", action="store_true",
                   help="Force the prompt-based protocol even on tool-capable models.")
    p.add_argument("--stream", dest="stream", action="store_true", default=None,
                   help="Stream the assistant's reply token-by-token (prompt-based "
                        "protocol only). Default: on for interactive chat, off elsewhere.")
    p.add_argument("--no-stream", dest="stream", action="store_false",
                   help="Disable token streaming; wait for the whole reply.")
    p.add_argument("--read-only", action="store_true",
                   help="Disable mutating tools (write/edit/bash); investigate only.")
    p.add_argument("--yolo", action="store_true",
                   help="Auto-approve mutating tools (skips the y/N prompt; footgun guards still apply).")
    p.add_argument("--no-graph", action="store_true",
                   help="Skip building the code graph (defn_of/who_calls/… tools).")
    p.add_argument("--no-graph-cache", action="store_true",
                   help="Rebuild the code graph from scratch (ignore the cached "
                        ".aibot/code_graph.json; no persistence this run).")
    p.add_argument("--no-memory", action="store_true",
                   help="Disable cross-session project memory (remember/recall + "
                        "auto-recall) for this run.")
    p.add_argument("--no-memory-suggest", action="store_true",
                   help="Don't propose durable facts to remember at the end of a run.")
    p.add_argument("--skip-preflight", action="store_true",
                   help="Skip the Ollama reachability / model-pulled check.")
    p.add_argument("--no-color", action="store_true")
    # V3 (ADR-0017): full-screen interactive TUI for `chat`. Auto-on when textual
    # is installed + TTY; --no-tui forces the plain REPL, --tui forces it on.
    p.add_argument("--tui", action="store_true",
                   help="Force the full-screen interactive TUI (chat only).")
    p.add_argument("--no-tui", action="store_true",
                   help="Force the plain REPL even if the TUI is available.")


def _revenant_version() -> str:
    """The installed revenant-cli version, from package metadata.

    Reads the distribution's own metadata (works for a normal or editable
    install); falls back to "unknown" if the package isn't installed (e.g. run
    straight from a source checkout with no metadata) so `--version` never
    crashes.
    """
    from importlib.metadata import version, PackageNotFoundError
    try:
        return version("revenant-cli")
    except PackageNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="revenant", description="Local coding agent (offline, Ollama-backed)."
    )
    parser.add_argument("--version", action="version",
                        version=f"revenant {_revenant_version()}")
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Run a single goal to completion (one-shot).")
    p_run.add_argument("goal", nargs="?", default="",
                       help="What you want the agent to do. Optional when --skill is given.")
    p_run.add_argument("--skill", metavar="NAME",
                       help="Run a skill's procedure as the goal (see `revenant skills`).")
    p_run.add_argument("--plan", action="store_true",
                       help="Decompose the goal into small steps and run them one "
                            "at a time, each verified before the next (H3).")
    _add_common_flags(p_run)

    p_chat = sub.add_parser("chat", help="Interactive multi-turn session (REPL).")
    _add_common_flags(p_chat)

    # F13 (P5): run a goal autonomously, iterating until a success condition.
    p_loop = sub.add_parser("loop", help="Run a goal autonomously until a condition is met.")
    p_loop.add_argument("goal", help="What you want the agent to accomplish.")
    _add_common_flags(p_loop)
    p_loop.add_argument("--autonomous", action="store_true",
                        help="Run unattended: auto-approve edits within the budget "
                             "(a checkpoint is taken before each iteration for undo).")
    p_loop.add_argument("--until", metavar="CMD",
                        help="Success when this shell command exits 0.")
    p_loop.add_argument("--until-tests", action="store_true",
                        help="Success when the test command exits 0 (see --test-cmd).")
    p_loop.add_argument("--until-file", metavar="PATH",
                        help="Success when PATH exists.")
    p_loop.add_argument("--test-cmd", default="pytest -q",
                        help="Command used by --until-tests (default: 'pytest -q').")
    p_loop.add_argument("--max-iterations", type=int, default=10,
                        help="Stop after this many iterations (default: 10).")
    p_loop.add_argument("--max-wall", type=float, default=0.0,
                        help="Stop after this many seconds of wall clock (0 = no limit).")
    p_loop.add_argument("--dry-run", action="store_true",
                        help="Preview: record intended edits/commands without executing them.")
    p_loop.add_argument("--watch", metavar="GLOB",
                        help="Re-run the loop whenever a workspace file matching GLOB "
                             "changes (mtime poll; respects ignore globs). Ctrl-C to stop.")
    p_loop.add_argument("--watch-interval", type=float, default=1.0,
                        help="Seconds between --watch polls (default: 1.0).")
    p_loop.add_argument("--every", type=float, metavar="SECONDS",
                        help="Re-run the loop every SECONDS on a fixed interval "
                             "(within the budget). Ctrl-C to stop.")

    p_undo = sub.add_parser("undo", help="Revert file changes the agent made.")
    p_undo.add_argument("--workspace", default=".", help="Repo root (default: cwd).")
    p_undo.add_argument("--all", action="store_true",
                        help="Revert all recorded changes (default: just the last).")
    p_undo.add_argument("--no-color", action="store_true")

    # F11 (P3): manage MCP servers configured via [[mcp.servers]]. The workspace
    # / color flags are added to BOTH the `mcp` parser and each sub-action, so
    # `revenant mcp list --workspace X` and `revenant mcp --workspace X list`
    # both parse (argparse won't accept a parent optional after a sub-action).
    def _add_mcp_flags(p: argparse.ArgumentParser, *, suppress: bool) -> None:
        # On sub-action parsers, default to SUPPRESS so a value given at the
        # parent level (`mcp --workspace X list`) isn't clobbered by the sub's default.
        ws_default = argparse.SUPPRESS if suppress else "."
        p.add_argument("--workspace", default=ws_default, help="Repo root (default: cwd).")
        if suppress:
            p.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS)
        else:
            p.add_argument("--no-color", action="store_true")

    p_mcp = sub.add_parser("mcp", help="Inspect configured MCP servers and their tools.")
    _add_mcp_flags(p_mcp, suppress=False)
    mcp_sub = p_mcp.add_subparsers(dest="mcp_action")
    p_mcp_list = mcp_sub.add_parser("list", help="List configured servers and their tools.")
    _add_mcp_flags(p_mcp_list, suppress=True)
    p_mcp_test = mcp_sub.add_parser("test", help="Connect to a server and report health.")
    _add_mcp_flags(p_mcp_test, suppress=True)
    p_mcp_test.add_argument("name", help="Server name to test (from [[mcp.servers]]).")
    p_mcp_add = mcp_sub.add_parser("add", help="Add an MCP server to your config.")
    _add_mcp_flags(p_mcp_add, suppress=True)
    p_mcp_add.add_argument("name", help="A unique name for the server.")
    p_mcp_add.add_argument("--transport", default="stdio", choices=["stdio", "http", "sse"],
                           help="How to reach the server (default: stdio).")
    p_mcp_add.add_argument("--command", help="stdio: the server executable.")
    p_mcp_add.add_argument("--arg", action="append", default=[], dest="args",
                           help="stdio: an argument to the command (repeatable).")
    p_mcp_add.add_argument("--url", help="http/sse: the server URL (local).")
    p_mcp_add.add_argument("--project", action="store_true",
                           help="Write to the project .revenant.toml instead of user config.")

    # F12 (P4): inspect skills (reusable SKILL.md workflows).
    def _add_skills_flags(p: argparse.ArgumentParser, *, suppress: bool) -> None:
        ws_default = argparse.SUPPRESS if suppress else "."
        p.add_argument("--workspace", default=ws_default, help="Repo root (default: cwd).")
        if suppress:
            p.add_argument("--no-color", action="store_true", default=argparse.SUPPRESS)
        else:
            p.add_argument("--no-color", action="store_true")

    p_skills = sub.add_parser("skills", help="List and show reusable skills (SKILL.md).")
    _add_skills_flags(p_skills, suppress=False)
    skills_sub = p_skills.add_subparsers(dest="skills_action")
    p_skills_list = skills_sub.add_parser("list", help="List available skills.")
    _add_skills_flags(p_skills_list, suppress=True)
    p_skills_show = skills_sub.add_parser("show", help="Print a skill's full body.")
    _add_skills_flags(p_skills_show, suppress=True)
    p_skills_show.add_argument("name", help="Skill name to show.")

    # F3 (P6): resume a saved session.
    p_resume = sub.add_parser("resume", help="Resume a saved session (or list them).")
    p_resume.add_argument("session_id", nargs="?",
                          help="Session to resume (default: the most recent).")
    _add_common_flags(p_resume)

    # U2 (ADR-0016): setup diagnostics + model discovery.
    p_doctor = sub.add_parser("doctor", help="Check Ollama + model setup and show resolved config.")
    p_doctor.add_argument("--workspace", default=".", help="Repo root (default: cwd).")
    p_doctor.add_argument("--base-url", default="", help="Model server URL.")
    p_doctor.add_argument("--model", default="", help="Model to check (default: resolved).")
    p_doctor.add_argument("--no-color", action="store_true")

    p_models = sub.add_parser("models", help="List models pulled on the Ollama server.")
    p_models.add_argument("--workspace", default=".", help="Repo root (default: cwd).")
    p_models.add_argument("--base-url", default="", help="Model server URL.")
    p_models.add_argument("--no-color", action="store_true")

    p_config = sub.add_parser("config", help="Show or set configuration values.")
    p_config.add_argument("--workspace", default=".", help="Repo root (default: cwd).")
    p_config.add_argument("--no-color", action="store_true")
    config_sub = p_config.add_subparsers(dest="config_action")
    p_config_show = config_sub.add_parser("show", help="Print the resolved configuration.")
    p_config_show.add_argument("--workspace", default=".", help=argparse.SUPPRESS)
    p_config_show.add_argument("--no-color", action="store_true", help=argparse.SUPPRESS)
    p_config_set = config_sub.add_parser("set", help="Set a value: `config set model=qwen2.5:7b`.")
    p_config_set.add_argument("assignment", help="KEY=VALUE (e.g. model=qwen2.5:7b).")
    p_config_set.add_argument("--project", action="store_true",
                              help="Write to project .revenant.toml (default: user config).")
    p_config_set.add_argument("--workspace", default=".", help=argparse.SUPPRESS)
    p_config_set.add_argument("--no-color", action="store_true", help=argparse.SUPPRESS)
    return parser


def _normalize_argv(argv: list[str]) -> list[str]:
    """Back-compat + convenience.

    - Bare `revenant` (no args at all) opens interactive chat (the TUI when
      available, else the REPL) — the friendliest default.
    - `revenant "<goal>"` still runs one-shot: if the first token isn't a known
      subcommand (and isn't a help/option flag), it's treated as an implicit `run`.
    """
    if not argv:
        return ["chat"]
    first = argv[0]
    if first in _SUBCOMMANDS or first in ("-h", "--help", "--version"):
        return argv
    return ["run", *argv]


def _best_pulled_model(available: "list[str]") -> str:
    """Pick a sensible default from pulled models: prefer a coder model, then a
    qwen, else the first. Used for the non-interactive auto-pick."""
    for pref in ("coder", "qwen"):
        for name in available:
            if pref in name.lower():
                return name
    return available[0]


def _persist_model(chosen: str, config, color) -> None:
    config.model = chosen
    try:
        path = write_model_choice(chosen, scope="user")
        print(f"{color['dim']}saved model={chosen} to {path} (change it with "
              f"`revenant config set model=…`){color['reset']}")
    except OSError:
        print(f"{color['dim']}(using {chosen} for this run; could not save config){color['reset']}")


def _offer_model_picker(pf: "preflight.PreflightResult", config, color) -> "str | None":
    """After a failed pre-flight, resolve a pulled model to proceed (U2, polished).

    - Interactive TTY: show a numbered picker; the chosen model is persisted so the
      next run doesn't ask again.
    - Non-interactive (piped / CI) but a server IS reachable with models pulled:
      auto-pick a sensible model (prefer a coder model) and persist it, rather than
      hard-failing on a merely-unpulled default.
    - Nothing pulled / server down: return None (caller hard-fails with the
      actionable message `check()` already printed).
    """
    if not (pf.reachable and pf.available):
        return None
    configured = getattr(config, "model", "") or "the default"

    if not sys.stdin.isatty():
        chosen = _best_pulled_model(pf.available)
        print(f"{color['dim']}model {configured!r} isn't pulled; auto-selecting "
              f"{chosen!r} from your pulled models.{color['reset']}")
        _persist_model(chosen, config, color)
        return chosen

    print(f"\n{color['bold']}Your configured model {configured!r} isn't pulled. "
          f"Pick one of your {len(pf.available)} pulled models "
          f"(saved for next time):{color['reset']}")
    for i, name in enumerate(pf.available, 1):
        print(f"  {color['cyan']}{i}{color['reset']}) {name}")
    try:
        raw = input(f"{color['bold']}Number (or Enter to cancel): {color['reset']}").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not raw.isdigit() or not (1 <= int(raw) <= len(pf.available)):
        print(f"{color['dim']}cancelled.{color['reset']}")
        return None
    chosen = pf.available[int(raw) - 1]
    _persist_model(chosen, config, color)
    return chosen


def _build_agent(args: argparse.Namespace):
    """Assemble (config, registry, loop knobs, color) shared by run and chat.

    Returns None if the workspace is invalid (caller returns exit code 2).
    """
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"error: workspace is not a directory: {workspace}", file=sys.stderr)
        return None

    # Layer in config (flag > project .revenant.toml > user config > OLLAMA_HOST
    # env > default).
    cfg = load_config(workspace)
    base_url = _normalize_base_url(resolve("base_url", args.base_url, cfg, _default_base_url()))
    model = resolve("model", args.model, cfg, "")
    read_only = resolve("read_only", args.read_only, cfg, False)
    yolo = resolve("yolo", args.yolo, cfg, False)
    # Reflect resolved values back onto args so downstream helpers (_mode_label) agree.
    args.read_only, args.yolo = read_only, yolo

    color = _color(sys.stdout.isatty() and not args.no_color)
    profiles = load_profiles(default_data_dir() / "profiles.json")
    config = build_config(profiles, base_url, model or None)

    # U1 (ADR-0016): pre-flight — is Ollama up and the model pulled? Hard-fail
    # here with an actionable message instead of a cryptic error deep in the
    # first model call. `--skip-preflight` bypasses. Offers an interactive picker
    # (U2) when a real model is simply not pulled and stdin is a TTY.
    if not getattr(args, "skip_preflight", False):
        pf = preflight.check(config.base_url, config.model)
        if not pf.ok:
            print(f"{color['red']}{pf.message}{color['reset']}", file=sys.stderr)
            picked = _offer_model_picker(pf, config, color)
            if picked is None:
                return None  # callers map None -> exit 2

    # A small/fast model for LLM-backed context compaction (F5). Resolved from the
    # 'summary' role; None if unmapped, in which case compaction uses the heuristic.
    summarizer = config_for_role("summary", base_url, profiles, base=None)

    # Hardware-aware defaults; explicit flag or config value overrides.
    rec = recommend(config.model, base_url=config.base_url)
    max_steps = resolve("max_steps", args.max_steps, cfg, 0) or rec.max_steps
    max_context = resolve("max_context_tokens", args.max_context_tokens, cfg, 0) or rec.max_context_tokens

    tools = build_fs_tools(workspace)
    checkpointer = None
    mcp_clients: list = []
    if not read_only:
        tools += build_edit_tools(workspace)
        tools.append(build_bash_tool(workspace))
        # Undo: prefer git-native checkpointing when the workspace is a git repo
        # (F16.1/P8) — it captures the whole tree incl. run_bash side-effects.
        # Otherwise fall back to file-snapshots (F8/P2.5).
        if is_git_repo(workspace):
            checkpointer = GitCheckpointer(workspace)
            print(f"{color['dim']}undo: git-native (whole-tree){color['reset']}")
        else:
            checkpointer = Checkpointer(workspace, store_path=_checkpoint_store(workspace))
        # F15.1 (P8): let the agent delegate a scoped sub-goal to a nested loop.
        # V2 (ADR-0017): pass a late-bound sink so the sub-agent's events surface in
        # the parent's console. The loop (and its on_event) is built below, after
        # `tools`, so we forward to `loop.on_event` at call time via `parent_hole`.
        parent_hole: dict = {}
        def _parent_sink(ev):
            sink = parent_hole.get("on_event")
            if sink is not None:
                sink(ev)
        tools.append(build_spawn_tool(
            _make_subagent_factory(args), depth=getattr(args, "_subagent_depth", 0),
            parent_sink=_parent_sink))
        # F11 (P3): connect configured MCP servers and add their tools. A server
        # that fails to connect is skipped with a warning (degrade, ADR-0001).
        specs = mcp_server_specs(cfg)
        if specs:
            mcp_tools, mcp_clients = build_mcp_tools(specs)
            tools += mcp_tools
            if mcp_tools:
                print(f"{color['dim']}mcp: loaded {len(mcp_tools)} tool(s) "
                      f"from {len(mcp_clients)} server(s){color['reset']}")

    # F14 (P7): index the workspace into a code graph and expose read-only
    # structural retrieval tools (defn_of / who_calls / neighbors / impact_of).
    # Read-only, so available in every mode. Opt out with --no-graph on big repos.
    # `graph` stays None when disabled/failed so H2 context injection below is a
    # clean no-op too (same "absent graph -> identical to today" degrade, ADR-0013).
    graph = None
    if not getattr(args, "no_graph", False):
        try:
            # W3 (ADR-0020): load a persisted graph and refresh only changed files
            # (falls back to a full build on a missing/corrupt cache). --no-graph-
            # cache forces a from-scratch build (no persistence).
            if getattr(args, "no_graph_cache", False):
                graph = build_index(workspace)
            else:
                cache_path = workspace / ".aibot" / "code_graph.json"
                graph = load_or_build_index(workspace, cache_path)
            tools += build_code_graph_tools(graph)
            st = graph.stats()
            print(f"{color['dim']}graph: {st['symbols']} symbols across "
                  f"{st['files']} files{color['reset']}")
        except Exception as exc:  # noqa: BLE001 - indexing must never block a run
            print(f"{color['dim']}graph: skipped ({exc}){color['reset']}")

    # M1 (ADR-0022): cross-session memory — remember/recall tools over a stdlib
    # SQLite store under .aibot/memory.db. Read-only w.r.t. the workspace, so in
    # every mode. --no-memory disables it; a store that can't open degrades to a
    # null store (tools just report "nothing saved / no matches").
    mem_store = None
    if not getattr(args, "no_memory", False):
        from nerva_agent.memory_store import MemoryStore
        from nerva_agent.memory_tools import build_memory_tools
        mem_store = MemoryStore(workspace / ".aibot" / "memory.db")
        tools += build_memory_tools(mem_store)

    registry = ToolRegistry(tools)

    # F6 (tier a): ground the agent on the project's own instruction file if present.
    preamble = compose_preamble(CODING_PREAMBLE, workspace)
    doc = find_project_doc(workspace)
    if doc is not None:
        print(f"{color['dim']}context: loaded {doc.name}{color['reset']}")

    # F12 (P4): fold a compact skill index into the preamble (progressive
    # disclosure — only names+descriptions, never bodies). The REPL can then
    # invoke a skill by name to load its full procedure.
    skills = _load_skills(workspace)
    index = render_skill_index(skills)
    if index:
        preamble = f"{preamble}\n\n{index}"
        print(f"{color['dim']}skills: {len(skills)} available{color['reset']}")

    # H1 (0.3.0): verify → repair. When [verify] is enabled, check each edit and
    # feed failures back so the model repairs before shipping broken code. Off by
    # default (no [verify] section = no behavior change).
    verify_hook = None
    if not read_only:
        vcfg = verify_config(cfg)
        verifier = build_verifier(workspace, vcfg)
        if verifier is not None:
            verify_hook = make_verify_hook(
                workspace, verifier,
                max_repair_attempts=vcfg["max_repair_attempts"],
                checkpointer=checkpointer,
                emit=lambda m: print(f"{color['dim']}{m}{color['reset']}"),
            )
            print(f"{color['dim']}verify: on ({vcfg['max_repair_attempts']} repair "
                  f"attempts){color['reset']}")

    # H2 (0.3.0): proactive context injection. Auto-attach a symbol's def+callers
    # after an edit touches it (H2.1), and auto-resolve symbols named in an error
    # observation (H2.2) — pushing what pack_symbol_context/defn_of would return,
    # instead of waiting for the model to ask. No-op when the graph is absent
    # (--no-graph or indexing failed) or [context] disables both sub-features.
    ccfg = context_config(cfg)
    context_hook = make_context_hook(
        graph,
        inject_on_edit=ccfg["inject_on_edit"],
        resolve_errors=ccfg["resolve_errors"],
        max_callers=ccfg["max_callers"],
    )
    if context_hook is not None:
        print(f"{color['dim']}context: proactive injection on (max_callers="
              f"{ccfg['max_callers']}){color['reset']}")
    after_tool = compose_after_tool_hooks(verify_hook, context_hook)

    # U4 (ADR-0016): one Console — rich if installed + TTY + colors on, else the
    # byte-for-byte plain-ANSI fallback. Drives the live event stream, the
    # approval prompt (with a real diff), and the "thinking…" spinner.
    console = make_console(color=_color_enabled(args),
                           no_color_env=bool(os.environ.get("NO_COLOR")))

    loop = AgentLoop(
        config, registry,
        system_preamble=preamble,
        max_steps=max_steps,
        # None -> auto-detect native tool support per model; --no-native-tools forces off.
        use_native_tools=False if args.no_native_tools else None,
        on_event=_on_event(console),
        approve=_make_approver(console),
        auto_approve=yolo,
        max_context_tokens=max_context,
        summarizer_config=summarizer,
        before_tool=(checkpointer.snapshot if checkpointer else None),
        after_tool=after_tool,
        # W1 (ADR-0019): stream the assistant reply when explicitly asked. The
        # `chat` command flips the default on for an interactive TTY (below);
        # `run`/eval leave it off (None -> False) so batch output is unchanged.
        stream=bool(getattr(args, "stream", None)),
    )
    # Stash MCP clients on the loop so the command handler can close them on exit.
    loop._mcp_clients = mcp_clients
    # Stash skills + base preamble so the REPL's /skill can inject a body (F12.4).
    loop._skills = {s.name: s for s in skills}
    loop._base_preamble = preamble
    # Stash the checkpointer so `loop` can take a per-iteration undo boundary (F13.2).
    loop._checkpointer = checkpointer
    # Stash the console so command handlers can render the header + spinner (U4).
    loop._console = console
    # Stash the memory store so run-start recall (M2) + end-of-run suggestions (M3)
    # + the /memory command can reach it.
    loop._memory = mem_store
    # V2 (ADR-0017): now that the loop's on_event exists, expose it to the
    # spawn tool's late-bound parent sink so sub-agent events surface here.
    if not read_only:
        parent_hole["on_event"] = getattr(loop, "on_event", None)
    return workspace, config, rec, loop, color


def _close_mcp(loop) -> None:
    """Close any MCP server subprocesses attached to a loop. Never raises."""
    for client in getattr(loop, "_mcp_clients", ()) or ():
        try:
            client.close()
        except Exception:  # noqa: BLE001 - cleanup is best-effort
            pass


def _checkpoint_store(workspace: Path) -> Path:
    """Where a workspace's undo snapshots are persisted.

    Kept under the workspace's own data dir so `revenant undo` (a separate
    invocation) can reconstruct the checkpointer for exactly this repo.
    """
    return workspace / default_data_dir() / "checkpoints.json"


def _skill_dirs(workspace: Path) -> tuple[Path, Path]:
    """The (project, user) skill roots for a workspace (F12, ADR-0005).

    Project skills live in `<ws>/.revenant/skills`; user skills alongside the
    user config in `~/.config/revenant/skills`. Returned even if absent —
    `discover_skills` tolerates missing roots.
    """
    project = workspace / ".revenant" / "skills"
    user = user_config_path().parent / "skills"
    return project, user


def _load_skills(workspace: Path):
    """Discover all skills for a workspace (project overrides user by name)."""
    project, user = _skill_dirs(workspace)
    return discover_skills(project, user)


def _make_subagent_factory(parent_args: argparse.Namespace):
    """A loop_factory for spawn_subagent: builds a nested agent (F15.1, P8).

    Each sub-agent is a full `_build_agent` with a deeper `_subagent_depth` (so its
    own spawn tool refuses past the cap) and, if the parent named tools, a registry
    scoped to just those. Runs unattended, so it inherits auto-approve within the
    parent's mode.

    W5 (ADR-0021): when a `role` is given, the child's model is routed via
    `config_for_role` (a strong-planner / cheap-executor split within one run); an
    empty or unresolved role keeps the parent's config verbatim (byte-identical to
    before).
    """
    import copy

    def factory(goal: str, tool_names, depth: int, role: str = ""):
        child = copy.copy(parent_args)
        child._subagent_depth = depth
        # A sub-agent runs unattended; auto-approve within the parent's budget.
        child.yolo = True
        built = _build_agent(child)
        if built is None:
            raise RuntimeError("could not build sub-agent workspace")
        _ws, _cfg, _rec, loop, _color = built
        # W5: route the child to a role-specific model when asked (and resolvable).
        if role:
            try:
                profiles = load_profiles()
                routed = config_for_role(role, loop.config.base_url, profiles)
                if routed is not None:
                    loop.config = routed
            except Exception:  # noqa: BLE001 - unresolved role -> keep parent config
                pass
        if tool_names:
            scoped = scope_registry(
                loop.registry,
                type("S", (), {"name": "subagent", "tools": tool_names})(),
            )
            loop.registry = scoped
        return loop

    return factory


def _mode_label(args: argparse.Namespace) -> str:
    return "read-only" if args.read_only else ("yolo" if args.yolo else "approval-gated")


def _recall_block(store, goal: str, limit: int) -> str:
    """Format the memories most relevant to `goal` as a preamble block (M2,
    ADR-0022). Empty string when there's no store or no hit — so the preamble is
    byte-identical to today when memory is empty/absent."""
    if store is None or limit <= 0 or not (goal or "").strip():
        return ""
    try:
        hits = store.recall(goal, limit=limit)
    except Exception:  # noqa: BLE001 - recall must never break a run
        hits = []
    if not hits:
        return ""
    lines = [f"- {h.content}" for h in hits]
    return ("Project memory (recalled from earlier runs — trust these unless the "
            "code contradicts them):\n" + "\n".join(lines))


def _apply_memory_recall(loop, goal: str, max_recall: int) -> None:
    """Augment the loop's system preamble with memories relevant to `goal`.

    Rebuilds from `loop._base_preamble` each call (idempotent across REPL/TUI
    turns) so recalled memories reflect the current goal and never stack up.
    No-op if the loop has no memory store or no preamble to augment.
    """
    store = getattr(loop, "_memory", None)
    if store is None:
        return
    base = getattr(loop, "_base_preamble", None)
    if base is None:
        base = getattr(loop, "system_preamble", None)
    if base is None:
        return
    block = _recall_block(store, goal, max_recall)
    loop.system_preamble = f"{base}\n\n{block}" if block else base


def _memory_max_recall(args: argparse.Namespace, workspace: Path) -> int:
    """Resolve the recall count from [memory] config (0 disables)."""
    try:
        mc = memory_config(load_config(workspace))
        return mc["max_recall"] if mc["enabled"] else 0
    except Exception:  # noqa: BLE001
        return 5


def _maybe_suggest_memories(loop, args, goal, result, console) -> None:
    """M3 hook (filled in the M3 slice): offer to remember durable facts. A no-op
    placeholder for now so M2 can land independently."""
    return None


def _make_plan(loop, goal: str):
    """Ask the model for a step checklist and parse it (H3.1, ADR-0014).

    Uses the same loop's model config for one constrained call. Any failure
    degrades to a single-step plan (the whole goal) — never worse than today.
    """
    from nerva_agent.planner import parse_plan, PLANNING_PROMPT
    from nerva_core.local_llm_writer import call_model, LocalLLMError

    prompt = [{"role": "user", "content": PLANNING_PROMPT.format(goal=goal)}]
    try:
        text = call_model(loop.config, prompt)
    except (LocalLLMError, Exception):  # noqa: BLE001 - never fail planning
        text = ""
    return parse_plan(text, goal)


def _run_planned(loop, goal: str, color) -> int:
    """Decompose the goal and drive each step, threading history (H3.2, ADR-0014).

    Each step runs through the same loop (so H1 verify + H2 context hooks apply);
    only the transcript is threaded forward, keeping the model focused on one
    small step at a time. Stops early if a step doesn't reach a final answer.
    """
    from nerva_agent.planner import render_plan

    plan = _make_plan(loop, goal)
    print(f"{color['dim']}{render_plan(plan)}{color['reset']}")
    if plan.single:
        # Nothing to decompose — behave like a normal single-goal run.
        result = loop.run(goal)
        return 0 if result.stopped_reason == "final" else 3

    history: list[dict] = []
    for step in plan.steps:
        print(f"{color['bold']}[step {step.index}/{len(plan)}]{color['reset']} "
              f"{color['dim']}{step.goal}{color['reset']}")
        result = loop.run(step.goal, history=history or None)
        history = result.messages
        if result.stopped_reason != "final":
            print(f"{color['dim']}step {step.index} stopped ({result.stopped_reason}); "
                  f"halting the plan.{color['reset']}")
            return 3
    print(f"{color['dim']}plan complete: {len(plan)} step(s).{color['reset']}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    skill_name = getattr(args, "skill", None)
    if not args.goal and not skill_name:
        print("error: provide a GOAL, or --skill <name>.", file=sys.stderr)
        return 2

    built = _build_agent(args)
    if built is None:
        return 2
    workspace, config, rec, loop, color = built

    goal = args.goal
    if skill_name:
        # One-shot skill: load its body as the goal, scope tools (F12.4 follow-up).
        skill = getattr(loop, "_skills", {}).get(skill_name)
        if skill is None:
            known = ", ".join(sorted(getattr(loop, "_skills", {}))) or "(none)"
            print(f"error: no skill named {skill_name!r}. Available: {known}",
                  file=sys.stderr)
            _close_mcp(loop)
            return 2
        base = getattr(loop, "_base_preamble", loop.system_preamble)
        loop.system_preamble = compose_skill_body(base, skill)
        if skill.tools:
            loop.registry = scope_registry(loop.registry, skill)
        goal = args.goal or skill.body
        print(f"{color['dim']}skill: {skill.name}{color['reset']}")

    console = _loop_console(loop)
    console.session_header(model=config.model, workspace=workspace,
                           mode=_mode_label(args), capacity=rec.note)

    # H3 (ADR-0014): --plan decomposes the goal into small, verified steps so the
    # model only reasons about one at a time. Without it, a normal single run.
    if getattr(args, "plan", False):
        try:
            return _run_planned(loop, goal, color)
        finally:
            _close_mcp(loop)

    # M2 (ADR-0022): recall project memories relevant to this goal into the preamble.
    if not getattr(args, "no_memory", False):
        _apply_memory_recall(loop, goal, _memory_max_recall(args, workspace))

    try:
        with console.status_spinner("thinking…"):
            result = loop.run(goal)
    finally:
        # M3 (ADR-0022): after the run, offer to remember durable facts learned.
        _maybe_suggest_memories(loop, args, goal, result, console)
        _close_mcp(loop)
    if result.stopped_reason == "final":
        return 0
    if result.stopped_reason == "max_steps":
        return 3
    return 1


def _resolve_predicate(args: argparse.Namespace, workspace: Path):
    """Build the success predicate + a human label from the loop flags."""
    if args.until:
        return command_predicate(args.until, cwd=workspace), f"`{args.until}` exits 0"
    if args.until_tests:
        return command_predicate(args.test_cmd, cwd=workspace), f"`{args.test_cmd}` passes"
    if args.until_file:
        target = (workspace / args.until_file)
        return file_exists_predicate(target), f"{args.until_file} exists"
    # Weakest bound: the agent declaring completion. Still budget-limited.
    return model_final_predicate(), "the agent reports completion"


def _tree_signature(workspace: Path, glob: str) -> dict:
    """Map of {path: mtime} for files matching `glob`, respecting ignore globs.

    Used by --watch to detect changes cheaply without a filesystem-events dep.
    """
    from nerva_agent.agent_ignore import load_ignore_matcher
    matcher = load_ignore_matcher(workspace)
    sig: dict[str, float] = {}
    for path in workspace.glob(glob):
        if not path.is_file():
            continue
        rel = path.relative_to(workspace).as_posix()
        if matcher.match(rel, is_dir=False):
            continue
        try:
            sig[rel] = path.stat().st_mtime
        except OSError:
            continue
    return sig


def cmd_loop(args: argparse.Namespace, _watch_ticks=None) -> int:
    """Run a goal autonomously, iterating until a condition is met (F13, ADR-0006).

    Bounded by --max-iterations / --max-wall. --autonomous auto-approves edits
    (a checkpoint is taken before each iteration so `revenant undo` can step back
    a whole round). Each iteration is journaled as a resumable session.

    With --watch GLOB, the whole loop re-runs each time a matching file changes
    (mtime poll). `_watch_ticks` is an injectable iterable of sleep callables for
    testing; in normal use it polls on a timer until interrupted.
    """
    if getattr(args, "watch", None):
        return _cmd_loop_watch(args, _watch_ticks)
    if getattr(args, "every", None):
        return _cmd_loop_every(args, _watch_ticks)
    return _cmd_loop_once(args)


def _cmd_loop_watch(args: argparse.Namespace, watch_ticks=None) -> int:
    """Re-run the loop whenever a matching file changes (F13.3)."""
    import time as _time
    workspace = Path(args.workspace).resolve()
    color = _color(sys.stdout.isatty() and not args.no_color)
    glob = args.watch
    print(f"{color['dim']}watch: {glob} · re-runs on change · Ctrl-C to stop{color['reset']}")

    # A tick source: injected (tests) or a real sleep generator (never-ending).
    def real_ticks():
        while True:
            yield lambda: _time.sleep(args.watch_interval)
    ticks = watch_ticks if watch_ticks is not None else real_ticks()

    last = _tree_signature(workspace, glob)
    rc = _cmd_loop_once(args)  # initial run
    try:
        for sleep in ticks:
            sleep()
            current = _tree_signature(workspace, glob)
            if current != last:
                last = current
                print(f"{color['dim']}watch: change detected — re-running{color['reset']}")
                rc = _cmd_loop_once(args)
    except KeyboardInterrupt:
        print()
    return rc


def _cmd_loop_every(args: argparse.Namespace, ticks=None) -> int:
    """Re-run the loop on a fixed time interval (W3, ADR-0020).

    Parallel to `--watch` but time-triggered rather than change-triggered: sleep
    `--every` seconds, re-run, repeat until Ctrl-C. `ticks` is an injectable
    iterable of sleep callables for testing (each `next()` is one interval); in
    normal use it sleeps on a timer. Each iteration is a full `_cmd_loop_once`,
    so per-iteration budgets/journaling/undo boundaries still apply (ADR-0006).
    """
    import time as _time
    color = _color(sys.stdout.isatty() and not args.no_color)
    interval = args.every
    print(f"{color['dim']}every: re-runs every {interval}s · Ctrl-C to stop{color['reset']}")

    def real_ticks():
        while True:
            yield lambda: _time.sleep(interval)
    tick_source = ticks if ticks is not None else real_ticks()

    rc = _cmd_loop_once(args)  # initial run
    try:
        for sleep in tick_source:
            sleep()
            print(f"{color['dim']}every: interval elapsed — re-running{color['reset']}")
            rc = _cmd_loop_once(args)
    except KeyboardInterrupt:
        print()
    return rc


def _cmd_loop_once(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"error: workspace is not a directory: {workspace}", file=sys.stderr)
        return 2

    # --autonomous implies auto-approve, but ONLY within the declared budget and
    # with checkpointing on (ADR-0006). --dry-run keeps approvals off and swaps
    # in recording tools (handled in _build_agent via args.dry_run) — but we keep
    # this slice's dry-run at the driver level: it forces read-only so no edits
    # execute, letting the user preview the plan the agent narrates.
    if args.autonomous and not args.dry_run:
        args.yolo = True
    if args.dry_run:
        args.read_only = True

    built = _build_agent(args)
    if built is None:
        return 2
    workspace, config, rec, loop, color = built
    predicate, label = _resolve_predicate(args, workspace)

    mode = "dry-run" if args.dry_run else ("autonomous" if args.autonomous else "loop")
    print(f"{color['dim']}revenant loop · model={config.model} · {mode} · "
          f"until: {label} · max {args.max_iterations} iters{color['reset']}")

    checkpointer = getattr(loop, "_checkpointer", None)
    session_id = {"id": None}

    def on_iteration(info) -> None:
        # Per-iteration checkpoint boundary: a marker snapshot so undo can step
        # back a whole iteration (real file snapshots happen inside tool calls).
        if checkpointer is not None and not args.dry_run:
            try:
                checkpointer.snapshot("edit_file", {"path": f".aibot/loop-iter-{info.index}.marker"})
            except Exception:  # noqa: BLE001 - checkpoint boundary is best-effort
                pass
        state = "✓ done" if info.predicate.done else "· continuing"
        print(f"{color['bold']}[iter {info.index}]{color['reset']} "
              f"{color['dim']}{state} — {info.predicate.reason}{color['reset']}")
        # F13.4 run journal: persist the transcript so far as a resumable session.
        session_id["id"] = session_store.save_session(
            workspace, goal=args.goal, model=config.model,
            messages=info.messages, session_id=session_id["id"],
            turns_covered=info.index,
        ) or session_id["id"]

    budget = Budget(max_iterations=args.max_iterations,
                    max_wall_seconds=args.max_wall)
    try:
        outcome = loop_until(args.goal, loop.run, predicate, budget,
                             on_iteration=on_iteration)
    finally:
        _close_mcp(loop)

    if outcome.stopped_reason == "done":
        print(f"{color['bold']}done{color['reset']} "
              f"{color['dim']}in {outcome.iterations} iteration(s): "
              f"{outcome.last_reason}{color['reset']}")
        return 0
    print(f"{color['dim']}stopped ({outcome.stopped_reason}) after "
          f"{outcome.iterations} iteration(s); last: {outcome.last_reason}{color['reset']}")
    if session_id["id"]:
        print(f"{color['dim']}resume with: revenant resume {session_id['id']}{color['reset']}")
    return 3


def _run_chat_tui(args, workspace, config, loop, initial_history) -> "int | None":
    """Drive the chat session in the Textual TUI (V3–V5). Returns an exit code,
    or None to signal the caller to fall back to the REPL (textual missing/broke).

    Wires the same session auto-save the REPL uses: a saver closure the app calls
    after each turn keeps a stable session id for the app's lifetime."""
    try:
        from revenant_cli.tui import run_tui
    except Exception:  # noqa: BLE001 - guarded import failed -> REPL fallback
        return None

    sid = {"id": getattr(args, "session_id", None) or None}

    def save_turn(goal: str, model: str, messages: list) -> None:
        sid["id"] = session_store.save_session(
            workspace, goal=goal, model=model, messages=messages,
            session_id=sid["id"],
        ) or sid["id"]

    try:
        rc = run_tui(
            loop=loop, workspace=workspace, model=config.model,
            mode=_mode_label(args), session_saver=save_turn,
            history=initial_history,
        )
    except Exception as exc:  # noqa: BLE001 - a TUI crash shouldn't kill the CLI
        print(f"{_color(False)['reset']}TUI error ({exc}); falling back to REPL.",
              file=sys.stderr)
        return None
    finally:
        _close_mcp(loop)
    if sid["id"]:
        print(f"session saved: {sid['id']} (revenant resume {sid['id']})")
    return rc


def cmd_chat(args: argparse.Namespace, input_fn=input,
             initial_history: list[dict] | None = None) -> int:
    """Interactive multi-turn REPL.

    One AgentLoop is built once; each user line calls loop.run(line, history),
    threading the returned transcript back in so the agent keeps prior context.
    `input_fn` is injectable for testing. REPL commands: /exit, /reset, /help.

    `initial_history` seeds the transcript when resuming a saved session (F3). The
    session is auto-saved after every turn under `<ws>/.aibot/sessions/` so it can
    be resumed later; the id is stable for the REPL's lifetime.
    """
    # W1 (ADR-0019): stream by default in an interactive terminal (the live reply
    # is the point of chat); leave it as explicitly set otherwise. `run`/eval never
    # reach here, so their default stays off.
    if getattr(args, "stream", None) is None:
        args.stream = sys.stdout.isatty() and not getattr(args, "no_color", False)
    built = _build_agent(args)
    if built is None:
        return 2
    workspace, config, rec, loop, color = built
    c = color

    # V3 (ADR-0017): launch the full-screen TUI when available + interactive,
    # unless --no-tui. Falls back to the REPL below on any failure or when off.
    if _tui_enabled(args):
        rc = _run_chat_tui(args, workspace, config, loop, initial_history)
        if rc is not None:
            return rc  # None means "TUI unavailable/failed — fall through to REPL"

    console = _loop_console(loop)
    console.session_header(model=config.model, workspace=workspace,
                           mode=_mode_label(args), capacity=rec.note,
                           extras=["Commands: /exit · /reset · /skills · /skill <name> · /help"])

    history: list[dict] = list(initial_history) if initial_history else []
    session_id: str | None = getattr(args, "session_id", None) or None
    first_goal = ""
    try:
        while True:
            try:
                line = input_fn(f"{c['bold']}revenant› {c['reset']}").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line in ("/exit", "/quit"):
                break
            if line == "/reset":
                history = []
                print(f"{c['dim']}context cleared.{c['reset']}")
                continue
            if line == "/help":
                print(f"{c['dim']}/exit quit · /reset clear context · "
                      f"/skills list · /skill <name> run a skill · /help{c['reset']}")
                continue
            if line == "/skills":
                _print_skill_list(loop, c)
                continue
            if line.startswith("/skill"):
                goal = _skill_repl_goal(loop, line, c)
                if goal is None:
                    continue  # unknown/misused: message already printed
                line = goal  # fall through to run the skill body as this turn's goal

            # M2: recall memories for this turn's goal (matters most on turn 1,
            # where the system prompt is built; later turns already carry context).
            if not history and not getattr(args, "no_memory", False):
                _apply_memory_recall(loop, line, _memory_max_recall(args, workspace))

            with console.status_spinner("thinking…"):
                result = loop.run(line, history=history or None)
            # Thread the transcript forward so the next turn keeps context.
            history = result.messages
            # F3: persist the session after each turn so it can be resumed later.
            first_goal = first_goal or line
            session_id = session_store.save_session(
                workspace, goal=first_goal, model=config.model,
                messages=history, session_id=session_id,
            ) or session_id
    finally:
        _close_mcp(loop)
    if session_id:
        print(f"{c['dim']}session saved: {session_id} "
              f"(revenant resume {session_id}){c['reset']}")
    return 0


def cmd_resume(args: argparse.Namespace, input_fn=input) -> int:
    """Resume a saved session, or list sessions (F3, ADR-0007).

    `revenant resume list`  → list this workspace's sessions (newest first).
    `revenant resume [<id>]` → re-hydrate a session's transcript into a REPL;
                               defaults to the most recent when no id is given.
    """
    workspace = Path(args.workspace).resolve()
    color = _color(sys.stdout.isatty() and not args.no_color)
    sid = getattr(args, "session_id", None)

    if sid == "list":
        metas = session_store.list_sessions(workspace)
        if not metas:
            print(f"{color['dim']}no saved sessions for {workspace}.{color['reset']}")
            return 0
        for m in metas:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["updated_at"]))
            print(f"{color['bold']}{m['id']}{color['reset']} "
                  f"{color['dim']}{when} · {m['message_count']} msgs · "
                  f"{m['goal'][:60]}{color['reset']}")
        return 0

    if sid is None:
        sid = session_store.latest_session_id(workspace)
        if sid is None:
            print(f"{color['dim']}no saved sessions to resume for {workspace}. "
                  f"Start one with `revenant chat`.{color['reset']}")
            return 0

    session = session_store.load_session(workspace, sid)
    if session is None:
        print(f"error: no session {sid!r} for {workspace}. "
              f"Try `revenant resume list`.", file=sys.stderr)
        return 2

    print(f"{color['dim']}resuming session {sid} "
          f"({len(session.messages)} messages)…{color['reset']}")
    # Continue the same session id so further turns update it in place.
    args.session_id = sid
    return cmd_chat(args, input_fn=input_fn, initial_history=session.messages)


def cmd_undo(args: argparse.Namespace) -> int:
    """Revert changes recorded by a prior run's checkpointer (F8/F16.1).

    Uses git-native whole-tree undo when the workspace is a git repo (reverts
    run_bash side-effects too); otherwise the file-snapshot store.
    """
    workspace = Path(args.workspace).resolve()
    color = _color(sys.stdout.isatty() and not args.no_color)

    if is_git_repo(workspace):
        checkpointer = GitCheckpointer(workspace)
        has_any = checkpointer.has_snapshots()
    else:
        checkpointer = Checkpointer.load(workspace, _checkpoint_store(workspace))
        has_any = bool(checkpointer.snapshots)

    if not has_any:
        print(f"{color['dim']}nothing to undo (no recorded changes for {workspace}).{color['reset']}")
        return 0

    if args.all:
        done = checkpointer.undo_all()
        for desc in done:
            print(f"{color['bold']}↶{color['reset']} {desc}")
        print(f"{color['dim']}reverted {len(done)} change(s).{color['reset']}")
    else:
        desc = checkpointer.undo_last()
        print(f"{color['bold']}↶{color['reset']} {desc}")
    return 0


def _print_skill_list(loop, color) -> None:
    """Print the skills available in a REPL session (from loop._skills)."""
    skills = getattr(loop, "_skills", {}) or {}
    if not skills:
        print(f"{color['dim']}no skills available. Add one under "
              f".revenant/skills/<name>/SKILL.md{color['reset']}")
        return
    for name in sorted(skills):
        s = skills[name]
        print(f"{color['bold']}{s.slash}{color['reset']} "
              f"{color['dim']}— {s.description}{color['reset']}")


def _skill_repl_goal(loop, line: str, color, emit=None) -> str | None:
    """Handle `/skill <name>` in the REPL: return the skill body as the turn goal.

    Injects the skill's instructions into the loop's system preamble (progressive
    disclosure — the body loads only now) and scopes the registry to the skill's
    declared tools. Returns the body to run as this turn's goal, or None if the
    skill name is missing/unknown (a message is emitted).

    `emit(text)` is the message sink (default: plain `print`). The TUI passes its
    own sink so status lines land in the activity log, not stdout (which the TUI
    owns) — keeping this one loader shared between the REPL and the TUI."""
    say = emit or (lambda t: print(t))
    parts = line.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        say(f"{color['dim']}usage: /skill <name>  (see /skills){color['reset']}")
        return None
    name = parts[1].strip().lstrip("/")
    skills = getattr(loop, "_skills", {}) or {}
    skill = skills.get(name)
    if skill is None:
        known = ", ".join(sorted(skills)) or "(none)"
        say(f"{color['dim']}unknown skill {name!r}. Available: {known}{color['reset']}")
        return None
    # Load the body into the active preamble and scope the tools for this skill.
    base = getattr(loop, "_base_preamble", loop.system_preamble)
    loop.system_preamble = compose_skill_body(base, skill)
    if skill.tools:
        loop.registry = scope_registry(loop.registry, skill)
    say(f"{color['dim']}▶ skill '{skill.name}' loaded{color['reset']}")
    return skill.body


def cmd_skills(args: argparse.Namespace) -> int:
    """`revenant skills list|show <name>` — inspect available skills (F12.4)."""
    workspace = Path(args.workspace).resolve()
    color = _color(sys.stdout.isatty() and not args.no_color)
    skills = {s.name: s for s in _load_skills(workspace)}
    action = getattr(args, "skills_action", None) or "list"

    if action == "show":
        skill = skills.get(args.name)
        if skill is None:
            known = ", ".join(sorted(skills)) or "(none)"
            print(f"error: no skill named {args.name!r}. Available: {known}", file=sys.stderr)
            return 2
        print(f"{color['bold']}{skill.slash}{color['reset']} "
              f"{color['dim']}({skill.source}){color['reset']}")
        print(f"{color['dim']}{skill.description}{color['reset']}")
        if skill.tools:
            print(f"{color['dim']}tools: {', '.join(skill.tools)}{color['reset']}")
        print()
        print(skill.body)
        return 0

    # action == "list"
    if not skills:
        print(f"{color['dim']}no skills found. Add one under "
              f".revenant/skills/<name>/SKILL.md{color['reset']}")
        return 0
    for name in sorted(skills):
        s = skills[name]
        tools = f" {color['dim']}[{', '.join(s.tools)}]{color['reset']}" if s.tools else ""
        print(f"{color['bold']}{s.slash}{color['reset']} "
              f"{color['dim']}({s.source}) — {s.description}{color['reset']}{tools}")
    return 0


def _resolve_endpoint(args: argparse.Namespace):
    """Resolve (base_url, model, cfg) the same way `_build_agent` does, so
    `doctor`/`models` report exactly what a run would use (no drift)."""
    workspace = Path(args.workspace).resolve()
    cfg = load_config(workspace)
    base_url = _normalize_base_url(
        resolve("base_url", getattr(args, "base_url", ""), cfg, _default_base_url()))
    model = resolve("model", getattr(args, "model", ""), cfg, "")
    profiles = load_profiles(default_data_dir() / "profiles.json")
    config = build_config(profiles, base_url, model or None)
    return config.base_url, config.model, cfg, workspace


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check Ollama reachability + model + show resolved config (U2)."""
    color = _color(sys.stdout.isatty() and not args.no_color)
    c = color
    base_url, model, _cfg, workspace = _resolve_endpoint(args)

    print(f"{c['bold']}Revenant doctor{c['reset']}")
    print(f"  workspace : {workspace}")
    print(f"  base_url  : {base_url}"
          + (f"  {c['dim']}(OLLAMA_HOST={os.environ['OLLAMA_HOST']}){c['reset']}"
             if os.environ.get('OLLAMA_HOST') else ""))
    print(f"  model     : {model}")

    pf = preflight.check(base_url, model)
    if not pf.reachable:
        print(f"\n{c['red']}✗ {pf.message}{c['reset']}")
        return 1
    print(f"\n{c['green']}✓ Ollama reachable{c['reset']} — {len(pf.available)} model(s) pulled:")
    for name in pf.available:
        mark = f" {c['green']}← in use{c['reset']}" if name == model else ""
        print(f"    {name}{mark}")
    if pf.model_present:
        print(f"\n{c['green']}✓ model {model!r} is pulled — you're ready to run.{c['reset']}")
        return 0
    print(f"\n{c['yellow']}✗ {pf.message}{c['reset']}")
    return 1


def cmd_models(args: argparse.Namespace) -> int:
    """List models pulled on the Ollama server (U2)."""
    color = _color(sys.stdout.isatty() and not args.no_color)
    c = color
    base_url, model, _cfg, _ws = _resolve_endpoint(args)
    available = preflight.list_local_models(base_url)
    if available is None:
        print(f"{c['red']}Ollama isn't reachable at {base_url} — start it with "
              f"`ollama serve`.{c['reset']}", file=sys.stderr)
        return 1
    if not available:
        print(f"{c['dim']}No models pulled. Get one with: ollama pull {model or 'qwen2.5-coder:7b'}{c['reset']}")
        return 0
    for name in available:
        mark = f" {c['green']}← in use (code role){c['reset']}" if name == model else ""
        print(f"{name}{mark}")
    return 0


def _coerce_scalar(key: str, raw: str):
    """Coerce a `config set` string value to the type the key expects."""
    if key in ("read_only", "yolo"):
        return raw.strip().lower() in ("true", "1", "yes", "on")
    if key in ("max_steps", "max_context_tokens"):
        try:
            return int(raw)
        except ValueError:
            raise ValueError(f"{key} expects an integer, got {raw!r}")
    return raw


def cmd_config(args: argparse.Namespace) -> int:
    """Show or set configuration (replaces the stub)."""
    color = _color(sys.stdout.isatty() and not getattr(args, "no_color", False))
    c = color
    workspace = Path(args.workspace).resolve()
    action = getattr(args, "config_action", None) or "show"

    if action == "set":
        assignment = args.assignment
        if "=" not in assignment:
            print(f"{c['red']}usage: config set KEY=VALUE (e.g. model=qwen2.5:7b){c['reset']}",
                  file=sys.stderr)
            return 2
        key, raw = assignment.split("=", 1)
        key, raw = key.strip(), raw.strip()
        if key not in _SCALAR_KEYS:
            print(f"{c['red']}unknown key {key!r}. Known: {', '.join(_SCALAR_KEYS)}{c['reset']}",
                  file=sys.stderr)
            return 2
        try:
            value = _coerce_scalar(key, raw)
            path = write_scalar(key, value, scope="project" if args.project else "user",
                                workspace=workspace)
        except (ValueError, OSError) as exc:
            print(f"{c['red']}{exc}{c['reset']}", file=sys.stderr)
            return 2
        print(f"{c['bold']}✓{c['reset']} set {key} = {value!r} in {path}")
        return 0

    # show: print each scalar's resolved value + the effective model/base_url.
    cfg = load_config(workspace)
    base_url, model, _cfg, _ws = _resolve_endpoint(args)
    print(f"{c['bold']}Revenant config{c['reset']}  {c['dim']}(workspace {workspace}){c['reset']}")
    for key in _SCALAR_KEYS:
        val = resolve(key, "", cfg, "(unset)")
        src = "project" if key in cfg.get("_raw_project", {}) else (
            "user" if key in cfg.get("_raw_user", {}) else "default")
        print(f"  {key:<20} {val!r}  {c['dim']}({src}){c['reset']}")
    print(f"  {c['dim']}→ effective model={model!r} base_url={base_url!r}{c['reset']}")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    """Inspect configured MCP servers and their tools (F11.4)."""
    workspace = Path(args.workspace).resolve()
    color = _color(sys.stdout.isatty() and not args.no_color)
    cfg = load_config(workspace)
    specs = mcp_server_specs(cfg)

    action = getattr(args, "mcp_action", None) or "list"

    # W6 (ADR-0021): `mcp add` writes a new [[mcp.servers]] entry (works even with
    # zero servers configured, so it must run before the "none configured" guard).
    if action == "add":
        try:
            path = write_mcp_server(
                args.name,
                command=getattr(args, "command", None),
                args=getattr(args, "args", None),
                transport=getattr(args, "transport", "stdio"),
                url=getattr(args, "url", None),
                scope="project" if getattr(args, "project", False) else "user",
                workspace=workspace,
            )
        except ValueError as exc:
            print(f"{color['red']}{exc}{color['reset']}", file=sys.stderr)
            return 2
        print(f"{color['bold']}✓{color['reset']} added MCP server "
              f"{args.name!r} to {path}")
        return 0

    if not specs:
        print(f"{color['dim']}no MCP servers configured. Add one with "
              f"`revenant mcp add <name> …` or edit .revenant.toml.{color['reset']}")
        return 0

    if action == "test":
        target = next((s for s in specs if s.name == args.name), None)
        if target is None:
            print(f"error: no configured server named {args.name!r}. "
                  f"Known: {', '.join(s.name for s in specs)}", file=sys.stderr)
            return 2
        tools, clients = build_mcp_tools([target])
        try:
            if clients:
                print(f"{color['bold']}✓{color['reset']} {target.name}: "
                      f"connected, {len(tools)} tool(s)")
                for t in tools:
                    print(f"{color['dim']}    - {t.name}{color['reset']}")
                return 0
            print(f"{color['bold']}✗{color['reset']} {target.name}: could not connect")
            return 1
        finally:
            for c in clients:
                try:
                    c.close()
                except Exception:  # noqa: BLE001
                    pass

    # action == "list"
    tools, clients = build_mcp_tools(specs)
    try:
        connected = {c.spec.name for c in clients}
        by_server: dict[str, list[str]] = {}
        for t in tools:
            server = t.name.split(".", 1)[0]
            by_server.setdefault(server, []).append(t.name)
        for spec in specs:
            mark = "✓" if spec.name in connected else "✗"
            label = spec.alias or spec.name
            names = by_server.get(label, [])
            print(f"{color['bold']}{mark}{color['reset']} {spec.name} "
                  f"{color['dim']}({spec.transport}){color['reset']} — {len(names)} tool(s)")
            for name in names:
                print(f"{color['dim']}    - {name}{color['reset']}")
        return 0
    finally:
        for c in clients:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass


def main(argv: list[str] | None = None) -> int:
    raw = argv if argv is not None else sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args(_normalize_argv(raw))

    if args.command == "run":
        return cmd_run(args)
    if args.command == "chat":
        return cmd_chat(args)
    if args.command == "loop":
        return cmd_loop(args)
    if args.command == "undo":
        return cmd_undo(args)
    if args.command == "mcp":
        return cmd_mcp(args)
    if args.command == "skills":
        return cmd_skills(args)
    if args.command == "doctor":
        return cmd_doctor(args)
    if args.command == "models":
        return cmd_models(args)
    if args.command == "resume":
        return cmd_resume(args)
    if args.command == "config":
        return cmd_config(args)
    # No subcommand at all: show help.
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
