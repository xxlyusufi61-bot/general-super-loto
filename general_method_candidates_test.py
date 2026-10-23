import unittest
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
