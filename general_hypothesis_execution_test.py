import json
import unittest
from datetime import date, timedelta

import general_hypothesis_execution as engine


def count_candidate_matches_spec():
    return {
        "schema_version": 1,
        "score_expression": {
            "op": "aggregate_history_numbers",
            "aggregate": "count",
            "expression": {
                "op": "eq",
                "left": {"op": "var", "name": "value"},
                "right": {"op": "var", "name": "candidate"},
            },
        },
    }


def synthetic_rows(count, *, start="2024-01-01", pool_size=60):
    first = date.fromisoformat(start)
    rows = []
    for index in range(count):
        day = (first + timedelta(days=index)).isoformat()
        base = (index * 7) % pool_size + 1
        numbers = sorted({
            ((base + offset * 11 - 1) % pool_size) + 1
            for offset in range(6)
        })
        if len(numbers) != 6:
            raise AssertionError("Synthetic draw did not contain six distinct numbers.")
        rows.append({"date": day, "numbers": numbers})
    return rows


class SafeHypothesisExecutionTests(unittest.TestCase):
    def test_spec_validation_returns_detached_copy(self):
        spec = count_candidate_matches_spec()
        checked = engine.validate_execution_spec(spec)
        self.assertEqual(checked, spec)
        self.assertIsNot(checked, spec)
        self.assertIsNot(checked["score_expression"], spec["score_expression"])

    def test_ranker_returns_full_pool_and_deterministic_tie_break(self):
        history = synthetic_rows(8, start="2020-07-01", pool_size=54)
        constant_spec = {
            "schema_version": 1,
            "score_expression": {
                "op": "aggregate_history_numbers",
                "aggregate": "count",
                "expression": {
                    "op": "gt",
                    "left": {"op": "const", "value": 1},
                    "right": {"op": "const", "value": 2},
                },
            },
        }
        first = engine.rank_candidates(
            history,
            cutoff_date="2020-07-09",
            number_pool_size=54,
            execution_spec=constant_spec,
        )
        second = engine.rank_candidates(
            history,
            cutoff_date="2020-07-09",
            number_pool_size=54,
            execution_spec=constant_spec,
        )
        self.assertEqual(first, second)
        self.assertEqual([item["number"] for item in first], list(range(1, 55)))
        self.assertEqual({item["score"] for item in first}, {0})

    def test_r54_and_r60_reject_cross_regime_dates(self):
        r54_rows = synthetic_rows(4, start="2020-07-30", pool_size=54)
        with self.assertRaisesRegex(ValueError, "outside the R54 regime"):
            engine.rank_candidates(
                r54_rows,
                cutoff_date="2020-08-01",
                number_pool_size=54,
                execution_spec=count_candidate_matches_spec(),
            )
        r60_rows = synthetic_rows(3, start="2020-07-30", pool_size=60)
        with self.assertRaisesRegex(ValueError, "outside the R60 regime"):
            engine.rank_candidates(
                r60_rows,
                cutoff_date="2020-08-03",
                number_pool_size=60,
                execution_spec=count_candidate_matches_spec(),
            )

    def test_regime_specific_cutoff_dates_are_enforced(self):
        r54_history = synthetic_rows(3, start="2020-07-20", pool_size=54)
        with self.assertRaisesRegex(ValueError, "R54 scoring cutoff must be before"):
            engine.rank_candidates(
                r54_history,
                cutoff_date="2020-08-02",
                number_pool_size=54,
                execution_spec=count_candidate_matches_spec(),
            )
        r60_history = synthetic_rows(3, start="2020-08-02", pool_size=60)
        with self.assertRaisesRegex(ValueError, "R60 scoring cutoff must be on or after"):
            engine.rank_candidates(
                r60_history,
                cutoff_date="2020-08-01",
                number_pool_size=60,
                execution_spec=count_candidate_matches_spec(),
            )

    def test_candidate_score_expression_runs_only_over_earlier_history(self):
        history = synthetic_rows(12, pool_size=60)
        ranked = engine.rank_candidates(
            history,
            cutoff_date="2024-01-13",
            number_pool_size=60,
            execution_spec=count_candidate_matches_spec(),
        )
        self.assertEqual(len(ranked), 60)
        self.assertEqual(ranked, sorted(ranked, key=lambda item: (-item["score"], item["number"])))
        self.assertTrue(all(1 <= item["number"] <= 60 for item in ranked))
        self.assertTrue(all(isinstance(item["score"], (int, float)) for item in ranked))

    def test_cutoff_rejects_target_date_in_history(self):
        history = synthetic_rows(4)
        with self.assertRaisesRegex(ValueError, "on or after the scoring cutoff"):
            engine.rank_candidates(
                history,
                cutoff_date="2024-01-04",
                number_pool_size=60,
                execution_spec=count_candidate_matches_spec(),
            )

    def test_rejects_r54_out_of_pool_value(self):
        history = synthetic_rows(3, start="2020-07-01", pool_size=54)
        history[0]["numbers"][-1] = 55
        with self.assertRaisesRegex(ValueError, "invalid six-number draw"):
            engine.rank_candidates(
                history,
                cutoff_date="2020-07-04",
                number_pool_size=54,
                execution_spec=count_candidate_matches_spec(),
            )

    def test_rejects_unknown_operator_and_extra_keys(self):
        bad_operator = {
            "schema_version": 1,
            "score_expression": {
                "op": "run_python",
                "source": "import os",
            },
        }
        with self.assertRaisesRegex(ValueError, "operator is not in the allowlist"):
            engine.validate_execution_spec(bad_operator)

        extra_key = count_candidate_matches_spec()
        extra_key["source_code"] = "print('not allowed')"
        with self.assertRaisesRegex(ValueError, "keys are invalid"):
            engine.validate_execution_spec(extra_key)

    def test_rejects_nested_or_multiple_aggregates(self):
        nested = {
            "schema_version": 1,
            "score_expression": {
                "op": "aggregate_history_numbers",
                "aggregate": "sum",
                "expression": {
                    "op": "aggregate_history_numbers",
                    "aggregate": "count",
                    "expression": {"op": "const", "value": 1},
                },
            },
        }
        with self.assertRaisesRegex(ValueError, "nested history aggregates"):
            engine.validate_execution_spec(nested)

        multiple = {
            "schema_version": 1,
            "score_expression": {
                "op": "add",
                "left": count_candidate_matches_spec()["score_expression"],
                "right": count_candidate_matches_spec()["score_expression"],
            },
        }
        with self.assertRaisesRegex(ValueError, "exactly one history aggregate"):
            engine.validate_execution_spec(multiple)

    def test_rejects_invalid_ast_size_and_depth(self):
        deep = {"op": "var", "name": "candidate"}
        for _ in range(engine.MAX_AST_DEPTH + 2):
            deep = {"op": "abs", "arg": deep}
        with self.assertRaisesRegex(ValueError, "depth exceeds"):
            engine.validate_execution_spec({
                "schema_version": 1,
                "score_expression": {
                    "op": "add",
                    "left": count_candidate_matches_spec()["score_expression"],
                    "right": deep,
                },
            })

    def test_runtime_division_by_zero_fails_closed(self):
        spec = {
            "schema_version": 1,
            "score_expression": {
                "op": "div",
                "left": count_candidate_matches_spec()["score_expression"],
                "right": {"op": "const", "value": 0},
            },
        }
        with self.assertRaisesRegex(ValueError, "division by zero"):
            engine.rank_candidates(
                synthetic_rows(3),
                cutoff_date="2024-01-04",
                number_pool_size=60,
                execution_spec=spec,
            )

    def test_holdout_evaluation_is_chronological_and_reports_only_aggregates(self):
        discovery = synthetic_rows(45, start="2024-01-01", pool_size=60)
        validation = synthetic_rows(5, start="2024-02-15", pool_size=60)
        report = engine.evaluate_holdout(
            discovery,
            validation,
            cutoff_date="2024-02-20",
            number_pool_size=60,
            execution_spec=count_candidate_matches_spec(),
            min_train_draws=20,
        )
        self.assertEqual(report["status"], "EVALUATED")
        self.assertEqual(report["validation_draw_count"], 5)
        self.assertEqual(report["number_pool_size"], 60)
        self.assertEqual(report["uniform_random_expected_hits_out_of_6"], 0.6)
        self.assertFalse(report["current_isolated_target_included"])
        self.assertFalse(report["current_target_numbers_exposed"])
        self.assertFalse(report["per_draw_target_numbers_reported"])
        self.assertFalse(report["candidate_rankings_reported"])
        self.assertFalse(report["automatic_candidate_selection"])
        self.assertFalse(report["method_registry_modified"])
        self.assertNotIn('"numbers":', json.dumps(report))

    def test_holdout_rejects_overlap_or_current_target(self):
        discovery = synthetic_rows(20, start="2024-01-01")
        validation = synthetic_rows(2, start="2024-01-21")
        with self.assertRaisesRegex(ValueError, "validation window contains the isolated target"):
            engine.evaluate_holdout(
                discovery,
                validation,
                cutoff_date="2024-01-22",
                number_pool_size=60,
                execution_spec=count_candidate_matches_spec(),
                min_train_draws=10,
            )
        with self.assertRaisesRegex(ValueError, "discovery history must end before the validation window begins"):
            engine.evaluate_holdout(
                discovery + [validation[0]],
                validation,
                cutoff_date="2024-01-24",
                number_pool_size=60,
                execution_spec=count_candidate_matches_spec(),
                min_train_draws=10,
            )

    def test_time_ordered_cv_uses_contiguous_blocks_without_shuffling(self):
        discovery = synthetic_rows(45, start="2024-01-01", pool_size=60)
        validation = synthetic_rows(6, start="2024-02-15", pool_size=60)
        report = engine.evaluate_time_ordered_cv(
            discovery,
            validation,
            cutoff_date="2024-02-21",
            number_pool_size=60,
            execution_spec=count_candidate_matches_spec(),
            folds=3,
            min_train_draws=20,
        )
        self.assertEqual(report["status"], "EVALUATED")
        self.assertEqual(report["folds"], 3)
        self.assertEqual(report["validation_draw_count"], 6)
        self.assertFalse(report["current_target_numbers_exposed"])
        self.assertFalse(report["automatic_candidate_selection"])
        self.assertLess(
            report["fold_reports"][0]["validation_end_date"],
            report["fold_reports"][1]["validation_start_date"],
        )

    def test_reverse_stress_is_explicitly_not_predictive_evidence(self):
        history = synthetic_rows(45, start="2024-01-01", pool_size=60)
        report = engine.evaluate_reverse_retrospective_stress(
            history,
            cutoff_date="2024-02-15",
            number_pool_size=60,
            execution_spec=count_candidate_matches_spec(),
            test_draw_count=5,
            min_train_draws=20,
        )
        self.assertEqual(report["status"], "EVALUATED_RETROSPECTIVE_ONLY")
        self.assertFalse(report["predictive_evidence"])
        self.assertFalse(report["current_isolated_target_included"])
        self.assertFalse(report["current_target_numbers_exposed"])
        self.assertEqual(report["uniform_random_expected_hits_out_of_6"], 0.6)
        self.assertEqual(report["evaluation_order"], "newest_to_oldest")
        self.assertGreater(report["first_test_date"], report["last_test_date"])

    def test_holdout_reports_regime_specific_random_reference(self):
        discovery = synthetic_rows(20, start="2020-07-01", pool_size=54)
        validation = synthetic_rows(3, start="2020-07-21", pool_size=54)
        report = engine.evaluate_holdout(
            discovery,
            validation,
            cutoff_date="2020-07-24",
            number_pool_size=54,
            execution_spec=count_candidate_matches_spec(),
            min_train_draws=10,
        )
        self.assertEqual(report["uniform_random_expected_hits_out_of_6"], 0.666667)

    def test_synthetic_contract_runner_checks_both_regimes(self):
        result = engine.run_synthetic_contract_tests(count_candidate_matches_spec())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["regimes_checked"], ["R54", "R60"])
        self.assertEqual(result["ranked_values_checked"], 114)
        self.assertTrue(result["deterministic"])
        self.assertFalse(result["input_mutated"])
        self.assertFalse(result["real_archive_used"])
        self.assertFalse(result["target_values_used"])

    def test_input_history_is_not_mutated(self):
        discovery = synthetic_rows(20)
        validation = synthetic_rows(2, start="2024-01-21")
        before = json.dumps([discovery, validation], sort_keys=True)
        engine.evaluate_holdout(
            discovery,
            validation,
            cutoff_date="2024-01-23",
            number_pool_size=60,
            execution_spec=count_candidate_matches_spec(),
            min_train_draws=10,
        )
        self.assertEqual(json.dumps([discovery, validation], sort_keys=True), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
