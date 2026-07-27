from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

from aibot_app.aibot_personal_memory import PersonalMemoryStore, extract_personal_memories


class PersonalMemoryTests(unittest.TestCase):
    def test_extracts_explicit_preferences_and_boundaries(self) -> None:
        memories = extract_personal_memories("I prefer short replies. I don't like robotic voice.")
        categories = {memory["category"] for memory in memories}
        self.assertIn("preference", categories)
        self.assertIn("boundary", categories)

    def test_migrates_legacy_companion_memory(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "companion_memory.json").write_text(
            json.dumps(
                {
                    "companions": {
                        "demo": {
                            "user_name": "Kavi",
                            "tone_preferences": ["brief replies"],
                            "boundaries": ["avoid robotic phrasing"],
                            "current_dynamic": "trusted companion",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        store = PersonalMemoryStore(root)
        memories = store.list_memories(companion_id="demo", include_archived=True)
        self.assertEqual(len(memories), 4)
        self.assertTrue((root / "companion_memory.legacy-backup.json").exists())

    def test_conflicting_memory_goes_to_review(self) -> None:
        root = Path(tempfile.mkdtemp())
        store = PersonalMemoryStore(root)
        store.create_memory("demo", "preference", "User prefers robotic voice.")
        learned = store.learn("demo", "I don't like robotic voice.", "conversation-1")
        self.assertEqual(learned[0]["status"], "pending")

    def test_prompt_uses_pinned_preferences_first(self) -> None:
        root = Path(tempfile.mkdtemp())
        store = PersonalMemoryStore(root)
        store.create_memory("demo", "preference", "User prefers concise replies.", pinned=True)
        store.create_memory("demo", "boundary", "Avoid robotic phrasing.")
        prompt, used = store.prompt_memories("demo")
        self.assertIn("Pinned needs and preferences", prompt)
        self.assertIn("Boundaries and avoidances", prompt)
        self.assertEqual(len(used), 2)


if __name__ == "__main__":
    unittest.main()
