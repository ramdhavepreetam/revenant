from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "core", _ROOT / "tts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from aibot_tts import apply_delivery, build_segments, prepare_spoken_text, qwen3_instruction


class TTSTextTests(unittest.TestCase):
    def test_removes_mmh_before_speech(self) -> None:
        self.assertEqual(prepare_spoken_text("Mmh... oh my god, good way."), "oh my god, good way.")

    def test_keeps_normal_oh(self) -> None:
        self.assertEqual(prepare_spoken_text("Oh, I missed you."), "Oh, I missed you.")

    def test_skips_filler_only_chunk(self) -> None:
        self.assertEqual(prepare_spoken_text("mmm... mmh"), "")

    def test_drops_stage_direction_before_speech(self) -> None:
        self.assertEqual(prepare_spoken_text("[soft sigh] I missed you."), "I missed you.")

    def test_build_segments_does_not_speak_action_beat_or_filler(self) -> None:
        segments = build_segments("*she sighs* Mmh... I missed you.", 0.8, 0.3, sfx_enabled=False)
        self.assertEqual(segments, [{"kind": "speech", "text": "I missed you.", "exaggeration": 0.8, "cfg_weight": 0.3}])

    def test_apply_delivery_sets_kokoro_speed(self) -> None:
        profile = {"engine": "kokoro", "speed": 1.0}
        adjusted = apply_delivery(profile, {"pace": 1.12, "energy": 0.6})
        self.assertEqual(adjusted["speed"], 1.12)

    def test_apply_delivery_sets_chatterbox_energy(self) -> None:
        profile = {"engine": "chatterbox", "exaggeration": 0.8, "cfg_weight": 0.3}
        adjusted = apply_delivery(profile, {"energy": 0.8})
        self.assertGreater(adjusted["exaggeration"], 0.8)
        self.assertLess(adjusted["cfg_weight"], 0.3)

    def test_qwen3_instruction_combines_mood_and_delivery(self) -> None:
        profile = {
            "qwen3_instruct": "Speak naturally.",
            "mood_instructions": {"warm": "Use warmth."},
        }
        instruction = qwen3_instruction(profile, "warm", {"pace": 1.15, "energy": 0.8})
        self.assertIn("Speak naturally.", instruction)
        self.assertIn("Use warmth.", instruction)
        self.assertIn("quick and lively", instruction)
        self.assertIn("more vocal energy", instruction)


if __name__ == "__main__":
    unittest.main()
