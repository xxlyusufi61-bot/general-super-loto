import io
import unittest
from unittest.mock import patch
from urllib.error import URLError

import general_archive_fetch as fetcher



class SafeFetchDiagnosticsTests(unittest.TestCase):
    def test_retries_transient_network_error_then_succeeds(self):
        with patch.object(
            fetcher, "urlopen",
            side_effect=[URLError("temporary private detail"), io.BytesIO(b"ok")],
        ) as mock_open, patch.object(fetcher.time, "sleep") as mock_sleep:
            self.assertEqual(fetcher.fetch_html("https://example.invalid", "fixture"), "ok")
        self.assertEqual(mock_open.call_count, 2)
        mock_sleep.assert_called_once_with(1)

    def test_fetch_failure_category_does_not_expose_source_details(self):
        exc = RuntimeError(
            "Primary source 2026 download failed after 3 attempt(s); "
            "private page contents and draw numbers suppressed"
        )
        self.assertEqual(fetcher.safe_failure_category(exc), "SOURCE_FETCH_FAILED")
        self.assertNotIn("2026", fetcher.safe_failure_category(exc))

    def test_cross_source_failure_has_safe_category(self):
        self.assertEqual(
            fetcher.safe_failure_category(
                RuntimeError("independent-source verification failed (2 mismatches)")
            ),
            "CROSS_SOURCE_MISMATCH",
        )

class MultiYearReconciliationTests(unittest.TestCase):
    def test_adds_only_independently_verified_missing_draws(self):
        primary = [
            {"date": "2021-01-03", "numbers": [1, 2, 3, 4, 5, 6]},
        ]
        independent = {
            "2021-01-03": (1, 2, 3, 4, 5, 6),
            "2021-01-07": (7, 8, 9, 10, 11, 12),
        }
        merged, report = fetcher.reconcile_year_with_loto_gen(
            2021, primary, independent
        )
        self.assertEqual(
            [row["date"] for row in merged],
            ["2021-01-03", "2021-01-07"],
        )
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(report["records_added_from_independent_source"], 1)
        self.assertFalse(report["draw_numbers_in_report"])

    def test_rejects_number_mismatch_without_saving(self):
        primary = [
            {"date": "2021-01-03", "numbers": [1, 2, 3, 4, 5, 6]},
        ]
        independent = {"2021-01-03": (1, 2, 3, 4, 5, 7)}
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            fetcher.reconcile_year_with_loto_gen(2021, primary, independent)

    def test_rejects_primary_only_dates_without_saving(self):
        primary = [
            {"date": "2021-01-03", "numbers": [1, 2, 3, 4, 5, 6]},
            {"date": "2021-01-07", "numbers": [7, 8, 9, 10, 11, 12]},
        ]
        independent = {"2021-01-03": (1, 2, 3, 4, 5, 6)}
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            fetcher.reconcile_year_with_loto_gen(2021, primary, independent)

    def test_rejects_source_parse_errors_without_saving(self):
        primary = [
            {"date": "2021-01-03", "numbers": [1, 2, 3, 4, 5, 6]},
        ]
        independent = {"2021-01-03": (1, 2, 3, 4, 5, 6)}
        with self.assertRaisesRegex(RuntimeError, "unparsed row"):
            fetcher.reconcile_year_with_loto_gen(
                2021, primary, independent, ["2021-01-07"]
            )

    def test_2020_reconciliation_is_scoped_to_r60_for_cross_check(self):
        primary = [
            {"date": "2020-01-02", "numbers": [1, 2, 3, 4, 5, 6]},
            {"date": "2020-08-02", "numbers": [7, 8, 9, 10, 11, 12]},
        ]
        independent = {"2020-08-02": (7, 8, 9, 10, 11, 12)}
        merged, report = fetcher.reconcile_year_with_loto_gen(
            2020, primary, independent
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual(report["result"], "PASS")
        self.assertEqual(report["independent_source_count"], 1)


if __name__ == "__main__":
    unittest.main()
