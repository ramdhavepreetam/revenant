"""Graph-backed retrieval tools for the agent (F14.2, ADR-0008).

These wrap a `CodeGraph` as read-only registry tools so the agent can ask
structural questions — "what calls this?", "where is this defined?", "what
imports this file?", "what's the blast radius of changing this?" — instead of
guessing from grep. All are `parallel_safe=True, mutating=False`: they only read
the index.

Each tool returns a short text observation (file:line + a snippet where useful).
When a symbol is unknown, the observation says so and suggests `search` — a
degrade, never a crash (ADR-0008).
"""
from __future__ import annotations

from pathlib import Path

from nerva_agent.agent_tools import Tool, ToolParam
from nerva_agent.code_graph.indexer import CodeGraph, Symbol

# Bound traversal/result sizes so a huge repo can't produce a wall of output.
_MAX_RESULTS = 40
_MAX_IMPACT_DEPTH = 4


def _snippet(graph: CodeGraph, sym: Symbol) -> str:
    """The definition line of a symbol, if readable (best-effort)."""
    try:
        lines = (graph.root / sym.file).read_text(encoding="utf-8", errors="replace").splitlines()
        return lines[sym.line - 1].strip() if 0 < sym.line <= len(lines) else ""
    except OSError:
        return ""


def _fmt(sym: Symbol) -> str:
    return f"{sym.file}:{sym.line}  {sym.qualname} ({sym.kind})"


def _unknown(name: str) -> str:
    return (f"No symbol named {name!r} in the code graph. It may be external, "
            f"dynamically defined, or in an unindexed language — try the `search` tool.")


def build_code_graph_tools(graph: CodeGraph) -> list[Tool]:
    """Read-only tools bound to `graph`, for the agent's registry."""

    def defn_of(symbol: str) -> str:
        syms = graph.resolve(symbol)
        if not syms:
            return _unknown(symbol)
        out = []
        for s in syms[:_MAX_RESULTS]:
            snip = _snippet(graph, s)
            out.append(_fmt(s) + (f"\n    {snip}" if snip else ""))
        return "\n".join(out)

    def who_calls(symbol: str) -> str:
        callers = graph.callers_of(symbol)
        if not callers:
            # Distinguish "unknown symbol" from "known but uncalled".
            if not graph.resolve(symbol):
                return _unknown(symbol)
            return f"No indexed callers of {symbol!r} (it may be unused or called dynamically)."
        lines = [_fmt(s) for s in callers[:_MAX_RESULTS]]
        more = "" if len(callers) <= _MAX_RESULTS else f"\n… and {len(callers) - _MAX_RESULTS} more"
        return f"{len(callers)} caller(s) of {symbol!r}:\n" + "\n".join(lines) + more

    def neighbors(path: str) -> str:
        fnode = graph.files.get(path)
        if fnode is None:
            return (f"{path!r} is not in the code graph. Use a workspace-relative "
                    f"path to an indexed source file.")
        imports = ", ".join(fnode.imports[:_MAX_RESULTS]) or "(none)"
        # Files that import this file's module basename (a cheap reverse edge).
        module = Path(path).stem
        importers = graph.importers_of(module)[:_MAX_RESULTS]
        importers_s = ", ".join(importers) or "(none indexed)"
        symbols = ", ".join(fnode.symbols[:_MAX_RESULTS]) or "(none)"
        return (f"{path}\n"
                f"  imports: {imports}\n"
                f"  imported-by: {importers_s}\n"
                f"  defines: {symbols}")

    def impact_of(symbol: str) -> str:
        if not graph.resolve(symbol):
            return _unknown(symbol)
        # Transitive callers, bounded depth (blast radius of a change).
        seen: dict[str, int] = {}
        frontier = [(symbol, 0)]
        while frontier:
            name, depth = frontier.pop()
            if depth >= _MAX_IMPACT_DEPTH:
                continue
            for caller in graph.callers_of(name):
                if caller.qualname not in seen:
                    seen[caller.qualname] = depth + 1
                    frontier.append((caller.name, depth + 1))
        if not seen:
            return f"Changing {symbol!r} has no indexed callers (blast radius: just itself)."
        ordered = sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))[:_MAX_RESULTS]
        lines = [f"  [{depth}] {q}" for q, depth in ordered]
        return (f"Blast radius of {symbol!r} — {len(seen)} transitive caller(s), "
                f"by hop distance:\n" + "\n".join(lines))

    return [
        Tool(name="defn_of",
             description="Find where a symbol (function/class/method) is defined. "
                         "Returns file:line and the definition line.",
             params=[ToolParam("symbol", "string", "Bare or qualified name, e.g. 'dispatch' or 'Registry.dispatch'.")],
             run=defn_of, parallel_safe=True, mutating=False),
        Tool(name="who_calls",
             description="List the places that call a given function/method — real "
                         "call sites from the code graph, not a text search.",
             params=[ToolParam("symbol", "string", "The called name to look up.")],
             run=who_calls, parallel_safe=True, mutating=False),
        Tool(name="neighbors",
             description="Show a file's graph neighborhood: what it imports, what "
                         "imports it, and the symbols it defines.",
             params=[ToolParam("path", "string", "Workspace-relative path to an indexed file.")],
             run=neighbors, parallel_safe=True, mutating=False),
        Tool(name="impact_of",
             description="Estimate the blast radius of changing a symbol: its "
                         "transitive callers, grouped by how many hops away they are.",
             params=[ToolParam("symbol", "string", "The symbol whose change-impact to assess.")],
             run=impact_of, parallel_safe=True, mutating=False),
    ]
