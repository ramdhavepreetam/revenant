from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "core", _ROOT / "tts", _ROOT / "apps"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agent_tools import Tool, ToolParam, ToolRegistry
from agent_protocol import (
    FinalAnswer,
    ToolCall,
    parse_action,
    render_system_block,
    looks_like_attempted_action,
    MALFORMED_ACTION_NUDGE,
)


def _registry() -> ToolRegistry:
    return ToolRegistry([
        Tool("read_file", "Read a file.", [ToolParam("path")], run=lambda path: "x", parallel_safe=True),
        Tool("write_file", "Write a file.", [ToolParam("path"), ToolParam("content")],
             run=lambda path, content: "ok", mutating=True),
    ])


class RenderTests(unittest.TestCase):
    def test_system_block_has_instructions_and_tools(self):
        block = render_system_block(_registry())
        self.assertIn("```action", block)
        self.assertIn("read_file(path)", block)
        self.assertIn("final answer", block.lower())

    def test_no_stray_format_braces(self):
        # The .format() template must not leak literal {} into the output.
        block = render_system_block(_registry())
        self.assertNotIn("{tool_docs}", block)


class NativeToolCallTests(unittest.TestCase):
    def test_ollama_shape_dict_args(self):
        msg = {"tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.py"}}}]}
        action = parse_action("", msg)
        self.assertEqual(action, ToolCall("read_file", {"path": "a.py"}))

    def test_openai_shape_string_args(self):
        msg = {"tool_calls": [{"function": {"name": "read_file", "arguments": '{"path": "b.py"}'}}]}
        action = parse_action("", msg)
        self.assertEqual(action, ToolCall("read_file", {"path": "b.py"}))

    def test_native_takes_precedence_over_text(self):
        msg = {"tool_calls": [{"function": {"name": "write_file", "arguments": {"path": "c", "content": "d"}}}]}
        action = parse_action("I will now write the file.", msg)
        self.assertEqual(action, ToolCall("write_file", {"path": "c", "content": "d"}))

    def test_first_call_only(self):
        msg = {"tool_calls": [
            {"function": {"name": "read_file", "arguments": {"path": "1"}}},
            {"function": {"name": "read_file", "arguments": {"path": "2"}}},
        ]}
        self.assertEqual(parse_action("", msg), ToolCall("read_file", {"path": "1"}))

    def test_bad_string_args_become_empty(self):
        msg = {"tool_calls": [{"function": {"name": "read_file", "arguments": "not json"}}]}
        self.assertEqual(parse_action("", msg), ToolCall("read_file", {}))


class FencedBlockTests(unittest.TestCase):
    def test_clean_fenced_action(self):
        text = 'Sure.\n```action\n{"tool": "read_file", "args": {"path": "x.py"}}\n```'
        self.assertEqual(parse_action(text), ToolCall("read_file", {"path": "x.py"}))

    def test_json_language_tag_also_accepted(self):
        text = '```json\n{"tool": "read_file", "args": {"path": "x.py"}}\n```'
        self.assertEqual(parse_action(text), ToolCall("read_file", {"path": "x.py"}))

    def test_fence_with_trailing_prose(self):
        text = '```action\n{"tool": "read_file", "args": {"path": "x.py"}}\n```\nLet me know!'
        self.assertEqual(parse_action(text), ToolCall("read_file", {"path": "x.py"}))

    def test_arguments_key_alias(self):
        # Some models emit "arguments" instead of "args".
        text = '```action\n{"tool": "read_file", "arguments": {"path": "x.py"}}\n```'
        self.assertEqual(parse_action(text), ToolCall("read_file", {"path": "x.py"}))


class SloppyOutputTests(unittest.TestCase):
    def test_no_fence_bare_json(self):
        text = 'Let me read it. {"tool": "read_file", "args": {"path": "x.py"}}'
        self.assertEqual(parse_action(text), ToolCall("read_file", {"path": "x.py"}))

    def test_single_quotes(self):
        text = "```action\n{'tool': 'read_file', 'args': {'path': 'x.py'}}\n```"
        self.assertEqual(parse_action(text), ToolCall("read_file", {"path": "x.py"}))

    def test_name_key_alias(self):
        text = '{"name": "read_file", "args": {"path": "x.py"}}'
        self.assertEqual(parse_action(text), ToolCall("read_file", {"path": "x.py"}))

    def test_missing_args_defaults_empty(self):
        text = '```action\n{"tool": "read_file"}\n```'
        self.assertEqual(parse_action(text), ToolCall("read_file", {}))

    def test_python_dict_mixed_quotes(self):
        # Regression: model emitted double-quoted keys but single-quoted values
        # that contain double quotes, e.g. {"old": ' "hi "'}. ast.literal_eval path.
        text = '```action\n{"tool": "edit_file", "args": {"path": "u.py", "old": \' "hi "\', "new": \' "bye "\'}}\n```'
        action = parse_action(text)
        self.assertEqual(action, ToolCall("edit_file", {"path": "u.py", "old": ' "hi "', "new": ' "bye "'}))

    def test_python_literals_true_none(self):
        text = "```action\n{'tool': 'x', 'args': {'flag': True, 'note': None}}\n```"
        action = parse_action(text)
        self.assertEqual(action, ToolCall("x", {"flag": True, "note": None}))

    def test_skips_non_tool_object_then_finds_tool_object(self):
        # A leading unrelated {} should not derail the scan.
        text = 'thinking {"note": "hmm"} then {"tool": "read_file", "args": {"path": "x"}}'
        self.assertEqual(parse_action(text), ToolCall("read_file", {"path": "x"}))


class FinalAnswerTests(unittest.TestCase):
    def test_plain_text_is_final(self):
        self.assertEqual(parse_action("The bug is fixed and tests pass."),
                         FinalAnswer("The bug is fixed and tests pass."))

    def test_empty_is_final_empty(self):
        self.assertEqual(parse_action(""), FinalAnswer(""))

    def test_json_without_tool_key_is_final(self):
        # A JSON object that isn't a tool call stays text.
        text = 'Here is data: {"result": 42, "ok": true}'
        action = parse_action(text)
        self.assertIsInstance(action, FinalAnswer)

    def test_final_answer_is_stripped(self):
        self.assertEqual(parse_action("  done  \n"), FinalAnswer("done"))


class NudgeHeuristicTests(unittest.TestCase):
    def test_detects_attempted_action_by_marker(self):
        self.assertTrue(looks_like_attempted_action("```action\noops", _registry()))

    def test_detects_by_tool_name(self):
        self.assertTrue(looks_like_attempted_action("I should call read_file now", _registry()))

    def test_plain_answer_not_attempted(self):
        self.assertFalse(looks_like_attempted_action("The task is complete.", _registry()))

    def test_nudge_text_exists(self):
        self.assertIn("action", MALFORMED_ACTION_NUDGE.lower())


if __name__ == "__main__":
    unittest.main()
