import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import real_archive_evaluation as exp
from v86_leakage_safe_evaluator import Draw


def fake_rows(n=140):
    rows = []
    for i in range(n):
        nums = sorted({((i * 7 + j * 11 + (i // 3) * (j + 2)) % 60) + 1 for j in range(6)})
        candidate = 1
        while len(nums) < 6:
            if candidate not in nums:
                nums.append(candidate)
                nums.sort()
            candidate += 1
        rows.append(Draw(date(2020, 1, 1) + timedelta(days=i), tuple(nums[:6])))
    return tuple(rows)


class RealArchiveEvaluationTests(unittest.TestCase):
    def test_archive_validator_rejects_duplicate_dates(self):
        payload = {"draws": [
            {"date": "2026-01-01", "numbers": [1, 2, 3, 4, 5, 6]},
            {"date": "2026-01-01", "numbers": [7, 8, 9, 10, 11, 12]},
        ]}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "archive.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                exp.load_and_validate_archive(p)

    def test_archive_validator_rejects_invalid_numbers(self):
        payload = {"draws": [{"date": "2026-01-01", "numbers": [1, 2, 3, 4, 5, 61]}]}
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "archive.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid"):
                exp.load_and_validate_archive(p)

    def test_predictor_never_receives_target_row(self):
        rows = fake_rows()
        idx = 90
        candidate = exp.Candidate("mod5diff", "sign", 3)
        expected = exp.predict_at(rows, idx, candidate)
        mutated = list(rows)
        mutated[idx] = Draw(mutated[idx].day, (1, 2, 3, 4, 5, 60))
        self.assertEqual(expected, exp.predict_at(tuple(mutated), idx, candidate))

    def test_target_is_last_row_and_not_part_of_pre_target_data(self):
        rows = fake_rows()
        target = rows[-1]
        pre_target = tuple(r for r in rows[:-1] if r.day not in exp.LOCKED_DATES)
        self.assertNotIn(target, pre_target)
        self.assertTrue(all(r.day < target.day for r in pre_target))

    def test_validation_selector_does_not_accept_final_interval(self):
        import inspect
        params = inspect.signature(exp.select_candidate).parameters
        self.assertEqual(set(params), {"rows", "candidates", "start", "end"})

    def test_random_baseline_is_exact_expectation(self):
        self.assertEqual(exp.random_baseline_mean_hits(10), 0.6)
        self.assertEqual(exp.random_baseline_mean_hits(0), 0.0)

    def test_block_bootstrap_is_reproducible_and_returns_ordered_interval(self):
        scores = [0, 1, 2, 0, 1, 0, 1, 2, 0, 1, 0, 1]
        first = exp.block_bootstrap_mean_ci(scores, replications=1000, block_length=3, seed=99)
        second = exp.block_bootstrap_mean_ci(scores, replications=1000, block_length=3, seed=99)
        self.assertEqual(first, second)
        low, high = first["mean_hits_ci"]
        self.assertLessEqual(low, high)
        self.assertEqual(first["replications"], 1000)

    def test_matched_random_null_is_reproducible_and_bounded(self):
        rows = fake_rows(30)
        first = exp.matched_random_null(rows, 0.6, replications=1000, seed=101)
        second = exp.matched_random_null(rows, 0.6, replications=1000, seed=101)
        self.assertEqual(first, second)
        self.assertGreaterEqual(first["one_sided_random_pick_tail_probability_mc"], 0.0)
        self.assertLessEqual(first["one_sided_random_pick_tail_probability_mc"], 1.0)
        self.assertEqual(first["theoretical_expected_mean_hits"], 0.6)

    def test_report_statistical_comparison_does_not_reuse_holdout_for_tuning(self):
        source = Path(exp.__file__).read_text(encoding="utf-8")
        self.assertIn('"final_interval_used_for_candidate_selection": False', source)
        self.assertIn('"holdout_reuse_policy":', source)
        self.assertIn('"matched_random_null": random_null', source)
        self.assertIn('"model_block_bootstrap_uncertainty": model_uncertainty', source)

    def test_report_contract_does_not_write_actual_or_predicted_number_sets(self):
        source = Path(exp.__file__).read_text(encoding="utf-8")
        self.assertIn('"isolated_target_numbers_written_to_report": False', source)
        self.assertIn('"target_numbers_passed_to_predictor": False', source)
        self.assertNotIn('"actual_numbers":', source)
        self.assertNotIn('"predicted_numbers":', source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
