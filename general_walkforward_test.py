import unittest
from datetime import date, timedelta

import general_walkforward as engine


def synthetic_rows(count=106, latest_numbers=None):
    start = date(2024, 1, 1)
    rows = []
    for index in range(count):
        draw_date = (start + timedelta(days=index)).isoformat()
        base = (index * 7) % 55 + 1
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
        self.assertTrue(report_a["regimes_evaluated_separately"])
        self.assertEqual(report_a["regime_counts"], report_b["regime_counts"])
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
        self.assertEqual(
            report_a["regimes"]["R54"]["status"],
            "INSUFFICIENT_PRETARGET_HISTORY",
        )

    def test_duplicate_date_is_rejected(self):
        rows = synthetic_rows()
        rows[-1]["date"] = rows[-2]["date"]
        with self.assertRaises(ValueError):
            engine.evaluate_rows(rows, min_train=10, cv_folds=5)

    def test_future_or_unordered_archive_is_rejected(self):
        rows = synthetic_rows()
        rows[5], rows[6] = rows[6], rows[5]
        with self.assertRaises(ValueError):
            engine.evaluate_rows(rows, min_train=10, cv_folds=5)

    def test_r54_and_r60_are_evaluated_independently(self):
        rows = []
        start_r54 = date(2020, 7, 20)
        for index in range(8):
            day = (start_r54 + timedelta(days=index)).isoformat()
            base = (index * 7) % 54 + 1
            nums = sorted({((base + offset * 9 - 1) % 54) + 1 for offset in range(6)})
            rows.append({"date": day, "numbers": nums})
        start_r60 = date(2020, 8, 2)
        for index in range(8):
            day = (start_r60 + timedelta(days=index)).isoformat()
            base = (index * 11) % 55 + 1
            nums = sorted({((base + offset * 7 - 1) % 60) + 1 for offset in range(6)})
            rows.append({"date": day, "numbers": nums})

        report = engine.evaluate_rows(rows, min_train=4, cv_folds=2)
        self.assertEqual(report["regime_counts"], {"R54": 8, "R60": 7})
        self.assertEqual(report["regimes"]["R54"]["status"], "EVALUATED")
        self.assertEqual(report["regimes"]["R60"]["status"], "EVALUATED")
        self.assertLess(report["regimes"]["R54"]["forward_walk_forward"]["first_test_date"], "2020-08-02")
        self.assertGreaterEqual(report["regimes"]["R60"]["forward_walk_forward"]["first_test_date"], "2020-08-02")

    def test_random_baseline_matches_each_regime_pool(self):
        rows = []
        start_r54 = date(2020, 7, 20)
        for index in range(8):
            day = (start_r54 + timedelta(days=index)).isoformat()
            base = (index * 7) % 54 + 1
            nums = sorted({((base + offset * 9 - 1) % 54) + 1 for offset in range(6)})
            rows.append({"date": day, "numbers": nums})
        start_r60 = date(2020, 8, 2)
        for index in range(8):
            day = (start_r60 + timedelta(days=index)).isoformat()
            base = (index * 11) % 60 + 1
            nums = sorted({((base + offset * 7 - 1) % 60) + 1 for offset in range(6)})
            rows.append({"date": day, "numbers": nums})

        report = engine.evaluate_rows(rows, min_train=4, cv_folds=2)
        for regime, pool_size, expected in (
            ("R54", 54, 0.666667),
            ("R60", 60, 0.6),
        ):
            details = report["regimes"][regime]
            self.assertEqual(details["number_pool_size"], pool_size)
            self.assertEqual(details["random_uniform_expected_hits_out_of_6"], expected)
            for section in (
                "forward_walk_forward",
                "reverse_retrospective_stress_test",
                "time_ordered_cross_validation",
            ):
                self.assertEqual(details[section]["number_pool_size"], pool_size)
                self.assertEqual(
                    details[section]["random_uniform_expected_hits_out_of_6"],
                    expected,
                )

    def test_r54_out_of_pool_pre_target_value_is_rejected(self):
        rows = []
        start = date(2020, 7, 20)
        for index in range(5):
            day = (start + timedelta(days=index)).isoformat()
            numbers = [1, 2, 3, 4, 5, 55] if index == 0 else [1, 2, 3, 4, 5, 6]
            rows.append({"date": day, "numbers": numbers})
        with self.assertRaisesRegex(ValueError, "R54 contains a pre-target value"):
            engine.evaluate_rows(rows, min_train=2, cv_folds=2)

    def test_invalid_fold_count_is_rejected(self):
        rows = synthetic_rows()
        with self.assertRaises(ValueError):
            engine.evaluate_rows(rows, min_train=10, cv_folds=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
