"""Proactive context injection (H2, ADR-0013).

The code graph (ADR-0008) is pull-only: the model must decide to call
`defn_of` / `who_calls` / `pack_symbol_context`, and a weaker local model
frequently won't. This module turns that pull primitive into a push: pure,
model-free functions that take a `CodeGraph` (or `None`) and produce short
observation text the harness can inject *before* the model needs to ask.

Two independent pieces (both additive, both no-ops on missing input):

  - H2.1 `pre_edit_context`   — before an edit, surface the target symbol's
    definition + immediate callers (via `pack_symbol_context`), bounded by
    `max_callers`.
  - H2.2 `resolve_error_symbols` — on an error observation (tool error or a
    stack trace), extract candidate symbol names and auto-attach their
    definitions (file:line), deduplicated and capped.

Kept in the engine tier (`nerva-agent`), not the CLI, per ADR-0002: this is
reusable logic over a `CodeGraph`, with no model or CLI dependency. Never
raises — a malformed trace, an unknown symbol, or a missing graph all degrade
to "nothing to inject", never a crash.
"""
from __future__ import annotations

import re

from nerva_agent.code_graph.indexer import CodeGraph
from nerva_agent.code_graph.tools import pack_symbol_context

# --- H2.1 — pre-edit context -------------------------------------------------

# Tools whose args name a file being edited (mirrors _PATH_TOOLS in verify_hook.py
# and _SNAPSHOTTED_TOOLS in checkpoint.py — the same "which tools touch a path"
# convention used by the H1 seams this module sits alongside).
_EDIT_TOOLS = {"write_file", "edit_file"}

# A def/class line inside the `old` span of an edit, or anywhere in written
# content — used to recover the symbol actually being touched. Handles Python
# ("def foo(...)", "class Foo:") and the common brace-language shape ("function
# foo(", "class Foo {") well enough to be a useful hint; never required to match.
_DEF_LINE_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|func|fn)\s+([A-Za-z_]\w*)",
    re.MULTILINE,
)


def _candidate_symbols_for_edit(tool: str, args: dict) -> list[str]:
    """Symbol names implicated by an edit-tool call, best-effort, deduped."""
    if tool not in _EDIT_TOOLS:
        return []
    names: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        if name and name not in seen:
            seen.add(name)
            names.append(name)

    # The text most likely to *name* the symbol being changed: for edit_file it's
    # the span being replaced (what already exists); for write_file it's the new
    # content (a fresh or rewritten file).
    for key in ("old", "content", "new"):
        text = args.get(key)
        if isinstance(text, str) and text:
            for m in _DEF_LINE_RE.finditer(text):
                _add(m.group(1))

    return names


def pre_edit_context(
    graph: "CodeGraph | None",
    tool: str,
    args: dict,
    *,
    max_callers: int = 5,
) -> str:
    """H2.1: definition + immediate callers for the symbol(s) an edit targets.

    Returns "" (never raises) when: the graph is absent, the tool isn't an edit
    tool, no symbol name can be recovered from the args, or none resolve in the
    graph. Strictly additive — a caller that ignores an empty string sees no
    behavior change from today.
    """
    if graph is None:
        return ""
    try:
        candidates = _candidate_symbols_for_edit(tool, args)
        blocks: list[str] = []
        seen_symbols: set[str] = set()
        for name in candidates:
            if name in seen_symbols:
                continue
            block = pack_symbol_context(graph, name, max_callers=max_callers)
            if block:
                seen_symbols.add(name)
                blocks.append(block)
        return "\n\n".join(blocks)
    except Exception:  # noqa: BLE001 - injection must never break an edit
        return ""


# --- H2.2 — error-symbol resolution -----------------------------------------

# Light regexes over an error message / traceback to recover plausible symbol
# names, without a real parser (the message is free text, possibly truncated).
#   - Python traceback frames:      File "x.py", line 12, in some_func
#   - Python exception w/ a name:   NameError: name 'foo' is not defined
#                                    AttributeError: 'X' object has no attribute 'bar'
#   - A bare call-looking token:    some_func(...)
#   - Quoted identifiers:           'foo' / "foo" / `foo`
_TRACEBACK_FRAME_RE = re.compile(r'File "[^"]*", line \d+, in (\S+)')
_QUOTED_NAME_RE = re.compile(r"""['"`]([A-Za-z_]\w*)['"`]""")
_CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")

# Common stdlib/builtin/keyword-ish noise that would otherwise dominate results
# with useless "definitions" (they're rarely what broke, and cluttering the
# top of the cap with them defeats the purpose of capping).
_STOPWORDS = {
    "self", "cls", "None", "True", "False", "Exception", "Error", "raise",
    "return", "print", "str", "int", "float", "list", "dict", "set", "tuple",
    "len", "range", "open", "super", "object", "type", "module",
}


def extract_candidate_symbols(text: str, *, max_symbols: int = 8) -> list[str]:
    """Pull plausible symbol names out of an error message / traceback.

    Best-effort and order-preserving-deduped: traceback frame names first (most
    likely to be the actual failing symbol), then quoted identifiers, then
    bare call sites. Never raises on malformed input — an empty/garbage string
    just yields no candidates. Capped at `max_symbols` so a huge trace can't
    produce an unbounded candidate list before resolution even runs.
    """
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []

    def _add_all(names: "list[str]") -> None:
        for n in names:
            if n and n not in _STOPWORDS and n not in seen:
                seen.add(n)
                out.append(n)

    try:
        _add_all(_TRACEBACK_FRAME_RE.findall(text))
        _add_all(_QUOTED_NAME_RE.findall(text))
        _add_all(_CALL_RE.findall(text))
    except Exception:  # noqa: BLE001 - a pathological string must never crash resolution
        return out[:max_symbols]
    return out[:max_symbols]


def resolve_error_symbols(
    graph: "CodeGraph | None",
    text: str,
    *,
    max_symbols: int = 5,
) -> str:
    """H2.2: auto-attach `defn_of`-style results for symbols named in `text`.

    Extracts candidates (`extract_candidate_symbols`), resolves each against the
    graph, and formats the ones that hit as "file:line  qualname (kind)" lines —
    deduplicated by qualname and capped at `max_symbols` resolved hits (not just
    candidates) so a noisy trace can't flood the context window. Returns "" when
    the graph is absent, nothing parses, or nothing resolves.
    """
    if graph is None or not text:
        return ""
    try:
        candidates = extract_candidate_symbols(text, max_symbols=max_symbols * 4)
        lines: list[str] = []
        seen_qualnames: set[str] = set()
        for name in candidates:
            if len(lines) >= max_symbols:
                break
            for sym in graph.resolve(name):
                if sym.qualname in seen_qualnames:
                    continue
                seen_qualnames.add(sym.qualname)
                lines.append(f"{sym.file}:{sym.line}  {sym.qualname} ({sym.kind})")
                if len(lines) >= max_symbols:
                    break
        if not lines:
            return ""
        return "[code-graph: definitions for symbols in this error]\n" + "\n".join(lines)
    except Exception:  # noqa: BLE001 - error-resolution must never itself raise
        return ""
