import unittest
from datetime import date, timedelta

import general_walkforward as engine


def synthetic_rows(count=106, *, start="2024-01-01", latest_numbers=None):
    first = date.fromisoformat(start)
    rows = []
    for index in range(count):
        draw_date = (first + timedelta(days=index)).isoformat()
        base = (index * 7) % 60 + 1
        numbers = sorted({((base + offset * 9 - 1) % 60) + 1 for offset in range(6)})
        if len(numbers) != 6:
            raise AssertionError("fixture generation did not produce six distinct numbers")
        rows.append({"date": draw_date, "numbers": numbers})
    if latest_numbers is not None:
        rows[-1]["numbers"] = latest_numbers
    return rows


class WalkForwardTests(unittest.TestCase):
    def test_target_is_excluded_and_numbers_do_not_change_scores(self):
        first = synthetic_rows(latest_numbers=[1, 2, 3, 4, 5, 6])
        second = synthetic_rows(latest_numbers=[55, 56, 57, 58, 59, 60])
        report_a = engine.evaluate_rows(first, min_train=10, cv_folds=5)
        report_b = engine.evaluate_rows(second, min_train=10, cv_folds=5)
        self.assertTrue(report_a["current_target_excluded_from_scoring"])
        self.assertFalse(report_a["current_target_numbers_used_for_scoring"])
        self.assertFalse(report_a["current_target_numbers_exposed"])
        self.assertEqual(report_a["target_date"], first[-1]["date"])
        self.assertEqual(report_a["archive_records"], len(first))
        self.assertEqual(report_a["pre_target_history_records"], len(first) - 1)
        self.assertTrue(report_a["only_active_scope_evaluated"])
        self.assertEqual(report_a["regime_counts"], {"R60": len(first) - 1})
        self.assertEqual(
            report_a["regimes"]["R60"]["forward_walk_forward"],
            report_b["regimes"]["R60"]["forward_walk_forward"],
        )
        self.assertEqual(
            report_a["regimes"]["R60"]["reverse_retrospective_stress_test"],
            report_b["regimes"]["R60"]["reverse_retrospective_stress_test"],
        )
        self.assertEqual(
            report_a["regimes"]["R60"]["time_ordered_cross_validation"],
            report_b["regimes"]["R60"]["time_ordered_cross_validation"],
        )
        self.assertEqual(set(report_a["regimes"]), {"R60"})

    def test_duplicate_date_is_rejected(self):
        rows = synthetic_rows()
        rows[-1]["date"] = rows[-2]["date"]
        with self.assertRaises(ValueError):
            engine.evaluate_rows(rows, min_train=10, cv_folds=5)

    def test_unordered_archive_is_rejected(self):
        rows = synthetic_rows()
        rows[5], rows[6] = rows[6], rows[5]
        with self.assertRaises(ValueError):
            engine.evaluate_rows(rows, min_train=10, cv_folds=5)

    def test_pre_scope_record_is_rejected(self):
        rows = synthetic_rows(start="2020-08-02", count=106)
        rows.insert(0, {"date": "2020-07-30", "numbers": [1, 2, 3, 4, 5, 6]})
        with self.assertRaisesRegex(ValueError, "before the active research scope"):
            engine.evaluate_rows(rows, min_train=10, cv_folds=5)

    def test_uniform_baseline_is_for_one_6_of_60_pool(self):
        rows = synthetic_rows(start="2020-08-02", count=106)
        report = engine.evaluate_rows(rows, min_train=10, cv_folds=5)
        detail = report["regimes"]["R60"]
        self.assertEqual(detail["number_pool_size"], 60)
        self.assertEqual(detail["random_uniform_expected_hits_out_of_6"], 0.6)
        self.assertEqual(report["research_scope_start_date"], "2020-08-02")

    def test_invalid_fold_count_is_rejected(self):
        rows = synthetic_rows()
        with self.assertRaises(ValueError):
            engine.evaluate_rows(rows, min_train=10, cv_folds=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
