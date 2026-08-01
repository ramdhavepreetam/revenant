"""Console abstraction: one interface, two backends (U3, ADR-0016).

The CLI's live output is routed through a `Console` object with two backends:

  - PlainConsole — reproduces today's plain-ANSI output BYTE-FOR-BYTE. The safety
    net: with `rich` uninstalled, or piped/non-TTY, or `--no-color`/`NO_COLOR`,
    behavior is exactly as before. Regression-locked by tests.
  - RichConsole  — a polished view (panels, a "thinking…" spinner, real edit
    diffs, syntax-highlighted observations) when `rich` is installed and stdout
    is a TTY.

`make_console` picks the backend the same way `local_llm_writer._get_encoder`
guards its optional `tiktoken` import — so `rich` stays an *optional* dependency
with a graceful fallback; nothing here is required at runtime.

Terminal UX lives in revenant-cli only (ADR-0002). The agent loop is untouched:
`Console.event(AgentEvent)` is passed as the loop's `on_event` sink.
"""
from __future__ import annotations

import difflib
import sys
from typing import Any

from nerva_agent.agent_loop import AgentEvent

# The exact ANSI palette the CLI has always used (kept here so PlainConsole is
# byte-identical to the legacy make_printer/make_approver).
_C = {
    "dim": "\033[2m", "cyan": "\033[36m", "green": "\033[32m",
    "yellow": "\033[33m", "red": "\033[31m", "bold": "\033[1m", "reset": "\033[0m",
}


def _palette(enabled: bool) -> dict:
    return _C if enabled else {k: "" for k in _C}


def unified_diff(path: str, old: str, new: str) -> str:
    """A real unified diff for an edit_file preview (replaces the naive block)."""
    lines = difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile=f"{path} (before)", tofile=f"{path} (after)", lineterm="",
    )
    return "\n".join(lines)


class PlainConsole:
    """Plain-ANSI backend — byte-for-byte the legacy output. The always-available
    fallback. `color=False` yields no escape codes (piped / --no-color / NO_COLOR)."""

    is_rich = False

    def __init__(self, color: bool = True) -> None:
        self.c = _palette(color)

    # --- agent activity (was make_printer) --------------------------------
    def event(self, ev: AgentEvent) -> None:
        c = self.c
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
        # "approval" events are handled by the approver, not printed here.

    # --- chrome -----------------------------------------------------------
    def status(self, msg: str, *, kind: str = "info") -> None:
        print(f"{self.c['dim']}{msg}{self.c['reset']}", file=sys.stderr if kind == "err" else sys.stdout)

    def session_header(self, *, model: str, workspace: Any, mode: str,
                       capacity: str = "", extras: "list[str] | None" = None) -> None:
        c = self.c
        print(f"{c['dim']}revenant · model={model} · workspace={workspace} · {mode}{c['reset']}")
        if capacity:
            print(f"{c['dim']}capacity: {capacity}{c['reset']}")
        for line in (extras or []):
            print(f"{c['dim']}{line}{c['reset']}")

    def banner(self, text: str) -> None:
        print(f"{self.c['dim']}{text}{self.c['reset']}")

    def rule(self, title: str = "") -> None:
        print(f"{self.c['dim']}{title}{self.c['reset']}" if title else "")

    def print(self, text: str = "") -> None:
        print(text)

    def error(self, text: str) -> None:
        print(f"{self.c['red']}{text}{self.c['reset']}", file=sys.stderr)

    # --- approval (was make_approver body) --------------------------------
    def approval(self, tool: str, args: dict) -> None:
        c = self.c
        print(f"\n{c['yellow']}{c['bold']}APPROVAL NEEDED: {tool}{c['reset']}")
        print(f"{c['yellow']}{self._preview(tool, args)}{c['reset']}")

    def _preview(self, tool: str, args: dict) -> str:
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

    def prompt(self, text: str) -> str:
        return input(f"{self.c['bold']}{text}{self.c['reset']}")

    def confirm(self, text: str) -> bool:
        try:
            answer = input(f"{self.c['bold']}{text} [y/N] {self.c['reset']}").strip().lower()
        except EOFError:
            answer = ""
        ok = answer in ("y", "yes")
        print(f"{self.c['dim']}{'approved' if ok else 'declined'}{self.c['reset']}")
        return ok

    # --- spinner (no-op in plain to keep output byte-identical) ------------
    def status_spinner(self, text: str = "thinking…"):
        return _NullSpinner()


class _NullSpinner:
    """A no-op spinner context manager for PlainConsole."""
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def make_console(*, color: bool, no_color_env: bool):
    """Pick the console backend.

    RichConsole only when `rich` imports AND color is on AND stdout is a TTY AND
    NO_COLOR is unset. Otherwise PlainConsole (color-off when color is disabled) —
    so CI, pipes, --no-color, NO_COLOR, and a missing rich all get the legacy
    plain output. `rich` is an optional dependency (guarded import, like tiktoken).
    """
    use_rich = color and not no_color_env and sys.stdout.isatty()
    if use_rich:
        try:
            from revenant_cli._rich_console import RichConsole  # local, guarded
            return RichConsole()
        except Exception:  # noqa: BLE001 - rich absent/broken -> graceful fallback
            pass
    return PlainConsole(color=color and not no_color_env)
