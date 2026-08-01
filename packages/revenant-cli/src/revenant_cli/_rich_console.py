"""RichConsole — the polished backend (U3, ADR-0016).

Imported ONLY from `console.make_console` inside a try/except, so `rich` stays an
optional dependency: if `import rich` fails, `make_console` falls back to
`PlainConsole`. Same interface as `PlainConsole` (duck-typed), so the CLI is
agnostic about which one it holds.

Polish over plain: a session-header panel, syntax-highlighted observations, a
real unified diff for edits, and a "thinking…" spinner that yields to streamed
output so lines never clobber the live region.
"""
from __future__ import annotations

from typing import Any

from rich.console import Console as _RichConsoleLib
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from nerva_agent.agent_loop import AgentEvent
from revenant_cli.console import unified_diff


class RichConsole:
    """rich-backed console. Duck-types PlainConsole's interface."""

    is_rich = True

    def __init__(self) -> None:
        self._c = _RichConsoleLib()
        self._err = _RichConsoleLib(stderr=True)
        self._spinner = None  # active rich Status, if any

    # --- spinner coordination --------------------------------------------
    def _pause(self):
        """Stop the live spinner (if any) so a print doesn't clobber it."""
        if self._spinner is not None:
            self._spinner.stop()

    def _resume(self):
        if self._spinner is not None:
            self._spinner.start()

    def _emit(self, renderable, *, err: bool = False):
        self._pause()
        (self._err if err else self._c).print(renderable)
        self._resume()

    # --- agent activity ---------------------------------------------------
    def event(self, ev: AgentEvent) -> None:
        # V2 (ADR-0017): sub-agent events carry a label; prefix them so nesting is
        # legible. Root events (agent == "") render exactly as before.
        pre = f"[sub:{ev.agent}] " if ev.agent else ""
        if ev.kind == "assistant" and ev.text:
            self._emit(Text(f"{pre}{ev.text}", style="dim"))
        elif ev.kind == "action":
            args = ", ".join(f"{k}={v!r}" for k, v in ev.args.items())
            self._emit(Text.assemble((f"{pre}→ ", "cyan"), (f"{ev.tool}", "bold cyan"),
                                      (f"({args})", "cyan")))
        elif ev.kind == "observation":
            self._emit(self._observation((f"{pre}{ev.text}") if pre else ev.text))
        elif ev.kind == "final":
            self._emit(Panel(Text(ev.text), title=f"answer{(' · ' + ev.agent) if ev.agent else ''}",
                             border_style="green"))
        elif ev.kind == "error":
            self._emit(Text(f"{pre}error: {ev.text}", style="bold red"), err=True)
        elif ev.kind == "limit":
            self._emit(Text(f"{pre}[{ev.text}]", style="yellow"), err=True)
        elif ev.kind == "compact":
            self._emit(Text(f"{pre}[context: {ev.text}]", style="dim"), err=True)
        elif ev.kind == "agent_start":
            self._emit(Panel(Text(ev.text), title=f"▸ sub-agent · {ev.agent}",
                             border_style="cyan", expand=False))
        elif ev.kind == "agent_end":
            self._emit(Text(f"▪ sub-agent [{ev.agent}] done", style="cyan"), err=True)
        # "context" events feed the live gauge (V3 TUI); RichConsole is a scrolling
        # view, so a per-step token line would be noise — stay quiet, like Plain.
        # "approval" handled by approval()/confirm().

    def _observation(self, text: str):
        # Show a code-ish observation with syntax highlighting; otherwise dim text.
        collapsed = text
        extra = ""
        lines = text.splitlines()
        if len(lines) > 20:
            collapsed = "\n".join(lines[:20])
            extra = f"\n… (+{len(lines) - 20} more lines)"
        looks_like_code = any(s in text for s in ("def ", "class ", "import ", "{", "};", "()"))
        if looks_like_code:
            try:
                return Syntax(collapsed + extra, "python", theme="ansi_dark",
                              background_color="default", word_wrap=True)
            except Exception:  # noqa: BLE001
                pass
        return Text((collapsed + extra), style="dim")

    # --- chrome -----------------------------------------------------------
    def status(self, msg: str, *, kind: str = "info") -> None:
        style = "red" if kind == "err" else "dim"
        self._emit(Text(msg, style=style), err=(kind == "err"))

    def session_header(self, *, model: str, workspace: Any, mode: str,
                       capacity: str = "", extras: "list[str] | None" = None) -> None:
        body = Text()
        body.append("model     ", style="bold");  body.append(f"{model}\n")
        body.append("workspace ", style="bold");  body.append(f"{workspace}\n")
        body.append("mode      ", style="bold");  body.append(f"{mode}")
        if capacity:
            body.append("\ncapacity  ", style="bold"); body.append(capacity)
        for line in (extras or []):
            body.append("\n"); body.append(line, style="dim")
        self._emit(Panel(body, title="revenant", border_style="cyan", expand=False))

    def banner(self, text: str) -> None:
        self._emit(Text(text, style="dim"))

    def rule(self, title: str = "") -> None:
        from rich.rule import Rule
        self._emit(Rule(title))

    def print(self, text: str = "") -> None:
        self._emit(Text(text))

    def error(self, text: str) -> None:
        self._emit(Text(text, style="bold red"), err=True)

    # --- approval + real diff --------------------------------------------
    def approval(self, tool: str, args: dict) -> None:
        self._pause()
        if tool == "edit_file":
            diff = unified_diff(str(args.get("path")), str(args.get("old", "")),
                                str(args.get("new", "")))
            self._c.print(Panel(
                Syntax(diff, "diff", theme="ansi_dark", background_color="default"),
                title=f"edit_file · {args.get('path')}", border_style="yellow"))
        elif tool == "write_file":
            content = str(args.get("content", ""))
            self._c.print(Panel(Syntax(content, "python", theme="ansi_dark",
                                       background_color="default", word_wrap=True),
                                title=f"write_file · {args.get('path')}", border_style="yellow"))
        elif tool == "run_bash":
            self._c.print(Panel(Text(f"$ {args.get('command')}", style="bold"),
                                title="run_bash", border_style="yellow"))
        else:
            body = ", ".join(f"{k}={v!r}" for k, v in args.items())
            self._c.print(Panel(Text(body), title=tool, border_style="yellow"))
        self._resume()

    def prompt(self, text: str) -> str:
        self._pause()
        try:
            return self._c.input(f"[bold]{text}[/bold]")
        finally:
            self._resume()

    def confirm(self, text: str) -> bool:
        self._pause()
        try:
            answer = self._c.input(f"[bold]{text} \\[y/N] [/bold]").strip().lower()
        except EOFError:
            answer = ""
        ok = answer in ("y", "yes")
        self._c.print(Text("approved" if ok else "declined", style="dim"))
        self._resume()
        return ok

    # --- spinner ----------------------------------------------------------
    def status_spinner(self, text: str = "thinking…"):
        return _RichSpinner(self, text)


class _RichSpinner:
    """Context manager wrapping a rich Status; registers itself so event/print
    can pause/resume it (see RichConsole._pause/_resume)."""
    def __init__(self, console: "RichConsole", text: str) -> None:
        self._console = console
        self._status = console._c.status(text, spinner="dots")

    def __enter__(self):
        self._console._spinner = self._status
        self._status.start()
        return self

    def __exit__(self, *a):
        self._status.stop()
        self._console._spinner = None
        return False
