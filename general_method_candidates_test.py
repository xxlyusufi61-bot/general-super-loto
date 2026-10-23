import unittest
from unittest.mock import patch
from datetime import date, timedelta

import general_method_candidates as engine
import general_data_guard as guard


def synthetic_rows(count=35):
    rows = []
    start = date(2024, 1, 1)
    for i in range(count):
        # Six distinct deterministic numbers in 1..60.
        values = sorted({((i * 11 + j * 7) % 60) + 1 for j in range(6)})
        if len(values) != 6:
            raise AssertionError("bad synthetic fixture")
        rows.append({"date": (start + timedelta(days=i)).isoformat(), "numbers": values})
    return rows


class MethodCandidateTests(unittest.TestCase):
    def test_three_candidate_methods_produce_valid_picks_from_history(self):
        draws = [tuple(row["numbers"]) for row in synthetic_rows()]
        history = draws[:25]
        predictions = {
            "abc": engine.predict_abc_transition_to_c(history),
            "a_to_t": engine.predict_a_to_t_pair_counts(history),
            "mod5": engine.predict_mod5_state_to_t(history),
        }
        for name, pick in predictions.items():
            with self.subTest(method=name):
                self.assertIsNotNone(pick)
                self.assertEqual(len(pick), 6)
                self.assertEqual(len(set(pick)), 6)
                self.assertTrue(all(type(n) is int and 1 <= n <= 60 for n in pick))

    def test_report_marks_methods_as_experimental_and_target_hidden(self):
        rows = synthetic_rows()
        target_date = (date.fromisoformat(rows[-1]["date"]) + timedelta(days=1)).isoformat()
        report = engine.evaluate_rows(rows, target_date, min_train=6, folds=5)
        self.assertEqual(report["method_status"], "EXPERIMENTAL_CANDIDATES_NOT_CANONICAL")
        self.assertFalse(report["current_target_numbers_provided"])
        self.assertFalse(report["current_target_numbers_used_for_scoring"])
        self.assertFalse(report["current_target_numbers_exposed"])
        self.assertEqual(report["pre_target_history_records"], len(rows))
        self.assertEqual(report["canonical_method_definitions_status"]["A->B->C"], "OPEN_DEFINITION")
        self.assertEqual(report["canonical_method_definitions_status"]["A->T"], "OPEN_DEFINITION")
        self.assertEqual(report["canonical_method_definitions_status"]["MOD5->T"], "OPEN_DEFINITION")

    def test_target_or_future_rows_are_rejected(self):
        rows = synthetic_rows()
        target_date = rows[-1]["date"]
        with self.assertRaises(guard.DataGuardError):
            engine.evaluate_rows(rows, target_date, min_train=6, folds=5)

    def test_unordered_history_is_rejected(self):
        rows = synthetic_rows()
        rows[5], rows[6] = rows[6], rows[5]
        target_date = (date.fromisoformat(rows[-1]["date"]) + timedelta(days=1)).isoformat()
        with self.assertRaises(guard.DataGuardError):
            engine.evaluate_rows(rows, target_date, min_train=6, folds=5)

    def test_invalid_mod5_parameters_rejected(self):
        draws = [tuple(row["numbers"]) for row in synthetic_rows(10)]
        with self.assertRaises(ValueError):
            engine.predict_mod5_state_to_t(draws, k=0)

    def test_generator_rows_are_materialized_before_validation(self):
        rows = synthetic_rows()
        target_date = (date.fromisoformat(rows[-1]["date"]) + timedelta(days=1)).isoformat()
        report = engine.evaluate_rows(iter(rows), target_date, min_train=6, folds=5)
        self.assertEqual(report["pre_target_history_records"], len(rows))

    def test_random_null_reproducible_and_uses_same_test_draws(self):
        rows = synthetic_rows(30)
        draws = [tuple(row["numbers"]) for row in rows]
        forward, records = engine._evaluate_forward(draws, 6)
        usable = [record for record in records if record["scores"]]
        score_sets = {
            name: [record["scores"][name] for record in usable if name in record["scores"]]
            for name in engine.METHODS
        }
        a = engine._random_prediction_null(draws, usable, score_sets, replications=100, seed=123)
        b = engine._random_prediction_null(draws, usable, score_sets, replications=100, seed=123)
        self.assertEqual(a, b)
        self.assertEqual(a["replications"], 100)
        self.assertFalse(a["current_isolated_target_used"])
        self.assertEqual(a["methods"]["abc_transition_to_c"]["evaluated_draws"], len(score_sets["abc_transition_to_c"]))
        self.assertIn("random_mean_95pct_interval", a["methods"]["abc_transition_to_c"])

    def test_r54_records_are_excluded_from_r60_evaluation(self):
        rows = synthetic_rows(35)
        rows[0]["date"] = "2020-07-30"
        rows[0]["numbers"] = [1, 2, 3, 4, 5, 54]
        rows = sorted(rows, key=lambda row: row["date"])
        target_date = (date.fromisoformat(rows[-1]["date"]) + timedelta(days=1)).isoformat()
        report = engine.evaluate_rows(rows, target_date, min_train=6, folds=5)
        self.assertEqual(report["active_regime"], "R60")
        self.assertFalse(report["mixed_R54_R60_training_used"])
        self.assertEqual(report["excluded_prior_R54_regime_records"], 1)
        self.assertEqual(report["active_regime_history_records"], len(rows) - 1)

    def test_r54_target_date_is_rejected_until_r54_model_exists(self):
        rows = synthetic_rows(35)
        with self.assertRaises(ValueError):
            engine.evaluate_rows(rows, "2020-07-30", min_train=6, folds=5)

    def test_r54_records_are_excluded_from_r60_evaluation(self):
        rows = synthetic_rows(35)
        rows[0]["date"] = "2020-07-30"
        rows[0]["numbers"] = [1, 2, 3, 4, 5, 54]
        rows = sorted(rows, key=lambda row: row["date"])
        target_date = (date.fromisoformat(rows[-1]["date"]) + timedelta(days=1)).isoformat()
        report = engine.evaluate_rows(rows, target_date, min_train=6, folds=5)
        self.assertEqual(report["active_regime"], "R60")
        self.assertFalse(report["mixed_R54_R60_training_used"])
        self.assertEqual(report["excluded_prior_R54_regime_records"], 1)
        self.assertEqual(report["active_regime_history_records"], len(rows) - 1)

    def test_chronological_fold_summaries_are_not_claimed_as_nested_cv(self):
        rows = synthetic_rows(30)
        draws = [tuple(row["numbers"]) for row in rows]
        _, records = engine._evaluate_forward(draws, 6)
        usable = [record for record in records if record["scores"]]
        result = engine._walk_forward_fold_summaries(usable, 5)
        self.assertFalse(result["independent_fold_model_selection"])
        self.assertIn("not nested CV", result["protocol"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
