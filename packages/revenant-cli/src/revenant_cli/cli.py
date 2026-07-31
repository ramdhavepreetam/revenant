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
import time
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

from revenant_cli.config import (
    load_config, resolve, mcp_server_specs, user_config_path,
)
from revenant_cli import session_store
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


_SUBCOMMANDS = ("run", "chat", "undo", "mcp", "skills", "config", "resume")


_DEFAULT_BASE_URL = "http://localhost:11434"


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
    p.add_argument("--read-only", action="store_true",
                   help="Disable mutating tools (write/edit/bash); investigate only.")
    p.add_argument("--yolo", action="store_true",
                   help="Auto-approve mutating tools (skips the y/N prompt; footgun guards still apply).")
    p.add_argument("--no-color", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="revenant", description="Local coding agent (offline, Ollama-backed)."
    )
    sub = parser.add_subparsers(dest="command")

    p_run = sub.add_parser("run", help="Run a single goal to completion (one-shot).")
    p_run.add_argument("goal", help="What you want the agent to do.")
    _add_common_flags(p_run)

    p_chat = sub.add_parser("chat", help="Interactive multi-turn session (REPL).")
    _add_common_flags(p_chat)

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

    # Skeleton subcommands wired in later slices.
    sub.add_parser("config", help="Show/edit configuration (coming soon).")
    return parser


def _normalize_argv(argv: list[str]) -> list[str]:
    """Back-compat: bare `revenant "<goal>"` still works.

    If the first token isn't a known subcommand (and isn't a help/option flag),
    treat the invocation as an implicit `run` so existing usage keeps working.
    """
    if not argv:
        return argv
    first = argv[0]
    if first in _SUBCOMMANDS or first in ("-h", "--help"):
        return argv
    return ["run", *argv]


def _build_agent(args: argparse.Namespace):
    """Assemble (config, registry, loop knobs, color) shared by run and chat.

    Returns None if the workspace is invalid (caller returns exit code 2).
    """
    workspace = Path(args.workspace).resolve()
    if not workspace.is_dir():
        print(f"error: workspace is not a directory: {workspace}", file=sys.stderr)
        return None

    # Layer in config (flag > project .revenant.toml > user config > default).
    cfg = load_config(workspace)
    base_url = resolve("base_url", args.base_url, cfg, _DEFAULT_BASE_URL)
    model = resolve("model", args.model, cfg, "")
    read_only = resolve("read_only", args.read_only, cfg, False)
    yolo = resolve("yolo", args.yolo, cfg, False)
    # Reflect resolved values back onto args so downstream helpers (_mode_label) agree.
    args.read_only, args.yolo = read_only, yolo

    color = _color(sys.stdout.isatty() and not args.no_color)
    profiles = load_profiles(default_data_dir() / "profiles.json")
    config = build_config(profiles, base_url, model or None)

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
        # F8: snapshot files before mutating tools so `revenant undo` can revert.
        checkpointer = Checkpointer(workspace, store_path=_checkpoint_store(workspace))
        # F11 (P3): connect configured MCP servers and add their tools. A server
        # that fails to connect is skipped with a warning (degrade, ADR-0001).
        specs = mcp_server_specs(cfg)
        if specs:
            mcp_tools, mcp_clients = build_mcp_tools(specs)
            tools += mcp_tools
            if mcp_tools:
                print(f"{color['dim']}mcp: loaded {len(mcp_tools)} tool(s) "
                      f"from {len(mcp_clients)} server(s){color['reset']}")
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

    loop = AgentLoop(
        config, registry,
        system_preamble=preamble,
        max_steps=max_steps,
        # None -> auto-detect native tool support per model; --no-native-tools forces off.
        use_native_tools=False if args.no_native_tools else None,
        on_event=make_printer(color),
        approve=make_approver(color),
        auto_approve=yolo,
        max_context_tokens=max_context,
        summarizer_config=summarizer,
        before_tool=(checkpointer.snapshot if checkpointer else None),
    )
    # Stash MCP clients on the loop so the command handler can close them on exit.
    loop._mcp_clients = mcp_clients
    # Stash skills + base preamble so the REPL's /skill can inject a body (F12.4).
    loop._skills = {s.name: s for s in skills}
    loop._base_preamble = preamble
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


def _mode_label(args: argparse.Namespace) -> str:
    return "read-only" if args.read_only else ("yolo" if args.yolo else "approval-gated")


def cmd_run(args: argparse.Namespace) -> int:
    built = _build_agent(args)
    if built is None:
        return 2
    workspace, config, rec, loop, color = built
    print(f"{color['dim']}revenant · model={config.model} · workspace={workspace} · {_mode_label(args)}{color['reset']}")
    print(f"{color['dim']}capacity: {rec.note}{color['reset']}")

    try:
        result = loop.run(args.goal)
    finally:
        _close_mcp(loop)
    if result.stopped_reason == "final":
        return 0
    if result.stopped_reason == "max_steps":
        return 3
    return 1


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
    built = _build_agent(args)
    if built is None:
        return 2
    workspace, config, rec, loop, color = built
    c = color
    print(f"{c['dim']}revenant chat · model={config.model} · workspace={workspace} · {_mode_label(args)}{c['reset']}")
    print(f"{c['dim']}capacity: {rec.note}{c['reset']}")
    print(f"{c['dim']}Type your goal. Commands: /exit, /reset, /help.{c['reset']}")

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
    """Revert file changes recorded by a prior session's checkpointer (F8)."""
    workspace = Path(args.workspace).resolve()
    color = _color(sys.stdout.isatty() and not args.no_color)
    store = _checkpoint_store(workspace)
    checkpointer = Checkpointer.load(workspace, store)

    if not checkpointer.snapshots:
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


def _skill_repl_goal(loop, line: str, color) -> str | None:
    """Handle `/skill <name>` in the REPL: return the skill body as the turn goal.

    Injects the skill's instructions into the loop's system preamble (progressive
    disclosure — the body loads only now) and scopes the registry to the skill's
    declared tools. Returns the body to run as this turn's goal, or None if the
    skill name is missing/unknown (a message is printed).
    """
    parts = line.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        print(f"{color['dim']}usage: /skill <name>  (see /skills){color['reset']}")
        return None
    name = parts[1].strip().lstrip("/")
    skills = getattr(loop, "_skills", {}) or {}
    skill = skills.get(name)
    if skill is None:
        known = ", ".join(sorted(skills)) or "(none)"
        print(f"{color['dim']}unknown skill {name!r}. Available: {known}{color['reset']}")
        return None
    # Load the body into the active preamble and scope the tools for this skill.
    base = getattr(loop, "_base_preamble", loop.system_preamble)
    loop.system_preamble = compose_skill_body(base, skill)
    if skill.tools:
        loop.registry = scope_registry(loop.registry, skill)
    print(f"{color['dim']}▶ skill '{skill.name}' loaded{color['reset']}")
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


def cmd_mcp(args: argparse.Namespace) -> int:
    """Inspect configured MCP servers and their tools (F11.4)."""
    workspace = Path(args.workspace).resolve()
    color = _color(sys.stdout.isatty() and not args.no_color)
    cfg = load_config(workspace)
    specs = mcp_server_specs(cfg)

    action = getattr(args, "mcp_action", None) or "list"

    if not specs:
        print(f"{color['dim']}no MCP servers configured. Add [[mcp.servers]] to "
              f".revenant.toml.{color['reset']}")
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
    if args.command == "undo":
        return cmd_undo(args)
    if args.command == "mcp":
        return cmd_mcp(args)
    if args.command == "skills":
        return cmd_skills(args)
    if args.command == "resume":
        return cmd_resume(args)
    if args.command == "config":
        print(f"'{args.command}' is not implemented yet — coming in a later release.",
              file=sys.stderr)
        return 2
    # No subcommand at all: show help.
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
