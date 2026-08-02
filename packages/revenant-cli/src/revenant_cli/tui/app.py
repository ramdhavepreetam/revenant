"""The Revenant Textual app (V3–V5, ADR-0017).

A full-screen terminal front-end for `revenant chat`: a persistent input box, a
live streaming activity log (with sub-agent lanes), a status bar with a context
gauge, a discoverable slash-command palette, and cooperative ctrl-c interrupt.

Threading model (the crux):
- One `AgentLoop` is built once (by the CLI, exactly as the REPL does).
- Each submitted line runs `loop.run(...)` in a Textual **thread worker** so the
  UI stays responsive. The loop emits `AgentEvent`s from that thread; the sink
  marshals each one onto the UI thread via `call_from_thread`.
- The loop's `approve` hook is synchronous and runs in the worker thread. We
  bridge it to a modal on the UI thread and block the worker on a
  `threading.Event` until the user answers — so approvals work inside the app.
- ctrl-c sets a flag the loop checks between steps (`should_stop`), cancelling the
  current run without killing the thread or the app.

Everything here imports `textual`; the module is only reached through the guarded
`revenant_cli.tui` package, so `textual` stays an optional dependency.
"""
from __future__ import annotations

import threading

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input

from nerva_agent.agent_loop import AgentEvent

from revenant_cli.tui.commands import SlashRegistry
from revenant_cli.tui.screens import ApprovalScreen, PaletteScreen
from revenant_cli.tui.widgets import (
    ActivityLog, ContextGauge, ModeBar, StatusBar, StreamLine,
)


class RevenantApp(App):
    """Interactive TUI over a prebuilt AgentLoop."""

    CSS = """
    Screen { layout: vertical; }
    #status { height: 1; padding: 0 1; background: $panel; }
    #gauge  { height: 1; padding: 0 1; }
    ActivityLog { height: 1fr; border: round $primary-darken-2; padding: 0 1; }
    #stream { height: auto; padding: 0 1; }
    #modebar { height: 1; padding: 0 1; dock: bottom; background: $panel; }
    #prompt { dock: bottom; }
    """

    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Interrupt/quit", priority=True),
        Binding("ctrl+d", "quit", "Quit", priority=True),
        Binding("ctrl+l", "clear_log", "Clear log"),
        # shift+tab cycles the approval mode (approval-gated <-> yolo). priority so
        # it fires even while the Input has focus.
        Binding("shift+tab", "cycle_mode", "Cycle mode", priority=True),
    ]

    def __init__(self, *, loop, workspace, model: str, mode: str,
                 session_saver=None, history=None) -> None:
        super().__init__()
        self.rv_loop = loop
        self.rv_workspace = str(workspace)
        self.rv_model = model
        self.rv_mode = mode
        self.rv_session_saver = session_saver     # save_turn(messages) -> None
        self.rv_history: list = list(history) if history else []
        self.rv_palette = SlashRegistry.from_loop(loop)
        self.rv_running = False
        self.rv_stop_flag = threading.Event()      # set by ctrl-c, read by the loop
        self.rv_agents_seen: set[str] = set()
        self.rv_first_goal = ""

    # --- layout -----------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield StatusBar(model=self.rv_model, workspace=self.rv_workspace,
                        mode=self.rv_mode, id="status")
        yield ContextGauge(id="gauge")
        yield ActivityLog(id="log")
        yield StreamLine(id="stream")   # W2: live token line, above the input
        yield Input(placeholder="Ask revenant…  (type / for commands)", id="prompt")
        yield ModeBar(id="modebar")     # approval-mode line below the input

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        self.query_one(ModeBar).mode = self.rv_mode   # seed the bottom mode line
        log = self.query_one(ActivityLog)
        log.append(AgentEvent("assistant", text="Ready. Type a goal, or / for commands."))

    # --- input ------------------------------------------------------------
    def on_input_changed(self, event: Input.Changed) -> None:
        # Typing "/" (as the first char) opens the discoverable command palette.
        if event.value == "/":
            self._open_palette("/")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        event.input.value = ""
        if not line:
            return
        if self.rv_running:
            self._log(AgentEvent("assistant", text="(busy — ctrl-c to interrupt)"))
            return
        if line.startswith("/"):
            self._handle_slash(line)
            return
        self._start_run(line)

    # --- slash commands ---------------------------------------------------
    def _open_palette(self, prefix: str) -> None:
        matches = self.rv_palette.match(prefix)

        def picked(name: "str | None") -> None:
            inp = self.query_one("#prompt", Input)
            if name:
                # Fill the input with the chosen command; user adds args / hits enter.
                inp.value = name + " "
                inp.cursor_position = len(inp.value)
            inp.focus()

        self.push_screen(PaletteScreen(matches), picked)

    def _handle_slash(self, line: str) -> None:
        cmd = self.rv_palette.get(line)
        name = line.split(" ", 1)[0]
        if cmd is None:
            self._log(AgentEvent("error", text=f"unknown command {name}. Type / to list."))
            return
        if cmd.kind == "skill":
            self._run_skill(name, line)
            return
        # Built-ins.
        if name in ("/exit", "/quit"):
            self.exit()
        elif name == "/reset":
            self.rv_history = []
            self._log(AgentEvent("assistant", text="context cleared."))
        elif name == "/clear":
            self.query_one(ActivityLog).clear()
        elif name == "/help":
            for c in self.rv_palette.all():
                self._log(AgentEvent("assistant", text=f"{c.name} {c.arg_hint} — {c.summary}"))
        elif name == "/skills":
            self._handle_slash("/help")  # skills appear in the same list
        elif name == "/model":
            arg = line[len(name):].strip()
            if arg:
                self._switch_model(arg)
            else:
                self._log(AgentEvent("assistant", text=f"model: {self.rv_model}  "
                                     "(use /model <name> to switch)"))
        elif name == "/mode":
            # Discoverable alias for the shift+tab toggle.
            self.action_cycle_mode()
        elif name == "/context":
            g = self.query_one(ContextGauge)
            self._log(AgentEvent("assistant",
                                 text=f"context: {g.used}/{g.budget or '—'} tokens"))
        elif name == "/agents":
            seen = ", ".join(sorted(self.rv_agents_seen)) or "(none yet)"
            self._log(AgentEvent("assistant", text=f"sub-agents this session: {seen}"))
        elif name == "/skill":
            self._log(AgentEvent("error", text="usage: /skill <name>  (see /skills)"))

    def _switch_model(self, name: str) -> None:
        """Switch the running model live (no restart). The tool registry is
        model-independent, so we only swap the loop's ChatConfig.model + refresh
        the StatusBar. Best-effort: an unresolvable name still sets the string so
        the next call surfaces any model error as a normal observation."""
        try:
            self.rv_loop.config.model = name
        except Exception as exc:  # noqa: BLE001 - never crash the UI on a bad name
            self._log(AgentEvent("error", text=f"could not switch model: {exc}"))
            return
        self.rv_model = name
        try:
            self.query_one(StatusBar)._model = name
            self.query_one(StatusBar).refresh()
        except Exception:  # noqa: BLE001 - status refresh is cosmetic
            pass
        self._log(AgentEvent("assistant", text=f"model → {name}"))

    def _run_skill(self, name: str, line: str) -> None:
        # Reuse the CLI's skill loader so behavior matches the REPL exactly, but
        # route its status text into our activity log (not stdout, which we own).
        from revenant_cli.cli import _skill_repl_goal, _color
        goal = _skill_repl_goal(
            self.rv_loop, f"/skill {name.lstrip('/')}", _color(False),
            emit=lambda t: self._log(AgentEvent("assistant", text=t)),
        )
        if goal is None:
            return  # the loader already emitted the reason into the log
        self._start_run(goal)

    # --- running a goal ---------------------------------------------------
    def _start_run(self, goal: str) -> None:
        self.rv_running = True
        self.rv_stop_flag.clear()
        self.rv_first_goal = self.rv_first_goal or goal
        self._log(AgentEvent("assistant", text=f"› {goal}"))
        self.rv_loop.on_event = self._thread_sink   # events arrive on the worker thread
        self.rv_loop.approve = self._thread_approve
        self._run_worker(goal)

    @work(thread=True, exclusive=True)
    def _run_worker(self, goal: str) -> None:
        result = self.rv_loop.run(
            goal, history=self.rv_history or None,
            should_stop=self.rv_stop_flag.is_set,
        )
        self.call_from_thread(self._on_run_done, result)

    def _on_run_done(self, result) -> None:
        self.rv_running = False
        self.rv_history = result.messages
        if self.rv_session_saver is not None:
            try:
                self.rv_session_saver(self.rv_first_goal, self.rv_model, self.rv_history)
            except Exception:  # noqa: BLE001 - saving is best-effort
                pass
        self.query_one("#prompt", Input).focus()

    # --- event sink (worker thread -> UI thread) --------------------------
    def _thread_sink(self, ev: AgentEvent) -> None:
        self.call_from_thread(self._apply_event, ev)

    def _apply_event(self, ev: AgentEvent) -> None:
        if ev.kind == "context" and ev.context is not None:
            g = self.query_one(ContextGauge)
            g.used = ev.context.used_tokens
            g.budget = ev.context.max_tokens
            return
        # W2 (ADR-0019): stream token deltas live on the StreamLine (root turns
        # only; sub-agent tokens stay in their lane's log to keep the live line
        # unambiguous). The completed turn's full text still lands in the log.
        if ev.kind == "token":
            if not ev.agent and ev.text:
                self.query_one(StreamLine).feed(ev.text)
            return
        # Any non-token activity closes the live line — its finished text arrives
        # as the "assistant"/"final"/"action" event we're about to log.
        self.query_one(StreamLine).clear_line()
        if ev.kind == "agent_start" and ev.agent:
            self.rv_agents_seen.add(ev.agent)
            self.query_one(StatusBar).agents = len(self.rv_agents_seen)
        self._log(ev)

    def _log(self, ev: AgentEvent) -> None:
        self.query_one(ActivityLog).append(ev)

    # --- approval bridge (worker thread blocks on a UI modal) -------------
    def _thread_approve(self, tool: str, args: dict) -> bool:
        """Called by the loop in the worker thread. Blocks it until the user
        answers a modal shown on the UI thread."""
        answer = {"ok": False}
        done = threading.Event()

        def ask() -> None:
            def got(ok: "bool | None") -> None:
                answer["ok"] = bool(ok)
                done.set()
            self.push_screen(ApprovalScreen(tool, args), got)

        self.call_from_thread(ask)
        done.wait()
        return answer["ok"]

    # --- key actions ------------------------------------------------------
    def action_interrupt(self) -> None:
        # First ctrl-c cancels a running goal; a second (when idle) quits.
        if self.rv_running:
            self.rv_stop_flag.set()
            self._log(AgentEvent("assistant", text="(interrupting…)"))
        else:
            self.exit()

    def action_clear_log(self) -> None:
        self.query_one(ActivityLog).clear()

    def action_cycle_mode(self) -> None:
        """shift+tab: toggle the approval mode approval-gated <-> yolo, live.

        Read-only is set at launch (it removes edit/bash tools at build time), so
        it can't flip mid-session — cycling from it is a no-op with a note. The
        toggle just flips the loop's auto_approve and re-labels the StatusBar +
        ModeBar so the change is immediately visible.
        """
        if self.rv_mode == "read-only":
            self._log(AgentEvent("assistant",
                                 text="read-only is set at launch (relaunch without --read-only to edit)."))
            return
        # approval-gated <-> yolo
        auto = not getattr(self.rv_loop, "auto_approve", False)
        self.rv_loop.auto_approve = auto
        self.rv_mode = "yolo" if auto else "approval-gated"
        self.query_one(StatusBar).mode = self.rv_mode
        self.query_one(ModeBar).mode = self.rv_mode
        self._log(AgentEvent("assistant", text=f"mode → {self.rv_mode}"))
