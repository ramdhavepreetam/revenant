from __future__ import annotations

import unittest

from web_app import delivery_for_sentence


class DeliveryMetadataTests(unittest.TestCase):
    def test_greeting_delivery_is_warm_and_quick(self) -> None:
        delivery = delivery_for_sentence("Hey, you.", {"label": "greeting", "mode": "chat"})
        self.assertEqual(delivery["mood"], "warm")
        self.assertGreaterEqual(delivery["pace"], 1.08)
        self.assertLessEqual(delivery["pause_after_ms"], 140)

    def test_soft_sentence_slows_down(self) -> None:
        delivery = delivery_for_sentence("I am here, soft and quiet.", {"label": "brief", "mode": "chat"})
        self.assertEqual(delivery["mood"], "soft")
        self.assertLessEqual(delivery["pace"], 0.98)
        self.assertGreaterEqual(delivery["pause_after_ms"], 260)

    def test_question_keeps_a_pause(self) -> None:
        delivery = delivery_for_sentence("Did you miss me?", {"label": "brief", "mode": "chat"})
        self.assertGreaterEqual(delivery["pause_after_ms"], 240)


if __name__ == "__main__":
    unittest.main()
