"""Textual widgets for the Revenant TUI (V3, ADR-0017).

Imported only from inside the guarded `revenant_cli.tui` package, so `textual`
stays an optional dependency. These widgets translate the loop's `AgentEvent`
stream into a live view:

  - ActivityLog  — the scrolling record of what the agent is doing, with
    sub-agent events shown in indented, coloured lanes (V2's `agent` field).
  - StatusBar    — always-visible model · workspace · mode · sub-agent count.
  - ContextGauge — a live "how full is the window" bar fed by `context` events.
"""
from __future__ import annotations

from rich.text import Text

from textual.reactive import reactive
from textual.widgets import RichLog, Static

from nerva_agent.agent_loop import AgentEvent

# Distinct lane colours cycled per sub-agent label so concurrent/successive
# sub-agents are visually separable.
_LANE_COLORS = ("magenta", "yellow", "green", "blue", "bright_cyan")


def _lane_color(label: str) -> str:
    return _LANE_COLORS[hash(label) % len(_LANE_COLORS)] if label else "white"


class ActivityLog(RichLog):
    """The streaming trace. `append(ev)` renders one AgentEvent."""

    def __init__(self, **kw) -> None:
        super().__init__(wrap=True, markup=False, highlight=False, **kw)

    def append(self, ev: AgentEvent) -> None:
        for line in self._render(ev):
            self.write(line)

    def _render(self, ev: AgentEvent) -> "list[Text]":
        # Sub-agent events (agent != "") get an indented, coloured lane so the
        # multi-agent structure is legible; root events are flush-left.
        if ev.agent:
            col = _lane_color(ev.agent)
            prefix = Text(f"  ┆ [{ev.agent}] ", style=col)
        else:
            col = None
            prefix = Text("")

        def line(body: Text) -> Text:
            out = prefix.copy()
            out.append_text(body)
            return out

        k = ev.kind
        if k == "assistant" and ev.text:
            return [line(Text(ev.text, style="dim"))]
        if k == "action":
            args = ", ".join(f"{a}={v!r}" for a, v in ev.args.items())
            return [line(Text.assemble(("→ ", "cyan"), (ev.tool, "bold cyan"),
                                       (f"({args})", "cyan")))]
        if k == "observation":
            body = ev.text if len(ev.text) <= 800 else ev.text[:800] + " …"
            return [line(Text(l, style="dim")) for l in ("  " + body).splitlines() or [""]]
        if k == "final":
            return [line(Text(ev.text, style="bold green"))]
        if k == "error":
            return [line(Text(f"error: {ev.text}", style="bold red"))]
        if k == "limit":
            return [line(Text(f"[{ev.text}]", style="yellow"))]
        if k == "interrupted":
            return [line(Text(f"[interrupted: {ev.text}]", style="yellow"))]
        if k == "compact":
            return [line(Text(f"[context: {ev.text}]", style="dim"))]
        if k == "agent_start":
            return [Text(f"▸ sub-agent [{ev.agent}]: {ev.text}",
                         style=f"bold {_lane_color(ev.agent)}")]
        if k == "agent_end":
            return [Text(f"▪ sub-agent [{ev.agent}] done",
                         style=_lane_color(ev.agent))]
        # "context" is consumed by the StatusBar gauge, not the log; "approval" is
        # handled by the modal screen. Anything else: ignore silently.
        return []


class ContextGauge(Static):
    """A compact token-usage bar: used/max with a filled meter."""

    used = reactive(0)
    budget = reactive(0)

    def render(self) -> Text:
        if not self.budget:
            return Text("context —", style="dim")
        frac = min(1.0, self.used / self.budget)
        width = 12
        filled = int(round(frac * width))
        bar = "█" * filled + "░" * (width - filled)
        style = "red" if frac >= 0.9 else ("yellow" if frac >= 0.7 else "green")
        return Text.assemble(
            ("ctx ", "dim"), (bar, style),
            (f" {self.used}/{self.budget}", "dim"),
        )


class StatusBar(Static):
    """model · workspace · mode · sub-agent count, plus the context gauge line."""

    mode = reactive("")
    agents = reactive(0)

    def __init__(self, *, model: str, workspace: str, mode: str, **kw) -> None:
        super().__init__(**kw)
        self._model = model
        self._workspace = workspace
        self.mode = mode

    def render(self) -> Text:
        t = Text()
        t.append("revenant", style="bold cyan")
        t.append(f"  {self._model}", style="dim")
        t.append(f"  {self._workspace}", style="dim")
        t.append(f"  [{self.mode}]", style="dim")
        if self.agents:
            t.append(f"  sub-agents:{self.agents}", style="magenta")
        return t
