"""Tool registry for the Revenant agent harness.

A `Tool` bundles a name, a human/model-readable description, a lightweight
parameter schema (used both to document the tool in the prompt and to build the
native Ollama `tools` array), and a `run` callable. Tools carry flags the loop
and the approval layer care about:

    parallel_safe      : read-only; multiple such calls may be batched/parallelized.
    requires_approval  : the human must confirm before this runs (mirrors Claude Code).
    mutating           : changes state (files, shell, memory) -> implies approval by default.

`ToolRegistry` holds a set of tools and knows how to (a) render them into a system
prompt block for the prompt-based protocol, (b) emit the native `tools` schema for
tool-capable models, and (c) dispatch a parsed call to the right tool with basic
argument validation.

Pure Python, no LLM dependency -- this module is the substrate the loop and both
front-ends (coding CLI, companion) build their tool sets on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class ToolError(RuntimeError):
    """Raised for registry/dispatch problems (unknown tool, bad args). The loop
    turns these into an observation the model can recover from, not a crash."""


@dataclass
class ToolParam:
    """One parameter of a tool.

    `type` is a JSON-schema scalar type name ("string", "integer", "boolean",
    "number") for a scalar param. W4b (ADR-0020) adds ONE structured shape: an
    array of flat objects — set `type="array"` and `item_fields` to a list of
    `ToolParam` describing each object's (scalar) fields. This is the single
    relaxation needed for a multi-edit tool taking `[{path, old, new}, …]`;
    arbitrary nesting stays out of scope (an item field is itself scalar).
    """

    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    # W4b: for type == "array", the scalar fields of each object element.
    item_fields: "list[ToolParam] | None" = None


def _param_schema(p: "ToolParam") -> dict[str, Any]:
    """JSON-schema fragment for one param. Scalar by default; W4b (ADR-0020)
    renders an array-of-objects when `type == "array"` + `item_fields` are set."""
    if p.type == "array" and p.item_fields:
        item_props = {
            f.name: {"type": f.type, **({"description": f.description} if f.description else {})}
            for f in p.item_fields
        }
        item_required = [f.name for f in p.item_fields if f.required]
        schema: dict[str, Any] = {
            "type": "array",
            "items": {"type": "object", "properties": item_props, "required": item_required},
        }
        if p.description:
            schema["description"] = p.description
        return schema
    return {"type": p.type, **({"description": p.description} if p.description else {})}


@dataclass
class Tool:
    """A single callable capability exposed to the model."""

    name: str
    description: str
    params: list[ToolParam] = field(default_factory=list)
    run: Callable[..., str] = None  # (**kwargs) -> str  (the observation text)
    parallel_safe: bool = False
    requires_approval: bool = False
    mutating: bool = False

    def __post_init__(self) -> None:
        if self.mutating and not self.requires_approval:
            # Mutating tools require approval unless a front-end explicitly clears it.
            self.requires_approval = True

    # --- Rendering ---------------------------------------------------------
    def signature(self) -> str:
        """`name(arg1, arg2?)` -- `?` marks optional args."""
        parts = [p.name + ("" if p.required else "?") for p in self.params]
        return f"{self.name}({', '.join(parts)})"

    def doc_line(self) -> str:
        """One line for the prompt's tool list, including arg docs and flags."""
        flags = []
        if self.requires_approval:
            flags.append("asks approval")
        if self.parallel_safe:
            flags.append("parallel-safe")
        flag_str = f"  [{'; '.join(flags)}]" if flags else ""
        line = f"- {self.signature()}: {self.description.strip()}{flag_str}"
        arg_docs = []
        for p in self.params:
            if p.type == "array" and p.item_fields:
                # W4b: describe the array-of-objects element shape so the
                # prompt-based path knows to emit a list of objects.
                shape = ", ".join(f"{f.name}: {f.type}" for f in p.item_fields)
                desc = f": {p.description}" if p.description else ""
                arg_docs.append(
                    f"    - {p.name} (array of {{{shape}}}{'' if p.required else ', optional'}){desc}".rstrip()
                )
            elif p.description:
                arg_docs.append(
                    f"    - {p.name} ({p.type}{'' if p.required else ', optional'}): {p.description}".rstrip()
                )
        return "\n".join([line, *arg_docs])

    def native_schema(self) -> dict[str, Any]:
        """OpenAI/Ollama-style function-tool schema for tool-capable models."""
        properties = {p.name: _param_schema(p) for p in self.params}
        required = [p.name for p in self.params if p.required]
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description.strip(),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }

    # --- Invocation --------------------------------------------------------
    def validate_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """Check required args are present and drop unknown keys. Returns the
        cleaned kwargs. Raises ToolError on a missing required arg."""
        if not isinstance(args, dict):
            raise ToolError(f"{self.name}: arguments must be an object, got {type(args).__name__}")
        known = {p.name for p in self.params}
        missing = [p.name for p in self.params if p.required and p.name not in args]
        if missing:
            raise ToolError(f"{self.name}: missing required argument(s): {', '.join(missing)}")
        # Keep only declared params; silently ignore extras (weak models add noise).
        return {k: v for k, v in args.items() if k in known}

    def invoke(self, args: dict[str, Any]) -> str:
        if self.run is None:
            raise ToolError(f"{self.name}: no run() implementation")
        clean = self.validate_args(args)
        result = self.run(**clean)
        return result if isinstance(result, str) else str(result)


class ToolRegistry:
    """An ordered collection of tools for one front-end (coding, companion, ...)."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ToolError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> list[str]:
        return list(self._tools)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def render_docs(self) -> str:
        """The tool catalogue for the prompt-based protocol's system block."""
        return "\n".join(tool.doc_line() for tool in self._tools.values())

    def native_schema(self) -> list[dict[str, Any]]:
        """The `tools` array for a native tool-calling request."""
        return [tool.native_schema() for tool in self._tools.values()]

    def dispatch(self, name: str, args: dict[str, Any]) -> str:
        """Run a tool by name. Raises ToolError for an unknown tool (the loop
        surfaces that back to the model as a recoverable observation)."""
        tool = self._tools.get(name)
        if tool is None:
            raise ToolError(
                f"unknown tool: {name!r}. Available: {', '.join(self._tools) or '(none)'}"
            )
        return tool.invoke(args)
