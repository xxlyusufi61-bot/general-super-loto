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
        self.assertNotIn("1, 2, 3, 4, 5, 7", str(result))

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
        self.assertNotIn("1, 2, 3, 4, 5, 6", str(result))
        self.assertNotIn("2, 3, 4, 5, 6, 7", str(result))


    def test_date_scope_uses_start_inclusive_end_exclusive_boundaries(self):
        self.assertFalse(
            audit._within_date_scope("2019-12-31", "2020-01-02", "2020-08-02")
        )
        self.assertTrue(
            audit._within_date_scope("2020-01-02", "2020-01-02", "2020-08-02")
        )
        self.assertFalse(
            audit._within_date_scope("2020-08-02", "2020-01-02", "2020-08-02")
        )
        self.assertTrue(
            audit._within_date_scope("2026-10-22", "2020-08-02")
        )

    def test_run_audit_declares_top_level_report_is_number_free(self):
        import json
        from unittest.mock import patch

        with TemporaryDirectory() as temp_dir:
            archive_path = Path(temp_dir) / "archive.json"
            archive_path.write_text(
                json.dumps({"draws": []}),
                encoding="utf-8",
            )
            guard_summary = {
                "valid": True,
                "guard_version": "test",
                "record_count": 0,
                "first_date": None,
                "last_date": None,
            }
            with patch(
                "general_data_guard.validate_archive",
                return_value=guard_summary,
            ), patch.object(audit, "fetch_html", return_value="<html></html>"):
                report = audit.run_audit(archive_path)

        self.assertIs(report.get("draw_numbers_in_report"), False)
        self.assertIs(report.get("archive_modified"), False)
        self.assertIs(report.get("active_target_numbers_exposed"), False)


    def test_lotobil_known_date_typo_is_normalized_only_by_exact_index_date(self):
        html = """
        <table><tr><td>08/08/2020</td><td>17</td>
        <td>1</td><td>8</td><td>14</td><td>27</td><td>33</td><td>59</td>
        </tr></table>
        """
        rows, errors, anomalies, _indices, _index_dates = (
            audit.parse_lotobil_archive_detailed(html)
        )
        self.assertEqual(errors, [])
        self.assertIn("2020-09-08", rows)
        self.assertNotIn("2020-08-08", rows)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(
            anomalies[0]["kind"], "CONFIRMED_DATE_LABEL_CORRECTION"
        )
        self.assertFalse(any("numbers" in key for key in anomalies[0]))

    def test_lotobil_known_duplicate_ball_row_is_excluded_exactly(self):
        html = """
        <table><tr><td>29/10/2023</td><td>130</td>
        <td>7</td><td>12</td><td>20</td><td>25</td><td>48</td><td>48</td>
        </tr></table>
        """
        rows, errors, anomalies, _indices, _index_dates = (
            audit.parse_lotobil_archive_detailed(html)
        )
        self.assertEqual(errors, [])
        self.assertNotIn("2023-10-29", rows)
        self.assertEqual(len(anomalies), 1)
        self.assertEqual(
            anomalies[0]["kind"], "INVALID_DUPLICATE_BALL_ROW"
        )

    def test_lotobil_unregistered_invalid_row_remains_a_parse_error(self):
        html = """
        <table><tr><td>29/10/2023</td><td>131</td>
        <td>7</td><td>12</td><td>20</td><td>25</td><td>48</td><td>48</td>
        </tr></table>
        """
        rows, errors, anomalies, _indices, _index_dates = (
            audit.parse_lotobil_archive_detailed(html)
        )
        self.assertEqual(rows, {})
        self.assertEqual(errors, ["2023-10-29"])
        self.assertEqual(anomalies, [])


if __name__ == "__main__":
    unittest.main()
