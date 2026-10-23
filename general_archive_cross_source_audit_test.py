import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import general_archive_cross_source_audit as audit


class ArchiveCrossSourceAuditTests(unittest.TestCase):
    def test_compare_exact_match(self):
        rows = [
            {"date": "2025-01-02", "numbers": [1, 2, 3, 4, 5, 6]},
            {"date": "2025-01-04", "numbers": [7, 8, 9, 10, 11, 12]},
        ]
        source = {
            "2025-01-02": (1, 2, 3, 4, 5, 6),
            "2025-01-04": (7, 8, 9, 10, 11, 12),
        }
        result = audit._compare(rows, source)
        self.assertEqual(result["status"], "MATCH")
        self.assertEqual(result["common_date_count"], 2)
        self.assertEqual(result["number_mismatch_count"], 0)
        self.assertFalse(result["draw_numbers_in_report"])

    def test_compare_detects_wrong_number_set_without_exposing_numbers(self):
        rows = [{"date": "2025-01-02", "numbers": [1, 2, 3, 4, 5, 6]}]
        source = {"2025-01-02": (1, 2, 3, 4, 5, 7)}
        result = audit._compare(rows, source)
        self.assertEqual(result["status"], "MISMATCH")
        self.assertEqual(result["number_mismatch_count"], 1)
        self.assertNotIn("numbers", str(result))

    def test_compare_distinguishes_source_lag_from_mismatch(self):
        rows = [
            {"date": "2025-01-02", "numbers": [1, 2, 3, 4, 5, 6]},
            {"date": "2025-01-04", "numbers": [7, 8, 9, 10, 11, 12]},
        ]
        source = {"2025-01-02": (1, 2, 3, 4, 5, 6)}
        result = audit._compare(rows, source)
        self.assertEqual(result["status"], "SOURCE_LAG_AT_TAIL")

    def test_parse_loto_gen_page_handles_draw_id_date_and_six_balls(self):
        html = """
        <table><tr><td>20250001</td><td>2 Eylül 2025</td>
        <td>1</td><td>8</td><td>14</td><td>27</td><td>33</td><td>59</td>
        <td>100.000 TL</td></tr></table>
        """
        rows, errors = audit.parse_loto_gen_year_page(html, 2025)
        self.assertEqual(errors, [])
        self.assertEqual(rows["2025-09-02"], (1, 8, 14, 27, 33, 59))

    def test_parse_loto_gen_page_rejects_missing_sixth_number(self):
        html = """
        <table><tr><td>20250001</td><td>2 Eylül 2025</td>
        <td>1</td><td>8</td><td>14</td><td>27</td><td>33</td>
        </tr></table>
        """
        rows, errors = audit.parse_loto_gen_year_page(html, 2025)
        self.assertEqual(rows, {})
        self.assertEqual(errors, ["2025-09-02"])

    def test_report_comparison_never_includes_draw_number_sets(self):
        rows = [{"date": "2025-01-02", "numbers": [1, 2, 3, 4, 5, 6]}]
        source = {"2025-01-03": (2, 3, 4, 5, 6, 7)}
        result = audit._compare(rows, source)
        self.assertFalse(result["draw_numbers_in_report"])
        self.assertNotIn("numbers", str(result))
        self.assertNotIn("2, 3, 4, 5, 6, 7", str(result))


if __name__ == "__main__":
    unittest.main()
