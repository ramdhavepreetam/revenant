from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent

import nerva_agent.agent_loop as agent_loop
from nerva_agent.agent_loop import AgentLoop, AgentEvent
from nerva_agent.agent_tools import Tool, ToolParam, ToolRegistry
from nerva_core.local_llm_writer import ChatConfig, LocalLLMError


def _config() -> ChatConfig:
    return ChatConfig(
        backend="ollama", base_url="http://x", model="m",
        temperature=0.2, top_p=0.9, repeat_penalty=1.05,
        min_tokens=1, max_tokens=64, context_messages=8, system_prompt="",
    )


def _registry(calls: list):
    return ToolRegistry([
        Tool("read_file", "Read a file.", [ToolParam("path")],
             run=lambda path: (calls.append(path) or f"contents of {path}"),
             parallel_safe=True),
    ])


def _scripted(*messages):
    """Return a fake call_model_message that yields the given messages in order."""
    it = iter(messages)

    def fake(config, msgs, tools=None):
        return next(it)
    return fake


class ContextManagementTests(unittest.TestCase):
    def _loop(self, **kw):
        return AgentLoop(_config(), _registry([]), **kw)

    def _long_transcript(self, pairs: int):
        msgs = [{"role": "system", "content": "SYS " * 60}, {"role": "user", "content": "GOAL"}]
        for i in range(pairs):
            msgs.append({"role": "assistant", "content": f"(calling read_file) step {i} " + "blah " * 50})
            msgs.append({"role": "user", "content": f"Result of read_file:\nobservation {i} " + "data " * 50})
        return msgs

    def test_under_budget_is_untouched(self):
        loop = self._loop(max_context_tokens=100_000)
        msgs = self._long_transcript(5)
        self.assertEqual(loop._compact_messages(msgs), msgs)

    def test_compaction_reduces_and_preserves_essentials(self):
        loop = self._loop(max_context_tokens=300, keep_recent_steps=2)
        msgs = self._long_transcript(8)
        out = loop._compact_messages(msgs)
        self.assertLess(len(out), len(msgs))
        self.assertEqual(out[0]["role"], "system")
        self.assertTrue(out[0]["content"].startswith("SYS"))
        self.assertEqual(out[1]["content"], "GOAL")
        self.assertIn("compacted", out[2]["content"].lower())
        # The last 2 step-pairs (4 msgs) are kept verbatim.
        self.assertIn("observation 7", out[-1]["content"])
        self.assertIn("observation 6", out[-3]["content"])

    def test_not_enough_middle_to_fold(self):
        # Only the recent-N pairs exist -> nothing to fold even if over budget.
        loop = self._loop(max_context_tokens=1, keep_recent_steps=3)
        msgs = self._long_transcript(3)  # exactly keep_recent_steps pairs
        self.assertEqual(loop._compact_messages(msgs), msgs)

    def test_compaction_fires_during_run(self):
        # A run that grows past budget must compact before a later model call.
        seen_lens = []
        reg = _registry([])
        big = "x " * 400  # each observation is chunky

        def fake(config, msgs, tools=None):
            seen_lens.append(len(msgs))
            step = len(seen_lens)
            if step < 6:
                return {"role": "assistant",
                        "content": '```action\n{"tool": "read_file", "args": {"path": "a"}}\n```'}
            return {"role": "assistant", "content": "done"}

        # Make read_file return a big observation so context grows fast.
        reg = ToolRegistry([
            Tool("read_file", "Read.", [ToolParam("path")], run=lambda path: big, parallel_safe=True),
        ])
        events = []
        with mock.patch.object(agent_loop, "call_model_message", side_effect=fake):
            AgentLoop(_config(), reg, max_steps=8, max_context_tokens=500,
                      keep_recent_steps=2, on_event=events.append).run("go")
        # A compact event fired, and message count stopped growing unboundedly.
        self.assertTrue(any(e.kind == "compact" for e in events))
        self.assertLessEqual(max(seen_lens), 8)  # bounded, not step*2+2


class LoopTests(unittest.TestCase):
    def test_prompt_based_tool_then_final(self):
        calls = []
        reg = _registry(calls)
        script = _scripted(
            {"role": "assistant",
             "content": '```action\n{"tool": "read_file", "args": {"path": "a.py"}}\n```'},
            {"role": "assistant", "content": "The file defines alpha()."},
        )
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            result = AgentLoop(_config(), reg, max_steps=5).run("what is a.py")
        self.assertEqual(result.stopped_reason, "final")
        self.assertEqual(result.answer, "The file defines alpha().")
        self.assertEqual(calls, ["a.py"])  # the tool actually ran
        self.assertEqual(result.steps, 2)

    def test_native_tool_call(self):
        calls = []
        reg = _registry(calls)
        script = _scripted(
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "b.py"}}}]},
            {"role": "assistant", "content": "Done."},
        )
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            result = AgentLoop(_config(), reg, max_steps=5).run("read b")
        self.assertEqual(result.answer, "Done.")
        self.assertEqual(calls, ["b.py"])

    def test_immediate_final_answer(self):
        reg = _registry([])
        script = _scripted({"role": "assistant", "content": "42 is the answer."})
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            result = AgentLoop(_config(), reg, max_steps=5).run("q")
        self.assertEqual(result.stopped_reason, "final")
        self.assertEqual(result.steps, 1)

    def test_result_carries_transcript(self):
        reg = _registry([])
        script = _scripted({"role": "assistant", "content": "the answer"})
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            result = AgentLoop(_config(), reg, max_steps=5).run("q1")
        # messages holds the full transcript incl. the final answer, for threading.
        roles = [m["role"] for m in result.messages]
        self.assertEqual(roles[0], "system")
        self.assertEqual(result.messages[1], {"role": "user", "content": "q1"})
        self.assertEqual(result.messages[-1], {"role": "assistant", "content": "the answer"})

    def test_run_with_history_continues_conversation(self):
        reg = _registry([])
        script = _scripted(
            {"role": "assistant", "content": "first answer"},
            {"role": "assistant", "content": "second answer"},
        )
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            loop = AgentLoop(_config(), reg, max_steps=5)
            r1 = loop.run("q1")
            r2 = loop.run("q2", history=r1.messages)
        # The second run kept the first turn's context and added only ONE system prompt.
        self.assertEqual(sum(1 for m in r2.messages if m["role"] == "system"), 1)
        contents = [m["content"] for m in r2.messages]
        self.assertIn("q1", contents)
        self.assertIn("first answer", contents)
        self.assertIn("q2", contents)
        self.assertEqual(r2.messages[-1], {"role": "assistant", "content": "second answer"})

    def test_unknown_tool_is_recoverable_observation(self):
        # An unknown tool should NOT crash the loop; it becomes an ERROR observation
        # and the model gets another turn.
        reg = _registry([])
        script = _scripted(
            {"role": "assistant", "content": '```action\n{"tool": "frobnicate", "args": {}}\n```'},
            {"role": "assistant", "content": "Okay, giving up gracefully."},
        )
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            result = AgentLoop(_config(), reg, max_steps=5).run("go")
        self.assertEqual(result.stopped_reason, "final")
        # An observation event carrying the error was emitted.
        obs = [e for e in result.events if e.kind == "observation"]
        self.assertTrue(any("unknown tool" in e.text.lower() for e in obs))

    def test_max_steps_cap(self):
        # A model that always calls the tool never finishes -> hits the cap.
        reg = _registry([])
        loop_msg = {"role": "assistant",
                    "content": '```action\n{"tool": "read_file", "args": {"path": "x"}}\n```'}
        with mock.patch.object(agent_loop, "call_model_message", return_value=loop_msg):
            result = AgentLoop(_config(), reg, max_steps=3).run("loop forever")
        self.assertEqual(result.stopped_reason, "max_steps")
        self.assertEqual(result.steps, 3)

    def test_llm_error_returns_error_result(self):
        reg = _registry([])
        with mock.patch.object(agent_loop, "call_model_message",
                               side_effect=LocalLLMError("no server")):
            result = AgentLoop(_config(), reg, max_steps=5).run("q")
        self.assertEqual(result.stopped_reason, "error")

    def test_events_streamed_to_callback(self):
        reg = _registry([])
        seen: list[AgentEvent] = []
        script = _scripted(
            {"role": "assistant", "content": '```action\n{"tool": "read_file", "args": {"path": "a"}}\n```'},
            {"role": "assistant", "content": "done"},
        )
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            AgentLoop(_config(), reg, max_steps=5, on_event=seen.append).run("q")
        kinds = [e.kind for e in seen]
        self.assertIn("action", kinds)
        self.assertIn("observation", kinds)
        self.assertIn("final", kinds)

    def test_approval_denied_becomes_declined_observation(self):
        # A mutating tool that the user declines must NOT run; the model gets a
        # "declined" observation and continues.
        ran = []
        reg = ToolRegistry([
            Tool("write_file", "Write.", [ToolParam("path"), ToolParam("content")],
                 run=lambda path, content: (ran.append(path) or "written"), mutating=True),
        ])
        script = _scripted(
            {"role": "assistant", "content": '```action\n{"tool": "write_file", "args": {"path": "x", "content": "y"}}\n```'},
            {"role": "assistant", "content": "Understood, not writing."},
        )
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            result = AgentLoop(_config(), reg, max_steps=5, approve=lambda t, a: False).run("go")
        self.assertEqual(ran, [])  # tool never ran
        obs = [e for e in result.events if e.kind == "observation"]
        self.assertTrue(any("declined" in e.text.lower() for e in obs))
        self.assertEqual(result.answer, "Understood, not writing.")

    def test_approval_granted_runs_tool(self):
        ran = []
        reg = ToolRegistry([
            Tool("write_file", "Write.", [ToolParam("path"), ToolParam("content")],
                 run=lambda path, content: (ran.append(path) or "written"), mutating=True),
        ])
        script = _scripted(
            {"role": "assistant", "content": '```action\n{"tool": "write_file", "args": {"path": "x", "content": "y"}}\n```'},
            {"role": "assistant", "content": "Done."},
        )
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            AgentLoop(_config(), reg, max_steps=5, approve=lambda t, a: True).run("go")
        self.assertEqual(ran, ["x"])

    def test_auto_approve_skips_hook(self):
        ran = []
        called = []
        reg = ToolRegistry([
            Tool("write_file", "Write.", [ToolParam("path"), ToolParam("content")],
                 run=lambda path, content: (ran.append(path) or "written"), mutating=True),
        ])
        script = _scripted(
            {"role": "assistant", "content": '```action\n{"tool": "write_file", "args": {"path": "x", "content": "y"}}\n```'},
            {"role": "assistant", "content": "Done."},
        )
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            AgentLoop(_config(), reg, max_steps=5, auto_approve=True,
                      approve=lambda t, a: called.append(1) or True).run("go")
        self.assertEqual(ran, ["x"])       # ran
        self.assertEqual(called, [])        # hook was NOT consulted under yolo

    def test_read_only_tool_never_prompts(self):
        # A non-mutating tool must not trigger the approval hook.
        called = []
        reg = _registry([])  # read_file is parallel_safe/non-mutating
        script = _scripted(
            {"role": "assistant", "content": '```action\n{"tool": "read_file", "args": {"path": "a"}}\n```'},
            {"role": "assistant", "content": "done"},
        )
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            AgentLoop(_config(), reg, max_steps=5, approve=lambda t, a: called.append(1) or True).run("q")
        self.assertEqual(called, [])

    def test_auto_native_tools_probes_when_none(self):
        # use_native_tools=None -> loop asks supports_native_tools once.
        import nerva_agent.agent_native_tools as agent_native_tools
        reg = _registry([])
        script = _scripted({"role": "assistant", "content": "done"})
        with mock.patch.object(agent_native_tools, "supports_native_tools", return_value=True) as probe, \
             mock.patch.object(agent_loop, "call_model_message", side_effect=script) as call:
            AgentLoop(_config(), reg, max_steps=2, use_native_tools=None).run("q")
        probe.assert_called_once()
        # tools schema was passed because probe said True
        _, kwargs = call.call_args
        self.assertIsNotNone(kwargs.get("tools"))

    def test_explicit_false_skips_probe(self):
        import nerva_agent.agent_native_tools as agent_native_tools
        reg = _registry([])
        script = _scripted({"role": "assistant", "content": "done"})
        with mock.patch.object(agent_native_tools, "supports_native_tools") as probe, \
             mock.patch.object(agent_loop, "call_model_message", side_effect=script) as call:
            AgentLoop(_config(), reg, max_steps=2, use_native_tools=False).run("q")
        probe.assert_not_called()
        _, kwargs = call.call_args
        self.assertIsNone(kwargs.get("tools"))

    def test_double_wrapped_native_args_unwrapped(self):
        # Regression: a model that double-wraps {tool,args:{tool,args:{...}}} via
        # the native path must still call the tool with the inner args.
        calls = []
        reg = _registry(calls)
        script = _scripted(
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "read_file",
                                          "arguments": {"tool": "read_file", "args": {"path": "deep.py"}}}}]},
            {"role": "assistant", "content": "done"},
        )
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            AgentLoop(_config(), reg, max_steps=5).run("q")
        self.assertEqual(calls, ["deep.py"])


if __name__ == "__main__":
    unittest.main()
