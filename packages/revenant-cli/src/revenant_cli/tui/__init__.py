"""Optional Textual TUI for revenant (V-series, ADR-0017).

This package is the *only* place `textual` is imported, and always behind a guard
so it stays an optional dependency (`revenant-cli[tui]`), exactly like `rich`
(ADR-0016). The CLI asks `tui_available()` and, if a TUI was requested and textual
is present + on a TTY, calls `run_tui(...)`; otherwise it falls back to the plain
REPL, byte-identical to today.

`commands.py` is pure Python (no textual) and is imported directly by tests; the
widgets/screens/app modules import textual and are reached only through `run_tui`.
"""
from __future__ import annotations


def tui_available() -> bool:
    """True when the `textual` package can be imported (guarded, like tiktoken)."""
    try:
        import textual  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 - any import failure -> not available
        return False


def run_tui(*, loop, workspace, model: str, mode: str,
            session_saver=None, history=None) -> int:
    """Launch the interactive TUI over a prebuilt AgentLoop. Returns an exit code.

    Import of the textual-backed app is deferred to here so merely importing this
    package (e.g. to call `tui_available`) never requires textual.
    """
    from revenant_cli.tui.app import RevenantApp

    app = RevenantApp(
        loop=loop, workspace=workspace, model=model, mode=mode,
        session_saver=session_saver, history=history,
    )
    app.run()
    return 0
