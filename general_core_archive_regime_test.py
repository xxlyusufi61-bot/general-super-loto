import json
import tempfile
import unittest
from pathlib import Path

import general_core as core
import general_data_guard as guard


SCOPE_START = "2020-08-02"
TARGET_DATE = "2020-08-06"


def eligible_rows():
    return [
        {"date": SCOPE_START, "numbers": [1, 2, 3, 4, 5, 60]},
        {"date": "2020-08-04", "numbers": [2, 3, 4, 5, 6, 60]},
        {"date": TARGET_DATE, "numbers": [3, 4, 5, 6, 7, 60]},
    ]


class GeneralCoreResearchScopeTests(unittest.TestCase):
    def run_prepare_data(self, rows, target=TARGET_DATE):
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "synthetic_archive.json"
            archive_path.write_text(json.dumps({"draws": rows}), encoding="utf-8")
            original_archive = core.ARCHIVE
            try:
                core.ARCHIVE = archive_path
                return core.prepare_data(target)
            finally:
                core.ARCHIVE = original_archive

    def test_only_post_scope_rows_enter_training(self):
        rows = eligible_rows()
        self.assertTrue(guard.validate_archive(rows)["valid"])
        prepared = self.run_prepare_data(rows)
        self.assertEqual(prepared["research_scope_start_date"], SCOPE_START)
        self.assertEqual(prepared["training_count"], 2)
        self.assertTrue(all(row["date"] >= SCOPE_START for row in prepared["training_data"]))
        self.assertNotIn("training_by_regime", prepared)
        self.assertNotIn("regime_counts", prepared)

    def test_pre_scope_row_is_rejected_before_target_isolation(self):
        rows = [
            {"date": "2020-07-30", "numbers": [1, 2, 3, 4, 5, 54]},
            *eligible_rows(),
        ]
        with self.assertRaisesRegex(guard.DataGuardError, "before the active research scope"):
            guard.validate_archive(rows)
        with self.assertRaisesRegex(RuntimeError, "Data Guard rejected archive"):
            self.run_prepare_data(rows)

    def test_target_must_be_inside_active_scope(self):
        with self.assertRaisesRegex(RuntimeError, "Data Guard rejected archive"):
            self.run_prepare_data(eligible_rows(), target="2020-07-30")

    def test_number_sixty_is_valid_in_current_archive_scope(self):
        prepared = self.run_prepare_data(eligible_rows())
        self.assertTrue(prepared["row_level_audit_pass"])
        self.assertTrue(all(max(row["numbers"]) <= 60 for row in prepared["training_data"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
