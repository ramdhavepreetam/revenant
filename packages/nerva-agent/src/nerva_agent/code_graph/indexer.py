"""Code-graph indexer: a symbol/dependency graph of a workspace (F14.1, ADR-0008).

Grep finds text; a graph finds *meaning* — "what calls this function", "what
imports this module", "what breaks if I change this signature". This indexer
walks a workspace and builds that graph so the retrieval tools (tools.py) can
answer structural questions instead of guessing from substring matches.

Parsing strategy (decision in ADR-0008): **stdlib `ast`, Python-first** — no
tree-sitter, no new dependency, fully accurate for Python. Non-Python files get
a best-effort regex import/def pass. tree-sitter could later slot in behind this
same interface without changing the tools.

Graph shape:
    nodes:  file (a source path), symbol (function/class/method, qualified)
    edges:  defines (file→symbol), imports (file→module), calls (symbol→name)

Everything degrades: a file that fails to parse is skipped (recorded, never
crashes the index); ignore globs (agent_ignore) keep vendored/generated code out.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from nerva_agent.agent_ignore import load_ignore_matcher

# Extensions we attempt to index. Python is parsed exactly; the rest get the
# regex fallback (imports/defs only).
_PY_EXT = {".py"}
_FALLBACK_EXT = {".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb"}
_INDEXABLE_EXT = _PY_EXT | _FALLBACK_EXT

# Cap per-file size so a giant generated file can't dominate indexing time.
_MAX_FILE_BYTES = 512_000


@dataclass
class Symbol:
    """A defined function/class/method."""

    name: str            # bare name, e.g. "dispatch"
    qualname: str        # dotted within-file path, e.g. "ToolRegistry.dispatch"
    kind: str            # "function" | "class" | "method"
    file: str            # workspace-relative path
    line: int            # 1-based definition line
    calls: list[str] = field(default_factory=list)  # bare names called in its body


@dataclass
class FileNode:
    """One indexed source file."""

    path: str                                   # workspace-relative
    language: str                               # "python" | "other"
    imports: list[str] = field(default_factory=list)   # imported module names
    symbols: list[str] = field(default_factory=list)   # qualnames defined here
    parse_error: str = ""                       # non-empty if it failed to parse


@dataclass
class CodeGraph:
    """The indexed workspace. Cheap in-memory adjacency + name lookups."""

    root: Path
    files: dict[str, FileNode] = field(default_factory=dict)
    symbols: dict[str, Symbol] = field(default_factory=dict)  # qualname -> Symbol
    # name -> qualnames defining it (a bare name may resolve to several symbols).
    _by_name: dict[str, list[str]] = field(default_factory=dict)

    # --- lookups used by the retrieval tools -------------------------------
    def resolve(self, name: str) -> list[Symbol]:
        """All symbols matching a bare or qualified name."""
        if name in self.symbols:
            return [self.symbols[name]]
        return [self.symbols[q] for q in self._by_name.get(name, [])]

    def callers_of(self, name: str) -> list[Symbol]:
        """Symbols whose body calls `name` (bare-name match)."""
        target = name.split(".")[-1]
        return [s for s in self.symbols.values() if target in s.calls]

    def importers_of(self, module: str) -> list[str]:
        """Files that import a module whose name contains `module`."""
        out = []
        for f in self.files.values():
            if any(module == imp or imp.endswith("." + module) or module in imp
                   for imp in f.imports):
                out.append(f.path)
        return sorted(out)

    def stats(self) -> dict:
        return {
            "files": len(self.files),
            "symbols": len(self.symbols),
            "parse_errors": sum(1 for f in self.files.values() if f.parse_error),
        }


# --- Python parsing (exact) --------------------------------------------------

class _PyVisitor(ast.NodeVisitor):
    """Collect imports + (nested-aware) symbol defs with their call names."""

    def __init__(self, rel_path: str) -> None:
        self.rel_path = rel_path
        self.imports: list[str] = []
        self.symbols: list[Symbol] = []
        self._scope: list[str] = []  # enclosing class/func names for qualname

    def _qual(self, name: str) -> str:
        return ".".join([*self._scope, name])

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.imports.append(node.module)
        self.generic_visit(node)

    def _handle_def(self, node, kind: str) -> None:
        qual = self._qual(node.name)
        # methods = a function whose immediate enclosing scope is a class; we
        # can't cheaply know that here, so mark as "method" when nested at all.
        actual_kind = "method" if (kind == "function" and self._scope) else kind
        sym = Symbol(
            name=node.name, qualname=qual, kind=actual_kind,
            file=self.rel_path, line=node.lineno, calls=_calls_in(node),
        )
        self.symbols.append(sym)
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._handle_def(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._handle_def(node, "function")

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._handle_def(node, "class")


def _calls_in(node: ast.AST) -> list[str]:
    """Bare names called anywhere inside `node` (deduped, order-stable)."""
    seen: dict[str, None] = {}
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                seen.setdefault(func.id, None)
            elif isinstance(func, ast.Attribute):
                seen.setdefault(func.attr, None)
    return list(seen)


# --- regex fallback (non-Python) --------------------------------------------

_JS_IMPORT_RE = re.compile(r"""(?:import\s.*?from\s+|require\()\s*['"]([^'"]+)['"]""")
_DEF_RE = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|def|func|fn)\s+([A-Za-z_]\w*)",
                     re.MULTILINE)


def _index_fallback(rel_path: str, text: str) -> tuple[FileNode, list[Symbol]]:
    imports = _JS_IMPORT_RE.findall(text)
    symbols: list[Symbol] = []
    for m in _DEF_RE.finditer(text):
        name = m.group(1)
        line = text.count("\n", 0, m.start()) + 1
        symbols.append(Symbol(name=name, qualname=name, kind="function",
                              file=rel_path, line=line))
    fnode = FileNode(path=rel_path, language="other",
                     imports=list(dict.fromkeys(imports)),
                     symbols=[s.qualname for s in symbols])
    return fnode, symbols


# --- the indexer -------------------------------------------------------------

def _index_file(root: Path, rel_path: str) -> tuple[FileNode, list[Symbol]]:
    full = root / rel_path
    try:
        if full.stat().st_size > _MAX_FILE_BYTES:
            return FileNode(path=rel_path, language="other",
                            parse_error="skipped: file too large"), []
        text = full.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return FileNode(path=rel_path, language="other", parse_error=str(exc)), []

    ext = full.suffix
    if ext in _PY_EXT:
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            return FileNode(path=rel_path, language="python",
                            parse_error=f"syntax error: {exc}"), []
        v = _PyVisitor(rel_path)
        v.visit(tree)
        fnode = FileNode(path=rel_path, language="python",
                         imports=list(dict.fromkeys(v.imports)),
                         symbols=[s.qualname for s in v.symbols])
        return fnode, v.symbols
    return _index_fallback(rel_path, text)


def build_index(root: str | Path) -> CodeGraph:
    """Index a workspace into a CodeGraph, respecting ignore globs.

    Never raises: unreadable/unparseable files are recorded with a parse_error
    and skipped. Only files with an indexable extension are visited.
    """
    root = Path(root).resolve()
    graph = CodeGraph(root=root)
    matcher = load_ignore_matcher(root)

    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if path.suffix not in _INDEXABLE_EXT:
            continue
        rel = path.relative_to(root).as_posix()
        # Respect ignore globs (vendored/generated code stays out).
        if matcher.match(rel, is_dir=False):
            continue
        fnode, symbols = _index_file(root, rel)
        graph.files[rel] = fnode
        for sym in symbols:
            # Last definition of a qualname wins; record every name -> qualname.
            graph.symbols[sym.qualname] = sym
            graph._by_name.setdefault(sym.name, [])
            if sym.qualname not in graph._by_name[sym.name]:
                graph._by_name[sym.name].append(sym.qualname)
    return graph
