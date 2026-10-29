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


    def test_root_archive_parser_reads_multiple_result_links_without_exposing_numbers(self):
        html = """
        <div class="archive-results">
          <a href="/super-loto/result/20260001">
            <span>#20260001</span><span>05 Ocak 2026</span>
            <span>3</span><span>11</span><span>18</span>
            <span>24</span><span>37</span><span>52</span>
          </a>
          <a href="/super-loto/result/20250001">
            <span>#20250001</span><span>02 Ocak 2025</span>
            <span>4</span><span>12</span><span>19</span>
            <span>25</span><span>38</span><span>53</span>
          </a>
        </div>
        """
        found, errors, years = audit.parse_loto_gen_root_archive_page(html)

        self.assertEqual(years, {2025, 2026})
        self.assertEqual(errors, [])
        self.assertIn((2026, "2026-01-05"), found)
        self.assertIn((2025, "2025-01-02"), found)
        self.assertEqual(len(found), 2)
        report_like = json.dumps({"record_count": len(found), "error_count": len(errors)})
        self.assertNotIn("numbers", report_like)

    def test_root_archive_parser_flags_invalid_draw_card(self):
        html = """
        <a href="/super-loto/result/20260002">
          <span>#20260002</span><span>07 Ocak 2026</span>
          <span>3</span><span>11</span><span>18</span>
        </a>
        """
        found, errors, years = audit.parse_loto_gen_root_archive_page(html)
        self.assertEqual(found, {})
        self.assertEqual(errors, [(2026, "2026-01-07")])
        self.assertEqual(years, {2026})

    def test_root_archive_fetch_stops_at_first_page_without_requested_year(self):
        page1 = """
        <a href="/super-loto/result/20260001"><span>#20260001</span>
        <span>05 Ocak 2026</span><span>3</span><span>11</span><span>18</span>
        <span>24</span><span>37</span><span>52</span></a>
        """
        page2 = """
        <a href="/super-loto/result/20250001"><span>#20250001</span>
        <span>02 Ocak 2025</span><span>4</span><span>12</span><span>19</span>
        <span>25</span><span>38</span><span>53</span></a>
        """
        with patch.object(audit, "fetch_html", side_effect=[page1, page2]) as fetch:
            rows, errors, pages, failures = audit.fetch_loto_gen_year_from_root_archive(2026)
        self.assertEqual(set(rows), {"2026-01-05"})
        self.assertEqual(errors, [])
        self.assertEqual(pages, 2)
        self.assertEqual(failures, [])
        self.assertEqual(fetch.call_count, 2)

    def test_root_archive_fetch_fails_closed_when_first_page_is_unparseable(self):
        with patch.object(audit, "fetch_html", return_value="<html></html>"):
            rows, errors, pages, failures = audit.fetch_loto_gen_year_from_root_archive(2026)
        self.assertEqual(rows, {})
        self.assertEqual(errors, [])
        self.assertEqual(pages, 1)
        self.assertEqual(failures, [
            "loto.gen.tr:root-archive:page1:no-draw-links"
        ])

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
