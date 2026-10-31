import json
import tempfile
import unittest
from pathlib import Path

import general_core as core
import general_data_guard as guard


R54_TO_R60 = "2020-08-02"
TARGET_DATE = "2020-08-06"


def valid_rows():
    return [
        {"date": "2020-07-30", "numbers": [1, 2, 3, 4, 5, 54]},
        {"date": R54_TO_R60, "numbers": [1, 2, 3, 4, 5, 60]},
        {"date": TARGET_DATE, "numbers": [2, 3, 4, 5, 6, 60]},
    ]


class GeneralCoreArchiveRegimeTests(unittest.TestCase):
    def run_prepare_data(self, rows, target=TARGET_DATE):
        with tempfile.TemporaryDirectory() as temporary_directory:
            archive_path = Path(temporary_directory) / "synthetic_archive.json"
            archive_path.write_text(
                json.dumps({"draws": rows}),
                encoding="utf-8",
            )
            original_archive = core.ARCHIVE
            try:
                core.ARCHIVE = archive_path
                return core.prepare_data(target)
            finally:
                core.ARCHIVE = original_archive

    def test_r54_and_r60_boundary_maximum_values_are_accepted(self):
        rows = valid_rows()

        # The global guard accepts these 1..60 values; GENERAL Core additionally
        # enforces the date-specific maxima and ascending canonical order.
        self.assertTrue(guard.validate_archive(rows)["valid"])
        prepared = self.run_prepare_data(rows)

        self.assertEqual(prepared["training_count"], 2)
        self.assertEqual(prepared["regime_counts"], {"R54": 1, "R60": 1})
        self.assertEqual(prepared["regime_boundary"], R54_TO_R60)

    def test_r54_out_of_pool_number_is_rejected_even_when_target_is_later(self):
        rows = valid_rows()
        rows[0]["numbers"] = [1, 2, 3, 4, 5, 55]

        # This demonstrates the precise gap: the generic 1..60 guard alone
        # accepts the synthetic row, but the date-aware core gate must reject it.
        self.assertTrue(guard.validate_archive(rows)["valid"])
        with self.assertRaisesRegex(RuntimeError, "invalid number set for R54"):
            self.run_prepare_data(rows)

    def test_hidden_r54_target_is_validated_before_it_is_excluded(self):
        rows = [
            {"date": "2020-07-23", "numbers": [1, 2, 3, 4, 5, 54]},
            {"date": "2020-07-30", "numbers": [1, 2, 3, 4, 5, 55]},
            {"date": R54_TO_R60, "numbers": [1, 2, 3, 4, 5, 60]},
        ]
        self.assertTrue(guard.validate_archive(rows)["valid"])

        with self.assertRaisesRegex(RuntimeError, "invalid number set for R54"):
            self.run_prepare_data(rows, target="2020-07-30")

    def test_unsorted_isolated_target_is_rejected_without_exposing_its_values(self):
        rows = valid_rows()
        rows[-1]["numbers"] = [3, 1, 2, 4, 5, 6]
        self.assertTrue(guard.validate_archive(rows)["valid"])

        with self.assertRaisesRegex(RuntimeError, "sorted ascending for R60"):
            self.run_prepare_data(rows)

    def test_r60_number_sixty_is_valid_at_and_after_boundary(self):
        rows = valid_rows()
        prepared = self.run_prepare_data(rows)

        self.assertTrue(prepared["row_level_audit_pass"])
        self.assertEqual(prepared["training_by_regime"]["R60"][0]["date"], R54_TO_R60)


if __name__ == "__main__":
    unittest.main(verbosity=2)
