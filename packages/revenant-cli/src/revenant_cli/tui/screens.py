"""Modal screens for the Revenant TUI (V4/V5, ADR-0017).

  - PaletteScreen  — the discoverable slash-command menu (V4). Shows each command
    name + summary; dismisses with the chosen command name (or None on cancel).
  - ApprovalScreen — the mutating-tool approval modal (V5). Shows a real diff for
    edits; dismisses with True/False. The app's worker thread blocks on the result.

Both import `textual`; reached only through the guarded `revenant_cli.tui` package.
"""
from __future__ import annotations

from rich.text import Text

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, OptionList, Static
from textual.widgets.option_list import Option

from revenant_cli.console import unified_diff


class PaletteScreen(ModalScreen):
    """A pick-list of slash commands. Returns the selected command name or None."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    PaletteScreen { align: center middle; }
    #palette { width: 70%; max-width: 80; height: auto; max-height: 80%;
               border: round $primary; background: $panel; }
    """

    def __init__(self, commands) -> None:
        super().__init__()
        self._commands = list(commands)

    def compose(self) -> ComposeResult:
        opts = [
            Option(self._label(c), id=c.name) for c in self._commands
        ] or [Option("(no commands)", id="__none__")]
        with Vertical(id="palette"):
            yield OptionList(*opts)

    @staticmethod
    def _label(c) -> Text:
        t = Text()
        t.append(c.name, style="bold cyan")
        if c.arg_hint:
            t.append(f" {c.arg_hint}", style="cyan")
        t.append(f"  — {c.summary}", style="dim")
        return t

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        oid = event.option.id
        self.dismiss(None if oid in (None, "__none__") else oid)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ModelPickerScreen(ModalScreen):
    """A pick-list of pulled Ollama models. Returns the chosen model name or None.

    The model currently in use is marked and pre-highlighted, so switching to a
    bigger model (e.g. a 14B) is a visible choice, not something you have to know
    the exact name of.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    CSS = """
    ModelPickerScreen { align: center middle; }
    #modelpick { width: 70%; max-width: 80; height: auto; max-height: 80%;
                 border: round $primary; background: $panel; }
    #modelpick-title { padding: 0 1; color: $text-muted; }
    """

    def __init__(self, models, current: str = "") -> None:
        super().__init__()
        self._models = list(models)
        self._current = current

    def compose(self) -> ComposeResult:
        opts = []
        for m in self._models:
            label = Text()
            label.append(m, style="bold cyan")
            if m == self._current:
                label.append("  ← in use", style="green")
            opts.append(Option(label, id=m))
        if not opts:
            opts = [Option("(no models pulled — run `ollama pull …`)", id="__none__")]
        with Vertical(id="modelpick"):
            yield Static("Pick a model  (esc to cancel)", id="modelpick-title")
            yield OptionList(*opts)

    def on_mount(self) -> None:
        # Pre-highlight the current model so Enter keeps it (a safe default).
        if self._current in self._models:
            self.query_one(OptionList).highlighted = self._models.index(self._current)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        oid = event.option.id
        self.dismiss(None if oid in (None, "__none__") else oid)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ApprovalScreen(ModalScreen):
    """Approve/deny a mutating tool call. Returns True (approve) or False (deny)."""

    BINDINGS = [
        Binding("escape", "deny", "Deny"),
        Binding("y", "approve", "Approve"),
        Binding("n", "deny", "Deny"),
    ]

    CSS = """
    ApprovalScreen { align: center middle; }
    #approval { width: 80%; max-width: 100; height: auto; max-height: 80%;
                border: round $warning; background: $panel; padding: 1 2; }
    #preview { height: auto; max-height: 20; }
    #buttons { height: auto; align: center middle; }
    Button { margin: 1 2 0 2; }
    """

    def __init__(self, tool: str, args: dict) -> None:
        super().__init__()
        self._tool = tool
        self._args = args

    def compose(self) -> ComposeResult:
        with Vertical(id="approval"):
            yield Static(Text(f"APPROVAL NEEDED: {self._tool}", style="bold yellow"))
            yield Static(self._preview(), id="preview")
            with Vertical(id="buttons"):
                yield Button("Approve (y)", variant="success", id="approve")
                yield Button("Deny (n/esc)", variant="error", id="deny")

    def _preview(self) -> Text:
        a = self._args
        if self._tool == "edit_file":
            diff = unified_diff(str(a.get("path", "")), str(a.get("old", "")),
                                str(a.get("new", "")))
            return Text(diff or "(no change)")
        if self._tool == "write_file":
            content = str(a.get("content", ""))
            head = content if len(content) <= 800 else content[:800] + " …"
            return Text(f"path={a.get('path')!r}\n---\n{head}")
        if self._tool == "run_bash":
            return Text(f"$ {a.get('command', '')}")
        return Text(", ".join(f"{k}={v!r}" for k, v in a.items()))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "approve")

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)
