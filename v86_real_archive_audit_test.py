import unittest
from datetime import date, timedelta

from v86_leakage_safe_evaluator import Draw
from v86_real_archive_audit import (
    DEFAULT_LOCKED_DATES,
    prepare_r60_history,
    run_audit,
)


def make_rows(count=240, start=date(2020, 1, 2)):
    rows = []
    day = start
    index = 0
    while len(rows) < count:
        # Synthetic deterministic unique sets; no real draw data is embedded.
        base = (index * 7) % 55 + 1
        numbers = sorted({((base + offset * (index % 3 + 2) - 1) % 60) + 1 for offset in range(6)})
        candidate = tuple(numbers)
        if len(candidate) == 6:
            rows.append({"date": day.isoformat(), "numbers": list(candidate)})
            day += timedelta(days=2)
            index += 1
        else:
            index += 1
    return rows


class RealArchiveAuditTests(unittest.TestCase):
    def test_target_or_future_rows_fail_closed(self):
        rows = make_rows(30)
        rows.append({"date": "2021-01-01", "numbers": [1, 2, 3, 4, 5, 6]})
        with self.assertRaisesRegex(ValueError, "target-date or future"):
            prepare_r60_history(rows, "2021-01-01")

    def test_locked_dates_are_excluded_and_r54_is_not_used(self):
        rows = make_rows(300)
        # Ensure both locked dates exist in the test fixture.
        rows.extend([
            {"date": "2021-01-26", "numbers": [1, 2, 3, 4, 5, 6]},
            {"date": "2021-01-28", "numbers": [2, 3, 4, 5, 6, 7]},
        ])
        rows.sort(key=lambda row: row["date"])
        # Remove any date collisions from this artificial fixture.
        deduped = []
        seen = set()
        for row in rows:
            if row["date"] not in seen:
                deduped.append(row)
                seen.add(row["date"])
        r60, summary = prepare_r60_history(deduped, "2022-12-31")
        self.assertTrue(all(draw.day >= date(2020, 8, 2) for draw in r60))
        self.assertTrue(all(draw.day.isoformat() not in DEFAULT_LOCKED_DATES for draw in r60))
        self.assertGreaterEqual(summary["r54_rows_excluded_from_this_audit"], 0)

    def test_small_candidate_grid_completes_with_frozen_final_interval(self):
        rows = make_rows(360)
        report = run_audit(
            rows,
            target_date="2023-01-01",
            candidates=[("mod5diff", "sign", 1), ("sumdiff", "sign", 1)],
            min_train=40,
            validation_fraction=0.7,
            folds=3,
            random_replications=100,
            seed=42,
        )
        self.assertEqual(report["status"], "COMPLETED_EXPERIMENTAL_AUDIT")
        self.assertEqual(report["canonical_status"], "NOT_CANONICAL_NOT_APPROVED")
        self.assertTrue(report["current_target"]["excluded_from_scoring"])
        self.assertFalse(report["current_target"]["numbers_used_for_scoring"])
        self.assertTrue(report["protocol"]["frozen_final_interval"]["selected_candidate_not_reselected"])
        self.assertEqual(len(report["chronological_final_folds"]), 3)
        self.assertFalse(report["reverse_retrospective_stress_test"]["predictive_evidence"])
        self.assertEqual(report["random_prediction_reference"]["replications"], 100)


    def test_archive_row_counts_distinguish_locked_exclusions(self):
        rows = make_rows(300)
        # Use a fixture where the locked date is guaranteed to be present.
        rows = [row for row in rows if row["date"] not in DEFAULT_LOCKED_DATES]
        rows.extend([
            {"date": "2021-01-26", "numbers": [1, 2, 3, 4, 5, 6]},
            {"date": "2021-01-28", "numbers": [2, 3, 4, 5, 6, 7]},
        ])
        rows.sort(key=lambda row: row["date"])
        r60, summary = prepare_r60_history(rows, "2022-12-31")
        self.assertEqual(summary["pre_target_rows_before_locked_date_exclusion"], len(rows))
        self.assertEqual(
            summary["pre_target_rows_after_locked_date_exclusion"],
            len(rows) - 2,
        )
        self.assertEqual(len(summary["locked_dates_excluded"]), 2)
        self.assertTrue(all(draw.day.isoformat() not in DEFAULT_LOCKED_DATES for draw in r60))

    def test_duplicate_dates_fail_closed(self):
        rows = make_rows(20)
        rows[1]["date"] = rows[0]["date"]
        with self.assertRaisesRegex(ValueError, "Duplicate draw date"):
            prepare_r60_history(rows, "2021-01-01")


if __name__ == "__main__":
    unittest.main()
