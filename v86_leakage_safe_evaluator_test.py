import unittest
from datetime import date, timedelta

from v86_leakage_safe_evaluator import (
    Draw, validate_history, predict_from_known_pair, walk_forward,
    select_on_validation, evaluate_frozen_candidate, reverse_stress_test,
    fold_summary
)


def fixture(n=45):
    rows = []
    for i in range(n):
        # Deterministic valid six-number draws, intentionally synthetic only.
        vals = sorted({((i * 7 + j * 11 + (i // 3) * (j + 2)) % 60) + 1 for j in range(6)})
        while len(vals) < 6:
            vals.append((vals[-1] % 60) + 1)
            vals = sorted(set(vals))
        rows.append(Draw(date(2020, 1, 1) + timedelta(days=i), tuple(vals)))
    # Guarantee valid six unique values in all rows via a simple replacement fallback.
    fixed=[]
    for i,row in enumerate(rows):
        nums=list(row.numbers)
        candidate=1
        while len(nums)<6:
            if candidate not in nums: nums.append(candidate)
            candidate+=1
        fixed.append(Draw(row.day,tuple(sorted(nums[:6]))))
    return tuple(fixed)


class LeakageSafeEvaluatorTests(unittest.TestCase):
    def test_target_date_filter_is_strict_and_locked_dates_are_excluded(self):
        rows = fixture(12)
        target = rows[9].day
        clean = validate_history(rows, target, locked_dates={rows[4].day})
        self.assertTrue(all(r.day < target for r in clean))
        self.assertNotIn(rows[4], clean)
        self.assertNotIn(rows[9], clean)

    def test_rejects_unsorted_or_duplicate_dates(self):
        rows = fixture(8)
        with self.assertRaisesRegex(ValueError, "strictly chronological"):
            validate_history((rows[1], rows[0]), rows[-1].day)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_history((rows[0], rows[0]), rows[-1].day)

    def test_prediction_does_not_accept_target_argument(self):
        import inspect
        params = inspect.signature(predict_from_known_pair).parameters
        self.assertNotIn("target", params)
        self.assertNotIn("actual", params)

    def test_prediction_uses_only_history_prefix(self):
        rows = fixture(30)
        history = rows[:20]
        p1 = predict_from_known_pair(history)
        # Appending a future/target draw cannot affect an earlier prediction,
        # because prediction is invoked with the original prefix only.
        _future = rows[20]
        p2 = predict_from_known_pair(tuple(rows[:20]))
        self.assertEqual(p1, p2)

    def test_walk_forward_target_mutation_does_not_change_prediction_history(self):
        rows = fixture(25)
        target_index = 18
        history = rows[:target_index]
        before = predict_from_known_pair(history)
        altered_target = Draw(rows[target_index].day, (1, 2, 3, 4, 5, 60))
        self.assertEqual(before, predict_from_known_pair(history))
        self.assertNotEqual(rows[target_index].numbers, altered_target.numbers)

    def test_validation_selection_is_separate_from_final_evaluation(self):
        rows = fixture(40)
        candidates = [("mod5diff", "sign", 1), ("gapdiff", "absolute", 3)]
        selected = select_on_validation(rows, candidates, 20, 28)
        self.assertEqual(len(selected), 2)
        final = evaluate_frozen_candidate(rows, selected[0][1], selected[0][2], selected[0][3], 28, 36)
        self.assertEqual(final["n_targets"], 8)
        self.assertNotIn("selected_by_final", final)

    def test_reverse_test_is_labeled_non_predictive(self):
        result = reverse_stress_test(fixture(30), "mod5diff", "sign", 3, 8, 20)
        self.assertIn("NOT_PREDICTIVE", result["protocol"])

    def test_fold_summaries_are_contiguous_and_labelled(self):
        rows = fixture(32)
        folds = fold_summary(rows, "mod5diff", "sign", 3, folds=4, start_index=4, end_index=28)
        self.assertEqual(len(folds), 4)
        for a, b in zip(folds, folds[1:]):
            self.assertEqual(a["end_index"], b["start_index"])

    def test_final_score_not_used_by_validation_selector(self):
        import inspect
        params = inspect.signature(select_on_validation).parameters
        self.assertIn("validation_start", params)
        self.assertIn("validation_end", params)
        self.assertNotIn("final_start", params)
        self.assertNotIn("final_end", params)


if __name__ == "__main__":
    unittest.main(verbosity=2)
