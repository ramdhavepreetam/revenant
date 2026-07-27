from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

from nerva_core.aibot_companion_compiler import (
    compile_companion_profile,
    fallback_compile,
    merge_compiled_into_companion,
    profile_hash,
    profile_needs_compile,
)
from nerva_core.aibot_profiles import build_companion_prompt


class CompanionCompilerTests(unittest.TestCase):
    def test_fallback_compile_structures_single_prompt(self) -> None:
        compiled = fallback_compile(
            "Create a mentor named Mira. She is warm, concise, and helps me think clearly.",
            display_name="",
        )
        self.assertEqual(compiled["display_name"], "Mira")
        self.assertEqual(compiled["archetype"], "mentor")
        self.assertIn("warm", compiled["identity"])
        self.assertTrue(compiled["behavior_rules"])

    def test_fallback_compile_preserves_companion_user_boundary(self) -> None:
        compiled = fallback_compile(
            "You are Eros, a romantic female companion. The user is the human partner.",
            display_name="Eros",
        )
        self.assertEqual(compiled["speaker_role"], "assistant_companion")
        self.assertIn("human partner", compiled["user_role"])
        self.assertIn("Eros is", compiled["identity"])
        self.assertNotIn("You are", compiled["identity"])

    def test_fallback_compile_keeps_romantic_woman_on_companion_side(self) -> None:
        compiled = fallback_compile(
            "You are Kavita, a romantic woman and girlfriend. Be warm with the user.",
            display_name="Kavita",
        )
        self.assertEqual(compiled["companion_gender"], "woman")
        self.assertEqual(compiled["user_gender"], "unspecified")
        block = compile_companion_profile(
            "You are Kavita, a romantic woman and girlfriend. Be warm with the user.",
            config=None,
            display_name="Kavita",
        )["compiled_system_block"]
        self.assertIn("Companion gender identity: woman", block)
        self.assertIn("User gender identity: unspecified", block)
        self.assertIn("Gender boundary", block)

    def test_compile_bundle_contains_cache_metadata(self) -> None:
        bundle = compile_companion_profile("A supportive friend who answers briefly.", config=None, display_name="Kai")
        self.assertEqual(bundle["profile_hash"], profile_hash("A supportive friend who answers briefly."))
        self.assertIn("compiled_profile", bundle)
        self.assertIn("compiled_system_block", bundle)

    def test_merge_compiled_profile_populates_legacy_fields(self) -> None:
        bundle = compile_companion_profile("A creative partner named Aria who loves vivid scenes.", config=None)
        companion = merge_compiled_into_companion({}, bundle)
        self.assertEqual(companion["display_name"], bundle["compiled_profile"]["display_name"])
        self.assertEqual(companion["persona"], bundle["compiled_system_block"])
        self.assertTrue(companion["role"])
        self.assertTrue(companion["behavior"])
        self.assertTrue(companion["response_style"])
        self.assertIn("Role boundary", companion["persona"])
        self.assertIn("Embodiment rule", companion["persona"])
        self.assertIn("Narration rule", companion["persona"])

    def test_profile_needs_compile_when_prompt_changes(self) -> None:
        bundle = compile_companion_profile("A calm mentor.", config=None, display_name="Mira")
        companion = merge_compiled_into_companion({}, bundle)
        self.assertFalse(profile_needs_compile(companion))
        companion["raw_prompt"] = "A playful friend."
        self.assertTrue(profile_needs_compile(companion))

    def test_build_companion_prompt_prefers_compiled_persona(self) -> None:
        prompt = build_companion_prompt(
            {
                "display_name": "Kai",
                "compiled_system_block": "Compiled identity block.",
                "role": "Legacy role should not win.",
            }
        )
        self.assertIn("Compiled identity block.", prompt)
        self.assertNotIn("Legacy role should not win.", prompt)


if __name__ == "__main__":
    unittest.main()
