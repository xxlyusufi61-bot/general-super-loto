import unittest
from datetime import date, timedelta

from general_bidirectional_evaluation import (
    CATALOG_ID,
    evaluate_bidirectional_full_history,
    fixed_candidate_catalog,
)


def make_rows(start: date, count: int, pool_size: int, step: int = 2):
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

    def test_both_directions_cover_eligible_rows_and_keep_target_isolated(self):
        r54 = make_rows(date(2020, 1, 2), 46, 54, step=3)
        r60 = make_rows(date(2020, 8, 2), 75, 60, step=2)
        target_date = "2021-01-10"
        report = evaluate_bidirectional_full_history(
            {"R54": r54, "R60": r60},
            isolated_target_date=target_date,
        )
        r60_report = report["regimes"]["R60"]
        self.assertEqual(report["status"], "EVALUATED_BIDIRECTIONAL_ADAPTIVE_BASELINE")
        self.assertFalse(report["isolated_target_numbers_exposed"])
        self.assertFalse(report["isolated_target_in_forward_or_reverse_test"])
        self.assertEqual(r60_report["forward"]["evaluated_draw_count"], 75 - 42)
        self.assertEqual(r60_report["reverse_retrospective"]["evaluated_draw_count"], 75 - 42)
        self.assertEqual(r60_report["forward"]["evaluation_order"], "oldest_to_newest")
        self.assertEqual(r60_report["reverse_retrospective"]["evaluation_order"], "newest_to_oldest")
        self.assertFalse(r60_report["reverse_retrospective"]["predictive_evidence"])
        self.assertEqual(
            r60_report["forward_and_reverse_tested_date_intersection_count"],
            75 - 84 if 75 > 84 else 0,
        )
        self.assertEqual(
            r60_report["forward_per_draw"][0]["test_date"],
            r60[42]["date"],
        )
        self.assertEqual(
            r60_report["reverse_per_draw"][0]["test_date"],
            r60[-43]["date"],
        )
        for per_draw in (r60_report["forward_per_draw"] + r60_report["reverse_per_draw"]):
            self.assertLess(per_draw["test_date"], target_date)
            self.assertTrue(0 <= per_draw["hit_count"] <= 6)
            self.assertEqual(
                per_draw["hit_count"],
                len(per_draw["hit_column_positions_1_to_6"]),
            )
            self.assertTrue(all(1 <= pos <= 6 for pos in per_draw["hit_column_positions_1_to_6"]))
            self.assertFalse(per_draw["target_numbers_reported"])
            self.assertFalse(per_draw["predicted_number_values_reported"])
            self.assertNotIn("numbers", per_draw)

    def test_reverse_is_marked_retrospective_not_predictive(self):
        rows = make_rows(date(2020, 8, 2), 90, 60)
        report = evaluate_bidirectional_full_history(
            {"R54": [], "R60": rows},
            isolated_target_date="2021-10-01",
        )
        reverse = report["regimes"]["R60"]["reverse_retrospective"]
        self.assertFalse(reverse["predictive_evidence"])
        self.assertIn("retrospective", reverse["direction"])

    def test_target_or_future_draws_are_rejected_before_scoring(self):
        rows = make_rows(date(2020, 8, 2), 45, 60)
        rows.append({"date": "2021-01-10", "numbers": [1, 2, 3, 4, 5, 6]})
        with self.assertRaisesRegex(ValueError, "isolated target"):
            evaluate_bidirectional_full_history(
                {"R54": [], "R60": rows},
                isolated_target_date="2021-01-10",
            )

    def test_r54_and_r60_boundary_is_enforced(self):
        r54 = make_rows(date(2020, 1, 2), 44, 54, step=4)
        r60 = make_rows(date(2020, 8, 2), 44, 60, step=3)
        r54[-1]["date"] = "2020-08-02"
        r54.sort(key=lambda row: row["date"])
        with self.assertRaises(ValueError):
            evaluate_bidirectional_full_history(
                {"R54": r54, "R60": r60},
                isolated_target_date="2021-01-10",
            )


if __name__ == "__main__":
    unittest.main()
