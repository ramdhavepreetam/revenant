from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "core", _ROOT / "tts", _ROOT / "apps"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import agent_loop
from agent_loop import AgentLoop
from agent_tools import ToolRegistry
from agent_companion_tools import build_companion_tools
from local_llm_writer import ChatConfig


class FakeMemory:
    def __init__(self, hits=None):
        self.hits = hits or []
        self.noted = []

    def recall(self, query, limit=5, companion_id=""):
        return self.hits

    def remember_note(self, cid, cat, content):
        self.noted.append((cat, content))


class FakePersonalMemory:
    def __init__(self):
        self.saved = []

    def create_memory(self, cid, category, content, *, status="active",
                      pinned=False, confidence=1.0, source="", source_conversation_id=""):
        m = {"companion_id": cid, "category": category, "content": content, "status": status}
        self.saved.append(m)
        return m


class FakeStore:
    def __init__(self):
        self.episodes = []

    def add_episode(self, cid, summary, conversation_id=""):
        self.episodes.append((cid, summary))
        return "ep-1"


def _reg(mem, pm, store, cid="eros"):
    return ToolRegistry(build_companion_tools(mem, pm, store, cid))


class CompanionToolTests(unittest.TestCase):
    def test_no_filesystem_or_shell_tools(self):
        reg = _reg(FakeMemory(), FakePersonalMemory(), FakeStore())
        self.assertEqual(set(reg.names()), {"memory_search", "memory_save", "set_reminder"})
        for forbidden in ("read_file", "write_file", "edit_file", "run_bash", "glob", "grep"):
            self.assertNotIn(forbidden, reg)

    def test_memory_search_returns_hits(self):
        reg = _reg(FakeMemory(hits=["likes goa", "has a dog"]), FakePersonalMemory(), FakeStore())
        out = reg.dispatch("memory_search", {"query": "trips"})
        self.assertIn("goa", out)

    def test_memory_search_empty(self):
        reg = _reg(FakeMemory(hits=[]), FakePersonalMemory(), FakeStore())
        self.assertIn("nothing", reg.dispatch("memory_search", {"query": "x"}).lower())

    def test_memory_save_preference_active_and_indexed(self):
        mem, pm = FakeMemory(), FakePersonalMemory()
        reg = _reg(mem, pm, FakeStore())
        reg.dispatch("memory_save", {"category": "preference", "content": "loves spicy food"})
        self.assertEqual(pm.saved[-1]["status"], "active")
        self.assertEqual(mem.noted[-1], ("preference", "loves spicy food"))

    def test_boundary_saved_pending_not_indexed(self):
        mem, pm = FakeMemory(), FakePersonalMemory()
        reg = _reg(mem, pm, FakeStore())
        out = reg.dispatch("memory_save", {"category": "boundary", "content": "no calls after 10pm"})
        self.assertEqual(pm.saved[-1]["status"], "pending")     # human-gated
        self.assertEqual(mem.noted, [])                          # NOT indexed
        self.assertIn("review", out.lower())

    def test_unknown_category_falls_back_to_preference(self):
        pm = FakePersonalMemory()
        reg = _reg(FakeMemory(), pm, FakeStore())
        reg.dispatch("memory_save", {"category": "weird_thing", "content": "x"})
        self.assertEqual(pm.saved[-1]["category"], "preference")

    def test_set_reminder_creates_episode(self):
        store = FakeStore()
        reg = _reg(FakeMemory(), FakePersonalMemory(), store)
        reg.dispatch("set_reminder", {"text": "ask about her interview"})
        self.assertEqual(len(store.episodes), 1)
        self.assertIn("interview", store.episodes[0][1])


class CompanionLoopTests(unittest.TestCase):
    def _config(self):
        return ChatConfig("ollama", "u", "stheno", 0.85, 0.9, 1.08, 1, 64, 8, "")

    def test_companion_saves_memory_mid_turn(self):
        # A scripted companion model that first saves a memory, then replies.
        mem, pm, store = FakeMemory(), FakePersonalMemory(), FakeStore()
        reg = _reg(mem, pm, store)
        it = iter([
            {"role": "assistant",
             "content": '```action\n{"tool": "memory_save", "args": {"category": "preference", "content": "loves Goa"}}\n```'},
            {"role": "assistant", "content": "Locked it in — I won't forget Goa."},
        ])
        with mock.patch.object(agent_loop, "call_model_message", side_effect=lambda *a, **k: next(it)):
            result = AgentLoop(self._config(), reg, max_steps=4, use_native_tools=False,
                               auto_approve=True).run("remember I love Goa")
        self.assertEqual(result.stopped_reason, "final")
        self.assertEqual(pm.saved[-1]["content"], "loves Goa")
        self.assertIn("Goa", result.answer)


if __name__ == "__main__":
    unittest.main()
