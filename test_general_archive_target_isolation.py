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

    def test_archive_update_and_multi_year_provenance_are_recorded(self):
        updated = self.payload.get("archive_updated_at")
        self.assertIsInstance(updated, str)
        updated_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        fetched_at = datetime.fromisoformat(
            str(self.payload.get("fetched_at", "")).replace("Z", "+00:00")
        )
        self.assertGreaterEqual(updated_at, fetched_at)

        validation = self.payload.get("multi_year_cross_source_validation")
        self.assertIsInstance(validation, dict)
        self.assertEqual(
            validation.get("result"),
            "PASS_AFTER_DOCUMENTED_SOURCE_NORMALIZATION",
        )

        # The multi-year audit is a dated snapshot. It may legitimately predate
        # the latest archive row; never silently relabel that older audit as
        # covering a newly appended target draw.
        validation_last_date = validation.get("archive_last_date")
        self.assertIsInstance(validation_last_date, str)
        datetime.fromisoformat(validation_last_date)
        self.assertLessEqual(validation_last_date, self.target_date)

        validated_rows = [
            row for row in self.rows
            if row["date"] <= validation_last_date
        ]
        self.assertTrue(validated_rows)
        self.assertEqual(validated_rows[-1]["date"], validation_last_date)
        self.assertEqual(
            validation.get("archive_records"),
            len(validated_rows),
        )
        self.assertIs(validation.get("draw_numbers_in_report"), False)
        self.assertIs(validation.get("archive_modified_by_audit"), False)

        # If the canonical archive has advanced beyond the last complete audit,
        # require a separate, number-free, explicitly limited source check for
        # the new target row. This check is not a substitute for full auditing.
        if validation_last_date < self.target_date:
            recent_check = self.payload.get("recent_draw_source_check")
            self.assertIsInstance(recent_check, dict)
            self.assertEqual(
                recent_check.get("status"),
                "MATCH_SELECTED_PUBLISHERS_PENDING_FULL_AUDIT",
            )
            self.assertEqual(recent_check.get("records_checked"), 1)
            self.assertEqual(
                recent_check.get("checked_dates"),
                [self.target_date],
            )
            self.assertEqual(
                recent_check.get("date_and_number_set_mismatch_count"),
                0,
            )
            self.assertIs(recent_check.get("numbers_in_metadata"), False)
        else:
            self.assertEqual(validation.get("archive_records"), len(self.rows))

        self.assertEqual(set(validation.get("loto_gen_r60_by_year", {})),
                         {str(year) for year in range(2020, 2027)})
        for details in validation["loto_gen_r60_by_year"].values():
            self.assertEqual(details.get("status"), "MATCH")
            self.assertEqual(details.get("archive_only_count"), 0)
            self.assertEqual(details.get("independent_only_count"), 0)
            self.assertEqual(details.get("number_mismatch_count"), 0)
            self.assertEqual(details.get("source_parse_error_count"), 0)
        self.assertEqual(
            validation["lotobil_r60"].get("status"),
            "MATCH_AFTER_DOCUMENTED_SOURCE_NORMALIZATION",
        )
        self.assertEqual(validation["fetch_failures"], [])

    def test_latest_live_archive_audit_covers_current_archive(self):
        # Keep the older multi-year validation snapshot immutable; this
        # separate live-audit record documents the newer full archive scope.
        audit = self.payload.get("latest_live_archive_audit")
        self.assertIsInstance(audit, dict)
        self.assertEqual(
            audit.get("result"),
            "PASS_WITH_ACCEPTED_LOTOBIL_TAIL_LAG",
        )
        self.assertIsInstance(audit.get("workflow_run_id"), int)
        self.assertGreater(audit.get("workflow_run_id"), 0)
        self.assertEqual(audit.get("archive_records"), len(self.rows))
        self.assertEqual(audit.get("archive_first_date"), self.dates[0])
        self.assertEqual(audit.get("archive_last_date"), self.target_date)
        self.assertEqual(audit.get("fetch_failure_count"), 0)
        self.assertIs(audit.get("archive_modified_by_audit"), False)
        self.assertIs(audit.get("target_numbers_exposed"), False)
        self.assertIs(audit.get("draw_numbers_in_report"), False)

        yearly = audit.get("loto_gen_by_year")
        expected_years = {str(year) for year in range(2020, 2027)}
        self.assertIsInstance(yearly, dict)
        self.assertEqual(set(yearly), expected_years)
        for details in yearly.values():
            self.assertEqual(details.get("status"), "MATCH")
            self.assertEqual(
                details.get("archive_count"),
                details.get("source_count"),
            )
            self.assertEqual(details.get("date_mismatch_count"), 0)
            self.assertEqual(details.get("number_mismatch_count"), 0)
            self.assertEqual(details.get("parse_error_count"), 0)

        r60 = audit.get("lotobil_r60")
        self.assertIsInstance(r60, dict)
        self.assertEqual(
            r60.get("status"),
            "ACCEPTED_SOURCE_LAG_AT_TAIL",
        )
        self.assertEqual(r60.get("raw_comparison_status"), "SOURCE_LAG_AT_TAIL")
        self.assertEqual(r60.get("archive_count"), r60.get("common_date_count") + 1)
        self.assertEqual(r60.get("source_count"), r60.get("common_date_count"))
        self.assertEqual(r60.get("archive_only_count"), 1)
        self.assertEqual(r60.get("archive_only_dates"), [self.target_date])
        self.assertEqual(r60.get("independent_only_count"), 0)
        self.assertEqual(r60.get("number_mismatch_count"), 0)
        self.assertEqual(r60.get("source_parse_error_count"), 0)
        self.assertEqual(r60.get("raw_source_parse_error_count"), 0)
        self.assertEqual(r60.get("unresolved_index_gap_count"), 0)

        self.assertIs(audit.get("official_result_page_confirmed"), False)

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
