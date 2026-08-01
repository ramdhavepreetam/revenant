from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

from nerva_agent.agent_tools import Tool, ToolParam, ToolRegistry, ToolError


def _registry() -> ToolRegistry:
    return ToolRegistry([
        Tool(
            "read_file", "Read a file from the workspace.",
            [ToolParam("path", "string", "Path to read")],
            run=lambda path: f"contents of {path}", parallel_safe=True,
        ),
        Tool(
            "write_file", "Write a file.",
            [ToolParam("path"), ToolParam("content")],
            run=lambda path, content: "written", mutating=True,
        ),
        Tool(
            "grep", "Search files.",
            [ToolParam("pattern"), ToolParam("path", "string", "", required=False)],
            run=lambda pattern, path=".": f"{pattern} in {path}", parallel_safe=True,
        ),
    ])


class ToolFlagTests(unittest.TestCase):
    def test_mutating_implies_approval(self):
        t = Tool("x", "d", run=lambda: "ok", mutating=True)
        self.assertTrue(t.requires_approval)

    def test_non_mutating_no_approval_by_default(self):
        t = Tool("x", "d", run=lambda: "ok")
        self.assertFalse(t.requires_approval)

    def test_signature_marks_optional(self):
        t = _registry().get("grep")
        self.assertEqual(t.signature(), "grep(pattern, path?)")


class RegistryTests(unittest.TestCase):
    def test_register_and_lookup(self):
        reg = _registry()
        self.assertEqual(len(reg), 3)
        self.assertIn("read_file", reg)
        self.assertIsNone(reg.get("nope"))

    def test_duplicate_name_rejected(self):
        reg = _registry()
        with self.assertRaises(ToolError):
            reg.register(Tool("read_file", "d", run=lambda: "x"))

    def test_render_docs_lists_flags(self):
        docs = _registry().render_docs()
        self.assertIn("read_file(path)", docs)
        self.assertIn("parallel-safe", docs)
        self.assertIn("asks approval", docs)  # write_file is mutating

    def test_native_schema_shape(self):
        schema = _registry().native_schema()
        wf = next(s for s in schema if s["function"]["name"] == "write_file")
        self.assertEqual(wf["type"], "function")
        self.assertEqual(set(wf["function"]["parameters"]["properties"]), {"path", "content"})
        self.assertEqual(wf["function"]["parameters"]["required"], ["path", "content"])

    def test_optional_arg_not_required_in_schema(self):
        schema = _registry().native_schema()
        g = next(s for s in schema if s["function"]["name"] == "grep")
        self.assertEqual(g["function"]["parameters"]["required"], ["pattern"])


class ArrayParamTests(unittest.TestCase):
    """W4b (ADR-0020): one array-of-objects param shape, scalar tools unchanged."""

    def _array_tool(self) -> Tool:
        return Tool(
            "apply_edits", "Apply edits atomically.",
            [ToolParam("edits", "array", "The edits.", item_fields=[
                ToolParam("path", "string", "File path"),
                ToolParam("old", "string", "Old text"),
                ToolParam("new", "string", "New text"),
            ])],
            run=lambda edits: "ok", mutating=True,
        )

    def test_array_param_native_schema(self):
        sch = self._array_tool().native_schema()["function"]["parameters"]
        edits = sch["properties"]["edits"]
        self.assertEqual(edits["type"], "array")
        self.assertEqual(edits["items"]["type"], "object")
        self.assertEqual(set(edits["items"]["properties"]), {"path", "old", "new"})
        self.assertEqual(edits["items"]["required"], ["path", "old", "new"])
        self.assertEqual(sch["required"], ["edits"])

    def test_array_param_doc_line_describes_shape(self):
        doc = self._array_tool().doc_line()
        self.assertIn("edits (array of {path: string, old: string, new: string})", doc)

    def test_array_param_validate_passes_list_through(self):
        t = self._array_tool()
        val = [{"path": "a.py", "old": "x", "new": "y"}]
        self.assertEqual(t.validate_args({"edits": val}), {"edits": val})

    def test_array_param_dispatches(self):
        reg = ToolRegistry([self._array_tool()])
        out = reg.dispatch("apply_edits", {"edits": [{"path": "a", "old": "x", "new": "y"}]})
        self.assertEqual(out, "ok")

    def test_scalar_tools_schema_byte_identical(self):
        # A scalar-only tool renders exactly as before the W4b relaxation.
        s = Tool("edit_file", "Edit",
                 [ToolParam("path", "string", "p"), ToolParam("old", "string", "o")],
                 run=lambda **k: "", mutating=True)
        props = s.native_schema()["function"]["parameters"]["properties"]
        self.assertEqual(props, {
            "path": {"type": "string", "description": "p"},
            "old": {"type": "string", "description": "o"},
        })


class DispatchTests(unittest.TestCase):
    def test_dispatch_ok(self):
        self.assertEqual(_registry().dispatch("read_file", {"path": "a.py"}), "contents of a.py")

    def test_unknown_tool_raises(self):
        with self.assertRaises(ToolError):
            _registry().dispatch("frobnicate", {})

    def test_missing_required_arg_raises(self):
        with self.assertRaises(ToolError):
            _registry().dispatch("read_file", {})

    def test_extra_args_ignored(self):
        # Weak models pass noise; extras are dropped, not fatal.
        self.assertEqual(
            _registry().dispatch("read_file", {"path": "a.py", "junk": 1}), "contents of a.py"
        )

    def test_optional_arg_defaults(self):
        self.assertEqual(_registry().dispatch("grep", {"pattern": "foo"}), "foo in .")

    def test_non_dict_args_raises(self):
        with self.assertRaises(ToolError):
            _registry().get("read_file").invoke(["not", "a", "dict"])

    def test_result_coerced_to_str(self):
        reg = ToolRegistry([Tool("count", "d", run=lambda: 42)])
        self.assertEqual(reg.dispatch("count", {}), "42")


if __name__ == "__main__":
    unittest.main()
