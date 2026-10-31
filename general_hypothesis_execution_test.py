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
        history = synthetic_rows(8, start="2020-08-02", pool_size=60)
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
            cutoff_date="2020-08-10",
            number_pool_size=60,
            execution_spec=constant_spec,
        )
        second = engine.rank_candidates(
            history,
            cutoff_date="2020-08-10",
            number_pool_size=54,
            execution_spec=constant_spec,
        )
        self.assertEqual(first, second)
        self.assertEqual([item["number"] for item in first], list(range(1, 61)))
        self.assertEqual({item["score"] for item in first}, {0})

    def test_pre_scope_history_is_rejected(self):
        history = synthetic_rows(4, start="2020-07-30", pool_size=60)
        with self.assertRaisesRegex(ValueError, "outside the active 6-of-60 research scope"):
            engine.rank_candidates(
                history,
                cutoff_date="2020-08-05",
                number_pool_size=60,
                execution_spec=count_candidate_matches_spec(),
            )


    def test_cutoff_before_active_scope_is_rejected(self):
        history = synthetic_rows(3, start="2020-08-02", pool_size=60)
        with self.assertRaisesRegex(ValueError, "scoring cutoff is before the active research scope"):
            engine.rank_candidates(
                history,
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

    def test_rejects_number_outside_the_6_of_60_pool(self):
        history = synthetic_rows(3, start="2020-08-02", pool_size=60)
        history[0]["numbers"][-1] = 61
        with self.assertRaisesRegex(ValueError, "invalid six-number draw"):
            engine.rank_candidates(
                history,
                cutoff_date="2020-08-05",
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

    def test_rejects_extremely_large_integer_constants_without_overflow(self):
        oversized = {
            "schema_version": 1,
            "score_expression": {
                "op": "add",
                "left": {
                    "op": "aggregate_history_numbers",
                    "aggregate": "sum",
                    "expression": {"op": "const", "value": 1},
                },
                "right": {"op": "const", "value": 10 ** 400},
            },
        }
        with self.assertRaisesRegex(ValueError, "within the safe bound"):
            engine.validate_execution_spec(oversized)

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

    def test_draw_age_uses_direction_relative_chronological_order(self):
        history = [
            {"date": "2024-01-01", "numbers": [1, 2, 3, 4, 5, 6]},
            {"date": "2024-01-02", "numbers": [55, 56, 57, 58, 59, 60]},
        ]
        spec = {
            "schema_version": 1,
            "score_expression": {
                "op": "aggregate_history_numbers",
                "aggregate": "count",
                "expression": {
                    "op": "and",
                    "left": {
                        "op": "eq",
                        "left": {"op": "var", "name": "value"},
                        "right": {"op": "var", "name": "candidate"},
                    },
                    "right": {
                        "op": "lt",
                        "left": {"op": "var", "name": "draw_age"},
                        "right": {"op": "const", "value": 1},
                    },
                },
            },
        }
        from general_hypothesis_execution import _rank_checked_history, _validate_rows
        checked = _validate_rows(
            history,
            pool_size=60,
            field="directional_test_history",
            maximum=engine.MAX_HISTORY_DRAWS,
        )
        forward = _rank_checked_history(
            checked, number_pool_size=60, spec=spec, direction="forward"
        )
        reverse = _rank_checked_history(
            checked, number_pool_size=60, spec=spec, direction="reverse"
        )
        self.assertEqual([item["number"] for item in forward[:6]], [55, 56, 57, 58, 59, 60])
        self.assertEqual([item["number"] for item in reverse[:6]], [1, 2, 3, 4, 5, 6])

    def test_retrospective_ranker_requires_only_strictly_later_history(self):
        later = synthetic_rows(45, start="2021-01-02", pool_size=60)
        ranking = engine.rank_candidates_retrospective(
            later[-40:],
            target_date="2021-01-01",
            number_pool_size=60,
            execution_spec=count_candidate_matches_spec(),
        )
        self.assertEqual(len(ranking), 60)
        self.assertEqual(sorted(row["number"] for row in ranking), list(range(1, 61)))
        with self.assertRaisesRegex(ValueError, "target or an earlier draw"):
            engine.rank_candidates_retrospective(
                later,
                target_date="2021-01-02",
                number_pool_size=60,
                execution_spec=count_candidate_matches_spec(),
            )
        with self.assertRaises(ValueError):
            engine.rank_candidates_retrospective(
                later[:39],
                target_date="2021-01-01",
                number_pool_size=60,
                execution_spec=count_candidate_matches_spec(),
            )

    def test_reverse_stress_uses_nearest_later_draw_as_draw_age_zero(self):
        rows = []
        for index in range(42):
            if index in {1, 2}:
                numbers = [1, 2, 3, 4, 5, 6]
            elif index == 0:
                numbers = [7, 8, 9, 10, 11, 12]
            else:
                numbers = [55, 56, 57, 58, 59, 60]
            rows.append({
                "date": (date(2024, 1, 1) + timedelta(days=index)).isoformat(),
                "numbers": numbers,
            })
        spec = {
            "schema_version": 1,
            "score_expression": {
                "op": "aggregate_history_numbers",
                "aggregate": "count",
                "expression": {
                    "op": "and",
                    "left": {
                        "op": "eq",
                        "left": {"op": "var", "name": "value"},
                        "right": {"op": "var", "name": "candidate"},
                    },
                    "right": {
                        "op": "lt",
                        "left": {"op": "var", "name": "draw_age"},
                        "right": {"op": "const", "value": 1},
                    },
                },
            },
        }
        report = engine.evaluate_reverse_retrospective_stress(
            rows,
            cutoff_date="2024-03-01",
            number_pool_size=60,
            execution_spec=spec,
            test_draw_count=1,
            min_train_draws=40,
        )
        self.assertEqual(report["evaluated_draw_count"], 1)
        self.assertEqual(report["mean_hits_out_of_6"], 6.0)
        self.assertEqual(len(report["per_draw"]), 1)
        self.assertEqual(report["per_draw"][0]["hit_column_positions_1_to_6"], [1, 2, 3, 4, 5, 6])
        self.assertFalse(report["per_draw"][0]["target_numbers_reported"])
        self.assertFalse(report["per_draw"][0]["predicted_number_values_reported"])
        self.assertFalse(report["predictive_evidence"])

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

    def test_reverse_stress_batches_are_contiguous_without_overlap(self):
        history = synthetic_rows(45, start="2024-01-01", pool_size=60)
        first = engine.evaluate_reverse_retrospective_stress(
            history,
            cutoff_date="2024-02-15",
            number_pool_size=60,
            execution_spec=count_candidate_matches_spec(),
            test_draw_count=5,
            min_train_draws=20,
        )
        second = engine.evaluate_reverse_retrospective_stress(
            history,
            cutoff_date="2024-02-15",
            number_pool_size=60,
            execution_spec=count_candidate_matches_spec(),
            test_draw_count=5,
            min_train_draws=20,
            skip_recent_test_draws=5,
        )
        self.assertEqual(first["evaluated_draw_count"], 5)
        self.assertEqual(second["evaluated_draw_count"], 5)
        self.assertEqual(first["first_test_date"], history[24]["date"])
        self.assertEqual(first["last_test_date"], history[20]["date"])
        self.assertEqual(second["first_test_date"], history[19]["date"])
        self.assertEqual(second["last_test_date"], history[15]["date"])
        self.assertLess(second["first_test_date"], first["last_test_date"])
        self.assertEqual(sum(first["hit_distribution"].values()), 5)
        self.assertEqual(sum(second["hit_distribution"].values()), 5)
        with self.assertRaises(ValueError):
            engine.evaluate_reverse_retrospective_stress(
                history,
                cutoff_date="2024-02-15",
                number_pool_size=60,
                execution_spec=count_candidate_matches_spec(),
                test_draw_count=5,
                min_train_draws=20,
                skip_recent_test_draws=21,
            )

    def test_holdout_reports_regime_specific_random_reference(self):
        discovery = synthetic_rows(20, start="2020-08-02", pool_size=60)
        validation = synthetic_rows(3, start="2020-08-22", pool_size=60)
        report = engine.evaluate_holdout(
            discovery,
            validation,
            cutoff_date="2020-08-25",
            number_pool_size=54,
            execution_spec=count_candidate_matches_spec(),
            min_train_draws=10,
        )
        self.assertEqual(report["uniform_random_expected_hits_out_of_6"], 0.666667)

    def test_synthetic_contract_runner_checks_both_regimes(self):
        result = engine.run_synthetic_contract_tests(count_candidate_matches_spec())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["number_pools_checked"], [60])
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
