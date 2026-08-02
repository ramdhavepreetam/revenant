"""Slash-command registry for the TUI palette (V4, ADR-0017).

Pure data + resolution logic, deliberately free of any `textual` import so it is
unit-testable without the optional TUI dependency. The app builds a `SlashRegistry`
from the loop (its skills) plus the built-in commands, and the Input widget queries
it to show a discoverable menu when the user types "/".

This is the "all the options available to the agent are visible" requirement made
concrete: every command carries a one-line summary shown in the palette.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SlashCommand:
    """One entry in the palette: the typed name, a summary, and a kind.

    `kind` is "builtin" (handled by the app) or "skill" (runs a skill body as the
    next turn's goal). `arg_hint` is shown after the name for commands that take one.
    """

    name: str            # includes the leading "/", e.g. "/help" or "/skill"
    summary: str
    kind: str = "builtin"
    arg_hint: str = ""


# The built-in commands the app knows how to handle. Kept here (not in the app) so
# they can be listed/tested without textual, and so the palette and the handler
# switch never drift apart — both read this list.
BUILTIN_COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("/help", "Show available commands and keys"),
    SlashCommand("/skills", "List the skills the agent can run"),
    SlashCommand("/skill", "Run a skill by name", "builtin", "<name>"),
    SlashCommand("/model", "Show or switch the model in use", "builtin", "<name>"),
    SlashCommand("/mode", "Toggle approval mode (approval-gated ↔ yolo)"),
    SlashCommand("/context", "Show current context usage vs. budget"),
    SlashCommand("/agents", "Show active/most-recent sub-agents"),
    SlashCommand("/reset", "Clear the conversation context"),
    SlashCommand("/clear", "Clear the activity log"),
    SlashCommand("/exit", "Quit revenant"),
)


class SlashRegistry:
    """Resolvable set of slash commands: built-ins + the loop's skills."""

    def __init__(self, commands: "list[SlashCommand]") -> None:
        # Preserve insertion order (built-ins first, then skills), dedup by name.
        self._by_name: dict[str, SlashCommand] = {}
        for c in commands:
            self._by_name.setdefault(c.name, c)

    @classmethod
    def from_loop(cls, loop) -> "SlashRegistry":
        """Built-in commands plus one `/<skill>` entry per skill on the loop."""
        cmds: list[SlashCommand] = list(BUILTIN_COMMANDS)
        skills = getattr(loop, "_skills", {}) or {}
        for name in sorted(skills):
            s = skills[name]
            slash = getattr(s, "slash", f"/{name}")
            desc = getattr(s, "description", "") or "run this skill"
            cmds.append(SlashCommand(slash, desc, kind="skill"))
        return cls(cmds)

    def all(self) -> "list[SlashCommand]":
        return list(self._by_name.values())

    def match(self, prefix: str) -> "list[SlashCommand]":
        """Commands whose name starts with `prefix` (case-insensitive).

        An empty or bare "/" prefix returns everything (so typing "/" opens the
        full menu). Matching is on the command word only — anything after the first
        space is an argument, not part of the name.
        """
        head = (prefix or "").split(" ", 1)[0].lower()
        if head in ("", "/"):
            return self.all()
        return [c for c in self.all() if c.name.lower().startswith(head)]

    def get(self, name: str) -> "SlashCommand | None":
        return self._by_name.get((name or "").split(" ", 1)[0])
