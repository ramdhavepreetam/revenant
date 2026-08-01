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

    # --- F5: LLM-backed compaction ----------------------------------------
    def test_compaction_uses_heuristic_without_summarizer(self):
        # No summarizer_config -> no model call, first-line recap used.
        loop = self._loop(max_context_tokens=300, keep_recent_steps=2)
        msgs = self._long_transcript(8)
        with mock.patch.object(agent_loop, "call_model") as m:
            out = loop._compact_messages(msgs)
        m.assert_not_called()
        # The heuristic recap keeps first-line gists (observation prefixes).
        self.assertIn("Result of read_file", out[2]["content"])

    def test_compaction_uses_llm_when_summarizer_set(self):
        loop = self._loop(max_context_tokens=300, keep_recent_steps=2,
                          summarizer_config=_config())
        msgs = self._long_transcript(8)
        with mock.patch.object(agent_loop, "call_model",
                               return_value="- read files\n- made an edit") as m:
            out = loop._compact_messages(msgs)
        m.assert_called_once()
        self.assertIn("made an edit", out[2]["content"])

    def test_compaction_falls_back_when_summarizer_raises(self):
        loop = self._loop(max_context_tokens=300, keep_recent_steps=2,
                          summarizer_config=_config())
        msgs = self._long_transcript(8)
        with mock.patch.object(agent_loop, "call_model",
                               side_effect=LocalLLMError("no server")):
            out = loop._compact_messages(msgs)
        # Must not crash; falls back to the heuristic recap.
        self.assertIn("Result of read_file", out[2]["content"])

    def test_compaction_falls_back_when_summarizer_returns_empty(self):
        loop = self._loop(max_context_tokens=300, keep_recent_steps=2,
                          summarizer_config=_config())
        msgs = self._long_transcript(8)
        with mock.patch.object(agent_loop, "call_model", return_value="   "):
            out = loop._compact_messages(msgs)
        self.assertIn("Result of read_file", out[2]["content"])

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


class EventModelTests(unittest.TestCase):
    """V0 (ADR-0017): additive AgentEvent fields, no breakage."""

    def test_new_fields_default_empty(self):
        ev = AgentEvent("action", tool="read_file")
        self.assertEqual(ev.agent, "")
        self.assertIsNone(ev.context)

    def test_context_info_carried_on_event(self):
        from nerva_agent.agent_loop import ContextInfo
        ci = ContextInfo(used_tokens=120, max_tokens=6000, folded=True)
        ev = AgentEvent("context", context=ci)
        self.assertEqual(ev.context.used_tokens, 120)
        self.assertTrue(ev.context.folded)

    def test_replace_stamps_agent(self):
        from dataclasses import replace
        ev = AgentEvent("action", tool="grep")
        self.assertEqual(replace(ev, agent="sub").agent, "sub")


class ContextEventTests(unittest.TestCase):
    """V1 (ADR-0017): the loop emits a context snapshot every step."""

    def test_context_event_each_step(self):
        reg = _registry([])
        script = _scripted(
            {"role": "assistant",
             "content": '```action\n{"tool": "read_file", "args": {"path": "a"}}\n```'},
            {"role": "assistant", "content": "done"},
        )
        seen = []
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            AgentLoop(_config(), reg, max_steps=5, max_context_tokens=100_000,
                      on_event=seen.append).run("go")
        ctx = [e for e in seen if e.kind == "context"]
        # one per model step (2 steps here); each carries a populated snapshot.
        self.assertEqual(len(ctx), 2)
        for e in ctx:
            self.assertIsNotNone(e.context)
            self.assertGreater(e.context.used_tokens, 0)
            self.assertEqual(e.context.max_tokens, 100_000)
            self.assertFalse(e.context.folded)  # never folded under a huge budget

    def test_folded_flag_set_only_on_fold_step(self):
        big = "x " * 400

        def fake(config, msgs, tools=None):
            step = getattr(fake, "n", 0) + 1
            fake.n = step
            if step < 6:
                return {"role": "assistant",
                        "content": '```action\n{"tool": "read_file", "args": {"path": "a"}}\n```'}
            return {"role": "assistant", "content": "done"}

        reg = ToolRegistry([
            Tool("read_file", "Read.", [ToolParam("path")], run=lambda path: big,
                 parallel_safe=True),
        ])
        seen = []
        with mock.patch.object(agent_loop, "call_model_message", side_effect=fake):
            AgentLoop(_config(), reg, max_steps=8, max_context_tokens=500,
                      keep_recent_steps=2, on_event=seen.append).run("go")
        ctx = [e for e in seen if e.kind == "context"]
        folded = [e for e in ctx if e.context.folded]
        # At least one fold happened, and every fold event coincides with a compact.
        self.assertTrue(folded)
        compact_steps = {e.step for e in seen if e.kind == "compact"}
        self.assertTrue({e.step for e in folded}.issubset(compact_steps))


class InterruptTests(unittest.TestCase):
    """V5 (ADR-0017): cooperative cancel between steps via should_stop."""

    def test_should_stop_halts_between_steps(self):
        reg = _registry([])
        # A script that would loop forever; should_stop cuts it after step 1.
        loop_msg = {"role": "assistant",
                    "content": '```action\n{"tool": "read_file", "args": {"path": "a"}}\n```'}
        calls = {"n": 0}

        def stop():
            calls["n"] += 1
            return calls["n"] > 2   # allow a couple steps, then interrupt

        seen = []
        with mock.patch.object(agent_loop, "call_model_message", return_value=loop_msg):
            result = AgentLoop(_config(), reg, max_steps=50,
                               on_event=seen.append).run("go", should_stop=stop)
        self.assertEqual(result.stopped_reason, "interrupted")
        self.assertTrue(any(e.kind == "interrupted" for e in seen))
        self.assertLess(result.steps, 50)  # stopped early, not at the cap

    def test_none_should_stop_is_unchanged(self):
        reg = _registry([])
        script = _scripted(
            {"role": "assistant",
             "content": '```action\n{"tool": "read_file", "args": {"path": "a"}}\n```'},
            {"role": "assistant", "content": "done"},
        )
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            result = AgentLoop(_config(), reg, max_steps=5).run("go")  # no should_stop
        self.assertEqual(result.stopped_reason, "final")


if __name__ == "__main__":
    unittest.main()


class AfterToolHookTests(unittest.TestCase):
    """H1.2: the after_tool hook fires post-dispatch for mutating tools and its
    return is appended to the observation the model sees next (ADR-0012)."""

    def _mutating_reg(self, calls):
        return ToolRegistry([
            Tool("write_file", "Write a file.",
                 [ToolParam("path"), ToolParam("content")],
                 run=lambda path, content: (calls.append(path) or "wrote it"),
                 mutating=True, requires_approval=False),
            Tool("read_file", "Read a file.", [ToolParam("path")],
                 run=lambda path: "contents", parallel_safe=True),
        ])

    def test_after_tool_appends_feedback_on_mutating_tool(self):
        seen = {}

        def after(tool, args, obs):
            seen["call"] = (tool, args["path"], obs)
            return "VERIFICATION FAILED (py_compile): boom"

        script = _scripted(
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "write_file",
                                          "arguments": {"path": "a.py", "content": "x"}}}]},
            {"role": "assistant", "content": "fixed."},
        )
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            result = AgentLoop(_config(), self._mutating_reg([]), max_steps=5,
                               after_tool=after).run("write a.py")
        # The hook saw the tool + its observation.
        self.assertEqual(seen["call"][0], "write_file")
        # The feedback is threaded into the transcript for the next turn.
        obs_msgs = [m["content"] for m in result.messages if m["role"] == "user"]
        self.assertTrue(any("VERIFICATION FAILED" in c for c in obs_msgs))

    def test_after_tool_not_called_for_read_only(self):
        calls = []

        def after(tool, args, obs):
            calls.append(tool)
            return None

        script = _scripted(
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "read_file", "arguments": {"path": "a.py"}}}]},
            {"role": "assistant", "content": "done."},
        )
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            AgentLoop(_config(), self._mutating_reg([]), max_steps=5, after_tool=after).run("read")
        self.assertEqual(calls, [])  # read_file is not mutating -> hook skipped

    def test_after_tool_none_appends_nothing(self):
        script = _scripted(
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "write_file",
                                          "arguments": {"path": "a.py", "content": "x"}}}]},
            {"role": "assistant", "content": "ok."},
        )
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            result = AgentLoop(_config(), self._mutating_reg([]), max_steps=5,
                               after_tool=lambda *a: None).run("write")
        obs = next(m["content"] for m in result.messages
                   if m["role"] == "user" and "write_file" in m["content"])
        self.assertIn("wrote it", obs)
        self.assertNotIn("VERIFICATION", obs)

    def test_after_tool_error_is_swallowed(self):
        def boom(tool, args, obs):
            raise RuntimeError("verifier crashed")

        script = _scripted(
            {"role": "assistant", "content": "",
             "tool_calls": [{"function": {"name": "write_file",
                                          "arguments": {"path": "a.py", "content": "x"}}}]},
            {"role": "assistant", "content": "ok."},
        )
        with mock.patch.object(agent_loop, "call_model_message", side_effect=script):
            result = AgentLoop(_config(), self._mutating_reg([]), max_steps=5,
                               after_tool=boom).run("write")
        # The run completes despite the hook raising.
        self.assertEqual(result.stopped_reason, "final")
