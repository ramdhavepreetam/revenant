from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent

import nerva_agent.agent_native_tools as agent_native_tools
from nerva_agent.agent_capacity import recommend, MachineInfo
from nerva_agent.agent_native_tools import supports_native_tools, clear_cache
from nerva_core.local_llm_writer import ChatConfig, LocalLLMError


def _machine(ram, cores=8, plat="darwin", arch="arm64"):
    return MachineInfo(ram, cores, plat, arch, plat == "darwin" and arch == "arm64")


class RecommendTests(unittest.TestCase):
    def test_context_scales_with_ram(self):
        low = recommend(machine=_machine(8), model_gb=5.0).max_context_tokens
        mid = recommend(machine=_machine(24), model_gb=5.0).max_context_tokens
        high = recommend(machine=_machine(64), model_gb=5.0).max_context_tokens
        self.assertLess(low, mid)
        self.assertLess(mid, high)

    def test_tiny_ram_gets_smallest_budget(self):
        r = recommend(machine=_machine(4), model_gb=5.0)
        self.assertLessEqual(r.max_context_tokens, 2000)

    def test_resident_true_when_room_for_two_models(self):
        # 24GB, a 4.4GB model: 4.4*2.2 + 6 = ~15.7 < 24 -> resident.
        self.assertTrue(recommend(machine=_machine(24), model_gb=4.4).keep_resident)

    def test_resident_false_for_big_model_on_modest_ram(self):
        # 24GB, an 8.4GB model: 8.4*2.2 + 6 = ~24.5 > 24 -> swap.
        self.assertFalse(recommend(machine=_machine(24), model_gb=8.4).keep_resident)

    def test_low_ram_never_resident(self):
        self.assertFalse(recommend(machine=_machine(8), model_gb=5.0).keep_resident)

    def test_steps_and_keep_recent_scale(self):
        big = recommend(machine=_machine(64), model_gb=5.0)
        small = recommend(machine=_machine(8), model_gb=5.0)
        self.assertGreaterEqual(big.max_steps, small.max_steps)
        self.assertGreaterEqual(big.keep_recent_steps, small.keep_recent_steps)

    def test_note_is_human_readable(self):
        note = recommend(machine=_machine(24), model_gb=4.4).note
        self.assertIn("RAM", note)
        self.assertIn("context", note)

    def test_unknown_model_size_conservative(self):
        # model_gb=0 -> uses a 5GB assumption; must not crash and returns a rec.
        r = recommend(machine=_machine(16), model_gb=0.0)
        self.assertGreater(r.max_context_tokens, 0)


class NativeDetectionTests(unittest.TestCase):
    def setUp(self):
        clear_cache()

    def tearDown(self):
        clear_cache()

    def _cfg(self, model="m"):
        return ChatConfig("ollama", "http://x", model, 0.2, 0.9, 1.0, 1, 32, 1, "")

    def test_detects_tool_capable_model(self):
        msg = {"role": "assistant", "content": "",
               "tool_calls": [{"function": {"name": "ping", "arguments": {"value": "x"}}}]}
        with mock.patch.object(agent_native_tools, "call_model_message", return_value=msg):
            self.assertTrue(supports_native_tools(self._cfg("qwen")))

    def test_detects_non_tool_model(self):
        msg = {"role": "assistant", "content": ""}  # no tool_calls
        with mock.patch.object(agent_native_tools, "call_model_message", return_value=msg):
            self.assertFalse(supports_native_tools(self._cfg("stheno")))

    def test_probe_error_returns_false(self):
        with mock.patch.object(agent_native_tools, "call_model_message",
                               side_effect=LocalLLMError("down")):
            self.assertFalse(supports_native_tools(self._cfg("x")))

    def test_result_is_cached(self):
        msg = {"role": "assistant", "content": "",
               "tool_calls": [{"function": {"name": "ping", "arguments": {}}}]}
        with mock.patch.object(agent_native_tools, "call_model_message", return_value=msg) as m:
            supports_native_tools(self._cfg("qwen"))
            supports_native_tools(self._cfg("qwen"))
        self.assertEqual(m.call_count, 1)  # probed once, then cached

    def test_force_reprobes(self):
        msg = {"role": "assistant", "content": "",
               "tool_calls": [{"function": {"name": "ping", "arguments": {}}}]}
        with mock.patch.object(agent_native_tools, "call_model_message", return_value=msg) as m:
            supports_native_tools(self._cfg("qwen"))
            supports_native_tools(self._cfg("qwen"), force=True)
        self.assertEqual(m.call_count, 2)


if __name__ == "__main__":
    unittest.main()
