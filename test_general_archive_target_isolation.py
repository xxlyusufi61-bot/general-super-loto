import json
import unittest
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import general_data_guard as guard


ROOT = Path(__file__).resolve().parent
ARCHIVE_PATH = ROOT / "super_loto_archive.json"
RESEARCH_SCOPE_START = "2020-08-02"
EXPECTED_HISTORICAL_YEAR_COUNTS = {
    2020: 66,
    2021: 156,
    2022: 156,
    2023: 157,
    2024: 157,
    2025: 156,
}


class CanonicalArchiveTargetIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not ARCHIVE_PATH.is_file():
            raise RuntimeError("Canonical archive file is missing.")
        try:
            cls.payload = json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Canonical archive could not be parsed.") from exc
        if not isinstance(cls.payload, dict):
            raise RuntimeError("Canonical archive root must be an object.")
        cls.rows = cls.payload.get("draws")
        if not isinstance(cls.rows, list):
            raise RuntimeError("Canonical archive must contain a draws list.")
        cls.summary = guard.validate_archive(cls.rows)
        cls.dates = [row["date"] for row in cls.rows]
        cls.target_date = cls.summary["last_date"]

    def test_archive_is_valid_strictly_chronological_and_unique(self):
        self.assertTrue(self.summary["valid"] is True)
        self.assertEqual(self.summary.get("research_scope_start_date"), RESEARCH_SCOPE_START)
        self.assertEqual(self.dates[0], RESEARCH_SCOPE_START)
        self.assertEqual(self.summary["record_count"], len(self.rows))
        self.assertEqual(self.summary["unique_dates"], len(self.dates))
        self.assertEqual(self.dates, sorted(self.dates))
        self.assertEqual(len(self.dates), len(set(self.dates)))
        self.assertEqual(self.dates[0], self.summary["first_date"])
        self.assertEqual(self.dates[-1], self.summary["last_date"])
        self.assertTrue(all(
            isinstance(row["numbers"], list)
            and len(row["numbers"]) == 6
            and len(set(row["numbers"])) == 6
            and all(type(n) is int and 1 <= n <= 60 for n in row["numbers"])
            and row["numbers"] == sorted(row["numbers"])
            for row in self.rows
        ))

    def test_historical_year_counts_match_fetcher_contract(self):
        counts = Counter(int(day[:4]) for day in self.dates)
        for year, expected in EXPECTED_HISTORICAL_YEAR_COUNTS.items():
            with self.subTest(year=year):
                self.assertEqual(counts[year], expected)
        self.assertGreater(counts[2026], 0)

    def test_independent_2025_cross_source_check_is_recorded_as_pass(self):
        check = self.payload.get("cross_check_validation")
        self.assertIsInstance(check, dict)
        self.assertEqual(check.get("result"), "PASS")
        self.assertEqual(check.get("year"), 2025)
        self.assertEqual(check.get("primary_record_count"), 156)
        self.assertEqual(check.get("independent_record_count"), 156)
        self.assertEqual(check.get("matching_dates_and_number_sets"), 156)
        try:
            checked_at = datetime.fromisoformat(
                str(check.get("checked_at", "")).replace("Z", "+00:00")
            )
            fetched_at = datetime.fromisoformat(
                str(self.payload.get("fetched_at", "")).replace("Z", "+00:00")
            )
        except ValueError as exc:
            self.fail("Archive provenance timestamps are invalid.")
        self.assertLessEqual(checked_at, fetched_at)

    def test_archive_provenance_matches_current_fetcher_schema(self):
        def parse_aware_timestamp(value, label):
            self.assertIsInstance(value, str, label + " must be a string")
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                self.fail(label + " is not valid ISO-8601")
            self.assertIsNotNone(parsed.tzinfo, label + " must include timezone")
            self.assertIsNotNone(parsed.utcoffset(), label + " must include UTC offset")
            return parsed

        fetched_at = parse_aware_timestamp(
            self.payload.get("fetched_at"),
            "Archive fetched_at",
        )
        yearly_counts = Counter(int(day[:4]) for day in self.dates)

        # The primary fetcher retains the independent 2025 check as a distinct
        # provenance record and writes the complete year-level reconciliation map.
        cross = self.payload.get("cross_check_validation")
        self.assertIsInstance(cross, dict)
        self.assertIs(type(cross.get("year")), int)
        self.assertEqual(cross.get("year"), 2025)
        self.assertEqual(cross.get("result"), "PASS")
        expected_2025_count = yearly_counts[2025]
        for field in (
            "primary_record_count",
            "independent_record_count",
            "matching_dates_and_number_sets",
        ):
            self.assertIs(type(cross.get(field)), int)
            self.assertEqual(cross.get(field), expected_2025_count)
        pages = cross.get("pages_checked")
        self.assertIsInstance(pages, list)
        self.assertTrue(pages)
        self.assertTrue(all(isinstance(page, str) and page.strip() for page in pages))
        self.assertLessEqual(
            parse_aware_timestamp(cross.get("checked_at"), "2025 cross-check timestamp"),
            fetched_at,
        )

        validation = self.payload.get("multi_year_cross_source_validation")
        expected_years = {str(year) for year in range(2020, 2027)}
        self.assertIsInstance(validation, dict)
        self.assertEqual(set(validation), expected_years)

        count_fields = (
            "archive_compared_count_before_merge",
            "independent_source_count",
            "records_added_from_independent_source",
            "matching_dates_and_number_sets_after_merge",
        )
        for year in sorted(expected_years):
            with self.subTest(year=year):
                details = validation[year]
                self.assertIsInstance(details, dict)
                self.assertEqual(details.get("result"), "PASS")
                self.assertIs(details.get("draw_numbers_in_report"), False)
                self.assertIsInstance(details.get("source"), str)
                self.assertTrue(details.get("source").strip())
                year_pages = details.get("pages_checked")
                self.assertIsInstance(year_pages, list)
                self.assertTrue(year_pages)
                self.assertTrue(
                    all(isinstance(page, str) and page.strip() for page in year_pages)
                )
                self.assertLessEqual(
                    parse_aware_timestamp(
                        details.get("checked_at"),
                        "Multi-year audit timestamp",
                    ),
                    fetched_at,
                )

                counts = {}
                for field in count_fields:
                    value = details.get(field)
                    self.assertIs(type(value), int)
                    self.assertGreaterEqual(value, 0)
                    counts[field] = value
                self.assertEqual(
                    counts["archive_compared_count_before_merge"]
                    + counts["records_added_from_independent_source"],
                    counts["matching_dates_and_number_sets_after_merge"],
                )
                self.assertEqual(
                    counts["independent_source_count"],
                    counts["matching_dates_and_number_sets_after_merge"],
                )
                self.assertEqual(
                    counts["matching_dates_and_number_sets_after_merge"],
                    yearly_counts[int(year)],
                )

    def test_every_canonical_record_is_inside_the_active_scope(self):
        self.assertEqual(self.dates[0], RESEARCH_SCOPE_START)
        self.assertTrue(all(day >= RESEARCH_SCOPE_START for day in self.dates))
        self.assertEqual(self.summary.get("research_scope_start_date"), RESEARCH_SCOPE_START)


    def test_latest_draw_is_present_once_and_excluded_from_training(self):
        target_matches = [
            row for row in self.rows if row["date"] == self.target_date
        ]
        self.assertEqual(len(target_matches), 1)
        self.assertEqual(self.target_date, self.dates[-1])

        result = guard.isolate_target(self.rows, self.target_date)
        self.assertIs(result.get("isolation_pass"), True)
        self.assertIs(result.get("target_present_in_archive"), True)
        self.assertIs(result.get("target_numbers_exposed"), False)

        train = result.get("train")
        excluded_dates = result.get("excluded_dates")
        self.assertIsInstance(train, list)
        self.assertIsInstance(excluded_dates, list)

        training_dates = [row.get("date") for row in train]
        expected_training_dates = [
            row["date"] for row in self.rows
            if row["date"] < self.target_date
        ]
        expected_excluded_dates = [
            row["date"] for row in self.rows
            if row["date"] >= self.target_date
        ]

        # Compare dates/counts only; do not place draw numbers in logs.
        self.assertEqual(training_dates, expected_training_dates)
        self.assertEqual(excluded_dates, expected_excluded_dates)
        self.assertNotIn(self.target_date, training_dates)
        self.assertTrue(all(day < self.target_date for day in training_dates))
        self.assertEqual(len(train) + len(excluded_dates), len(self.rows))
        self.assertEqual(len(excluded_dates), 1)

        safe = guard.safe_summary(result)
        self.assertIs(safe.get("target_numbers_exposed"), False)
        self.assertNotIn("train", safe)
        self.assertNotIn("numbers", safe)

    def test_guard_rejects_duplicate_date_and_malformed_draw(self):
        fixture = [
            {"date": "2026-01-01", "numbers": [1, 2, 3, 4, 5, 6]},
            {"date": "2026-01-02", "numbers": [1, 2, 3, 4, 5, 6]},
        ]
        with self.assertRaises(guard.DataGuardError):
            guard.validate_archive([fixture[0], fixture[0]])
        with self.assertRaises(guard.DataGuardError):
            guard.validate_archive([
                {"date": "2026-01-01", "numbers": [1, 2, 3, 4, 5, 61]}
            ])


if __name__ == "__main__":
    unittest.main(verbosity=2)
