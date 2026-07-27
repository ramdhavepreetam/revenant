from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent

import nerva_agent.agent_router as agent_router
from nerva_agent.agent_router import (
    DEFAULT_FALLBACK,
    classify,
    config_for_role,
    _heuristic_role,
    _normalize_role,
)
from nerva_core.local_llm_writer import ChatConfig, LocalLLMError


# A minimal profiles dict with the role map + the models it references.
PROFILES = {
    "models": {
        "qwen2.5-7b": {"backend": "ollama", "base_url": "http://x:11434", "model": "qwen2.5:7b"},
        "qwen2.5-14b": {"backend": "ollama", "base_url": "http://x:11434", "model": "qwen2.5:14b"},
        "stheno-8b": {"backend": "ollama", "base_url": "http://x:11434", "model": "stheno-gguf"},
        "gemma": {"backend": "ollama", "base_url": "http://x:11434", "model": "gemma:latest"},
    },
    "model_roles": {
        "code": "qwen2.5-7b",
        "language": "qwen2.5-14b",
        "companion": "stheno-8b",
        "summary": "gemma",
        "router": "qwen2.5-7b",
        "fallback": "language",
    },
}


def _base_config() -> ChatConfig:
    return ChatConfig(
        backend="ollama", base_url="http://localhost:11434", model="llama3.1:8b",
        temperature=0.85, top_p=0.9, repeat_penalty=1.08,
        min_tokens=400, max_tokens=800, context_messages=18, system_prompt="SYS",
    )


class HeuristicPreFilterTests(unittest.TestCase):
    def test_code_keyword(self):
        self.assertEqual(_heuristic_role("fix this traceback in app.py", has_companion=False), "code")

    def test_code_fence(self):
        text = "what's wrong here\n```python\ndef f(): return 1\n```"
        self.assertEqual(_heuristic_role(text, has_companion=False), "code")

    def test_code_write_phrase(self):
        self.assertEqual(
            _heuristic_role("write a function to reverse a list", has_companion=False), "code"
        )

    def test_language_signal(self):
        self.assertEqual(
            _heuristic_role("explain why recursion works", has_companion=False), "language"
        )

    def test_companion_wins_for_casual(self):
        self.assertEqual(
            _heuristic_role("i missed you today", has_companion=True), "companion"
        )

    def test_companion_asking_for_code_routes_to_code(self):
        self.assertEqual(
            _heuristic_role("babe can you write a python script for me", has_companion=True), "code"
        )

    def test_ambiguous_defers_to_llm(self):
        self.assertIsNone(_heuristic_role("hmm what should we do", has_companion=False))


class ConfigResolutionTests(unittest.TestCase):
    def test_fresh_config_for_language(self):
        cfg = config_for_role("language", "http://localhost:11434", PROFILES, base=None)
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.model, "qwen2.5:14b")
        self.assertEqual(cfg.backend, "ollama")
        self.assertEqual(cfg.base_url, "http://x:11434")

    def test_fresh_config_for_code_uses_low_temp(self):
        cfg = config_for_role("code", "http://localhost:11434", PROFILES, base=None)
        self.assertEqual(cfg.model, "qwen2.5:7b")
        self.assertLessEqual(cfg.temperature, 0.3)

    def test_base_mutation_preserves_turn_shaping(self):
        base = _base_config()
        out = config_for_role("code", base.base_url, PROFILES, base=base)
        self.assertIs(out, base)  # mutated in place
        self.assertEqual(base.model, "qwen2.5:7b")  # model switched
        # tokens / temperature / system_prompt untouched
        self.assertEqual(base.min_tokens, 400)
        self.assertEqual(base.max_tokens, 800)
        self.assertEqual(base.temperature, 0.85)
        self.assertEqual(base.system_prompt, "SYS")

    def test_missing_role_leaves_base_unchanged(self):
        base = _base_config()
        out = config_for_role("nonexistent", base.base_url, PROFILES, base=base)
        self.assertIs(out, base)
        self.assertEqual(base.model, "llama3.1:8b")  # untouched

    def test_missing_role_fresh_returns_none(self):
        self.assertIsNone(config_for_role("nonexistent", "u", PROFILES, base=None))

    def test_no_model_roles_section_fresh_returns_none(self):
        self.assertIsNone(config_for_role("code", "u", {"models": {}}, base=None))


class NormalizeRoleTests(unittest.TestCase):
    def test_clean_word(self):
        self.assertEqual(_normalize_role("language"), "language")

    def test_trailing_whitespace_and_case(self):
        self.assertEqual(_normalize_role("  Code\n"), "code")

    def test_extracts_from_chatter(self):
        self.assertEqual(_normalize_role("Sure! This is code."), "code")

    def test_out_of_set_falls_back(self):
        self.assertEqual(_normalize_role("banana"), DEFAULT_FALLBACK)

    def test_empty_falls_back(self):
        self.assertEqual(_normalize_role(""), DEFAULT_FALLBACK)


class ClassifyTests(unittest.TestCase):
    def test_heuristic_hit_skips_llm(self):
        # Obvious code turn -> no call_model at all.
        with mock.patch.object(agent_router, "call_model") as m:
            role = classify("refactor the parser in agent_loop.py", profiles=PROFILES)
        self.assertEqual(role, "code")
        m.assert_not_called()

    def test_ambiguous_uses_llm(self):
        with mock.patch.object(agent_router, "call_model", return_value="language\n") as m:
            role = classify("give me your take on this idea", profiles=PROFILES)
        self.assertEqual(role, "language")
        m.assert_called_once()

    def test_llm_garbage_normalizes(self):
        with mock.patch.object(agent_router, "call_model", return_value="I think this is code."):
            role = classify("something genuinely unclear here", profiles=PROFILES)
        self.assertEqual(role, "code")

    def test_llm_out_of_set_falls_back(self):
        with mock.patch.object(agent_router, "call_model", return_value="banana"):
            role = classify("something genuinely unclear here", profiles=PROFILES)
        self.assertEqual(role, "language")  # profile fallback

    def test_llm_error_never_raises(self):
        with mock.patch.object(agent_router, "call_model", side_effect=LocalLLMError("boom")):
            role = classify("something genuinely unclear here", profiles=PROFILES)
        self.assertEqual(role, "language")

    def test_router_model_missing_falls_back_without_calling(self):
        profiles = {"models": {}, "model_roles": {"fallback": "language"}}
        with mock.patch.object(agent_router, "call_model") as m:
            role = classify("something genuinely unclear here", profiles=profiles)
        self.assertEqual(role, "language")
        m.assert_not_called()


if __name__ == "__main__":
    unittest.main()
