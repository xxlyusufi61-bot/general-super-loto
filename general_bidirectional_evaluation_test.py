import unittest
from datetime import date, timedelta

from general_bidirectional_evaluation import (
    CATALOG_ID,
    _rank_catalog_history,
    evaluate_bidirectional_full_history,
    fixed_candidate_catalog,
)
from general_hypothesis_execution import rank_candidates


def make_rows(start: date, count: int, pool_size: int = 60, step: int = 2):
    rows = []
    for index in range(count):
        draw_day = (start + timedelta(days=step * index)).isoformat()
        base = (index * 7) % pool_size + 1
        numbers = sorted({((base + offset * 11 - 1) % pool_size) + 1 for offset in range(6)})
        rows.append({"date": draw_day, "numbers": numbers})
    return rows


class BidirectionalFullHistoryTests(unittest.TestCase):
    def test_catalog_is_frozen_dsl_valid_and_never_uses_archive_values(self):
        catalog = fixed_candidate_catalog()
        self.assertEqual(len(catalog), 6)
        self.assertEqual(len({item["spec_id"] for item in catalog}), 6)
        self.assertEqual(len({item["spec_sha256"] for item in catalog}), 6)
        self.assertEqual(catalog[0]["spec_id"], "EXACT_FREQUENCY_ALL")
        self.assertEqual(CATALOG_ID, "GENERAL-SAFE-DSL-CATALOG-v1")

    def test_recent_catalog_window_follows_each_direction(self):
        rows = []
        for index in range(10):
            numbers = list(range(1, 7)) if index < 5 else list(range(55, 61))
            rows.append({
                "date": (date(2024, 1, 1) + timedelta(days=index)).isoformat(),
                "numbers": numbers,
            })
        candidate = next(
            item for item in fixed_candidate_catalog()
            if item["spec_id"] == "EXACT_FREQUENCY_RECENT_5_DRAWS"
        )
        forward = _rank_catalog_history(
            rows, number_pool_size=60, candidate=candidate, direction="forward"
        )
        reverse = _rank_catalog_history(
            rows, number_pool_size=60, candidate=candidate, direction="reverse"
        )
        self.assertEqual([item["number"] for item in forward[:6]], [55, 56, 57, 58, 59, 60])
        self.assertEqual([item["number"] for item in reverse[:6]], [1, 2, 3, 4, 5, 6])

    def test_fast_catalog_ranker_matches_safe_dsl_interpreter(self):
        history = make_rows(date(2024, 1, 2), 50)
        for candidate in fixed_candidate_catalog():
            with self.subTest(spec_id=candidate["spec_id"]):
                expected = rank_candidates(
                    history,
                    cutoff_date="2025-01-01",
                    number_pool_size=60,
                    execution_spec=candidate["execution_spec"],
                )
                actual = _rank_catalog_history(
                    history, number_pool_size=60, candidate=candidate
                )
                self.assertEqual(actual, expected)

    def test_both_directions_cover_eligible_rows_and_keep_target_isolated(self):
        rows = make_rows(date(2020, 8, 2), 75)
        target_date = (date.fromisoformat(rows[-1]["date"]) + timedelta(days=2)).isoformat()
        report = evaluate_bidirectional_full_history(
            {"R60": rows}, isolated_target_date=target_date
        )
        details = report["regimes"]["R60"]
        self.assertEqual(report["status"], "EVALUATED_BIDIRECTIONAL_ADAPTIVE_BASELINE")
        self.assertFalse(report["isolated_target_numbers_exposed"])
        self.assertFalse(report["isolated_target_in_forward_or_reverse_test"])
        self.assertEqual(details["forward"]["evaluated_draw_count"], 75 - 42)
        self.assertEqual(details["reverse_retrospective"]["evaluated_draw_count"], 75 - 42)
        self.assertEqual(details["forward"]["evaluation_order"], "oldest_to_newest")
        self.assertEqual(details["reverse_retrospective"]["evaluation_order"], "newest_to_oldest")
        self.assertFalse(details["reverse_retrospective"]["predictive_evidence"])
        self.assertTrue(all(row["test_date"] < target_date for row in details["forward_per_draw"] + details["reverse_per_draw"]))
        self.assertTrue(all(not row["target_numbers_reported"] and not row["predicted_number_values_reported"] for row in details["forward_per_draw"] + details["reverse_per_draw"]))
        self.assertEqual(set(report["regimes"]), {"R60"})

    def test_reverse_is_retrospective_not_predictive(self):
        rows = make_rows(date(2020, 8, 2), 90)
        report = evaluate_bidirectional_full_history(
            {"R60": rows}, isolated_target_date="2021-01-10"
        )
        reverse = report["regimes"]["R60"]["reverse_retrospective"]
        self.assertFalse(reverse["predictive_evidence"])
        self.assertIn("retrospective", reverse["direction"])

    def test_target_or_future_draws_are_rejected_before_scoring(self):
        rows = make_rows(date(2020, 8, 2), 45)
        target_date = rows[-1]["date"]
        rows.append({"date": target_date, "numbers": [1, 2, 3, 4, 5, 6]})
        with self.assertRaisesRegex(ValueError, "isolated target"):
            evaluate_bidirectional_full_history(
                {"R60": rows}, isolated_target_date=target_date
            )

    def test_pre_scope_history_is_rejected(self):
        rows = make_rows(date(2020, 8, 2), 45)
        rows.insert(0, {"date": "2020-07-30", "numbers": [1, 2, 3, 4, 5, 6]})
        with self.assertRaises(ValueError):
            evaluate_bidirectional_full_history(
                {"R60": rows}, isolated_target_date="2021-01-10"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
