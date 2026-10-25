import json
import tempfile
import unittest
from pathlib import Path

import general_open_research as research

class OpenResearchIsolationTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"date": "2026-10-18", "numbers": [1, 7, 13, 22, 34, 48]},
            {"date": "2026-10-20", "numbers": [2, 8, 14, 23, 35, 49]},
            {"date": "2026-10-22", "numbers": [3, 9, 15, 24, 36, 50]},
        ]

    def test_target_and_future_are_not_in_model_payload(self):
        target, train = research.isolate_pre_target(self.rows)
        messages = research.build_messages(target, train)
        payload = json.loads(messages[1]["content"])
        self.assertEqual(target, "2026-10-22")
        self.assertEqual([r["date"] for r in payload["pre_target_history"]],
                         ["2026-10-18", "2026-10-20"])
        self.assertNotIn("2026-10-22", messages[1]["content"])
        self.assertFalse(payload["target_numbers_included"])
        self.assertFalse(payload["post_target_records_included"])
        self.assertNotIn("3,9,15,24,36,50", messages[1]["content"])

    def test_prompt_does_not_prescribe_legacy_method_families(self):
        target, train = research.isolate_pre_target(self.rows)
        messages = research.build_messages(target, train)
        system = messages[0]["content"].lower()
        for legacy_marker in ("a->b->c", "a->t", "mod5->t", "v8.6", "v19", "v0.3.1"):
            self.assertNotIn(legacy_marker, system)

    def test_prompt_requires_user_approval_for_rule_changes(self):
        system = research.build_messages("2026-10-22", self.rows[:2])[0]["content"].lower()
        self.assertIn("explicit user approval", system)
        self.assertIn("telegram", system)

    def test_invalid_archive_stops_before_model_payload(self):
        bad = [
            {"date": "2026-10-20", "numbers": [1, 1, 14, 23, 35, 49]},
            {"date": "2026-10-22", "numbers": [3, 9, 15, 24, 36, 50]},
        ]
        with self.assertRaises(RuntimeError):
            research.isolate_pre_target(bad)

    def test_target_or_future_training_row_is_rejected(self):
        bad = [
            {"date": "2026-10-18", "numbers": [1, 7, 13, 22, 34, 48]},
            {"date": "2026-10-20", "numbers": [2, 8, 14, 23, 35, 49]},
            {"date": "2026-10-22", "numbers": [3, 9, 15, 24, 36, 50]},
        ]
        target, train = research.isolate_pre_target(bad)
        self.assertTrue(all(row["date"] < target for row in train))

if __name__ == "__main__":
    unittest.main()
