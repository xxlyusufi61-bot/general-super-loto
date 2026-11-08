import json
import unittest
from datetime import date, timedelta
from unittest.mock import patch

import general_hypothesis_execution_v2 as engine


def draw(index, *, start="2024-01-01", numbers=None):
    day = (date.fromisoformat(start) + timedelta(days=index)).isoformat()
    if numbers is None:
        base = (index * 7) % 60 + 1
        numbers = sorted({((base + offset * 11 - 1) % 60) + 1 for offset in range(6)})
    return {"date": day, "numbers": list(numbers)}


def rows(count, *, start="2024-01-01"):
    return [draw(index, start=start) for index in range(count)]


def state(slot):
    return {"op": "state_ref", "slot": slot}


def score(expression, direction="forward"):
    return {
        "schema_version": 2,
        "direction": direction,
        "score_expression": expression,
    }


def bool_score(expression):
    return {"op": "bool_to_number", "arg": expression}


class SafeHypothesisExecutionV2Tests(unittest.TestCase):
    def test_forward_state_pair_binds_only_a_and_b(self):
        pair = [
            draw(0, numbers=[1, 2, 3, 4, 5, 6]),
            draw(1, numbers=[7, 8, 9, 10, 11, 12]),
        ]
        spec = score(bool_score({"op": "contains_candidate", "state": state("A")}))
        ranking = engine.rank_candidates_for_pair(
            pair, target_date="2024-01-03", number_pool_size=60, execution_spec=spec
        )
        self.assertEqual([item["number"] for item in ranking[:6]], [1, 2, 3, 4, 5, 6])
        self.assertEqual(len(ranking), 60)

    def test_reverse_state_pair_binds_only_b_and_c(self):
        pair = [
            draw(1, numbers=[7, 8, 9, 10, 11, 12]),
            draw(2, numbers=[13, 14, 15, 16, 17, 18]),
        ]
        spec = score(bool_score({"op": "contains_candidate", "state": state("B")}), "reverse")
        ranking = engine.rank_candidates_for_pair(
            pair, target_date="2024-01-01", number_pool_size=60, execution_spec=spec
        )
        self.assertEqual([item["number"] for item in ranking[:6]], [7, 8, 9, 10, 11, 12])

    def test_forbidden_target_slots_are_rejected_during_validation(self):
        with self.assertRaisesRegex(ValueError, "hidden or direction-forbidden"):
            engine.validate_execution_spec(
                score(bool_score({"op": "contains_candidate", "state": state("C")}))
            )
        with self.assertRaisesRegex(ValueError, "hidden or direction-forbidden"):
            engine.validate_execution_spec(
                score(bool_score({"op": "contains_candidate", "state": state("A")}), "reverse")
            )

    def test_wrong_direction_and_non_adjacent_pair_order_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "strictly later"):
            engine.rank_candidates_for_pair(
                [draw(0), draw(1)],
                target_date="2024-01-03",
                number_pool_size=60,
                execution_spec=score(bool_score({"op": "contains_candidate", "state": state("C")}), "reverse"),
            )
        with self.assertRaisesRegex(ValueError, "strictly earlier"):
            engine.rank_candidates_for_pair(
                [draw(0), draw(1)],
                target_date="2024-01-02",
                number_pool_size=60,
                execution_spec=score(bool_score({"op": "contains_candidate", "state": state("A")})),
            )
        with self.assertRaisesRegex(ValueError, "strictly later"):
            engine.rank_candidates_for_pair(
                [draw(1), draw(2)],
                target_date="2024-01-02",
                number_pool_size=60,
                execution_spec=score(bool_score({"op": "contains_candidate", "state": state("B")}), "reverse"),
            )

    def test_set_union_intersection_difference_and_symmetric_difference(self):
        pair = [
            draw(0, numbers=[1, 2, 3, 4, 5, 6]),
            draw(1, numbers=[4, 5, 6, 7, 8, 9]),
        ]
        expected = {
            "set_union": [1, 2, 3, 4, 5, 6, 7, 8, 9],
            "set_intersection": [4, 5, 6],
            "set_difference": [1, 2, 3],
            "set_symmetric_difference": [1, 2, 3, 7, 8, 9],
        }
        for operation, selected in expected.items():
            expression = bool_score({
                "op": "contains_candidate",
                "state": {"op": operation, "left": state("A"), "right": state("B")},
            })
            ranking = engine.rank_candidates_for_pair(
                pair,
                target_date="2024-01-03",
                number_pool_size=60,
                execution_spec=score(expression),
            )
            self.assertEqual([item["number"] for item in ranking if item["score"] == 1], selected)

    def test_cardinality_of_state_and_derived_set(self):
        pair = [
            draw(0, numbers=[1, 2, 3, 4, 5, 6]),
            draw(1, numbers=[4, 5, 6, 7, 8, 9]),
        ]
        expression = {
            "op": "cardinality",
            "state": {"op": "set_intersection", "left": state("A"), "right": state("B")},
        }
        ranking = engine.rank_candidates_for_pair(
            pair, target_date="2024-01-03", number_pool_size=60, execution_spec=score(expression)
        )
        self.assertEqual({item["score"] for item in ranking}, {3})

    def test_mod5_residue_set_has_explicit_zero_through_four_semantics(self):
        pair = [draw(0, numbers=[1, 2, 3, 4, 5, 6]), draw(1)]
        view = {
            "op": "mod5_project",
            "state": state("A"),
            "representation": "residue_set",
        }
        expression = bool_score({
            "op": "residue_contains",
            "view": view,
            "residue": 0,
        })
        ranking = engine.rank_candidates_for_pair(
            pair, target_date="2024-01-03", number_pool_size=60, execution_spec=score(expression)
        )
        self.assertEqual({item["score"] for item in ranking}, {1})
        self.assertEqual([number % 5 for number in pair[0]["numbers"]], [1, 2, 3, 4, 0, 1])

    def test_mod5_residue_counts_preserve_multiplicity_and_fixed_length(self):
        pair = [draw(0, numbers=[1, 2, 3, 4, 5, 6]), draw(1)]
        expression = {
            "op": "residue_frequency",
            "view": {
                "op": "mod5_project",
                "state": state("A"),
                "representation": "residue_counts",
            },
            "residue": 1,
        }
        ranking = engine.rank_candidates_for_pair(
            pair, target_date="2024-01-03", number_pool_size=60, execution_spec=score(expression)
        )
        self.assertEqual({item["score"] for item in ranking}, {2})
        checked = engine.validate_execution_spec(score(expression))
        self.assertEqual(checked["score_expression"]["view"]["representation"], "residue_counts")

    def test_mod5_sorted_residues_preserve_sorted_source_number_order(self):
        pair = [draw(0, numbers=[1, 2, 3, 4, 5, 6]), draw(1)]
        expression = {
            "op": "residue_at",
            "view": {
                "op": "mod5_project",
                "state": state("A"),
                "representation": "sorted_residues",
            },
            "index": 4,
        }
        ranking = engine.rank_candidates_for_pair(
            pair, target_date="2024-01-03", number_pool_size=60, execution_spec=score(expression)
        )
        self.assertEqual({item["score"] for item in ranking}, {0})

    def test_mod5_modes_are_not_inferred_or_silently_defaulted(self):
        bad = score({
            "op": "mod5_project",
            "state": state("A"),
            "representation": "whatever_is_best",
        })
        with self.assertRaisesRegex(ValueError, "explicitly selected"):
            engine.validate_execution_spec(bad)

    def test_residue_frequency_and_index_are_type_and_range_checked(self):
        bad_frequency = score({
            "op": "residue_frequency",
            "view": {
                "op": "mod5_project",
                "state": state("A"),
                "representation": "residue_set",
            },
            "residue": 1,
        })
        with self.assertRaisesRegex(ValueError, "requires the residue_counts"):
            engine.validate_execution_spec(bad_frequency)
        bad_index = score({
            "op": "residue_at",
            "view": {
                "op": "mod5_project",
                "state": state("A"),
                "representation": "sorted_residues",
            },
            "index": 6,
        })
        with self.assertRaisesRegex(ValueError, "index must be"):
            engine.validate_execution_spec(bad_index)

    def test_invalid_types_extra_keys_unknown_ops_and_bad_schema_rejected(self):
        cases = [
            {"schema_version": 2, "direction": "forward", "score_expression": state("A")},
            {**score({"op": "add", "left": state("A"), "right": {"op": "const", "value": 1}})},
            {**score({"op": "python", "source": "import os"})},
            {**score({"op": "const", "value": 1}), "extra": True},
            {"schema_version": 1, "direction": "forward", "score_expression": {"op": "const", "value": 1}},
        ]
        for item in cases:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    engine.validate_execution_spec(item)

    def test_ast_depth_and_node_budgets_are_enforced(self):
        deep = {"op": "var", "name": "candidate"}
        for _ in range(engine.MAX_AST_DEPTH + 1):
            deep = {"op": "abs", "arg": deep}
        with self.assertRaisesRegex(ValueError, "depth exceeds"):
            engine.validate_execution_spec(score(deep))
        level = [{"op": "const", "value": 1} for _ in range(33)]
        while len(level) > 1:
            next_level = []
            for index in range(0, len(level), 2):
                if index + 1 < len(level):
                    next_level.append({
                        "op": "add",
                        "left": level[index],
                        "right": level[index + 1],
                    })
                else:
                    next_level.append(level[index])
            level = next_level
        large = level[0]
        with self.assertRaisesRegex(ValueError, "too many nodes"):
            engine.validate_execution_spec(score(large))

    def test_numeric_faults_and_candidate_pool_are_bounded(self):
        bad_division = score({
            "op": "div",
            "left": {"op": "const", "value": 1},
            "right": {"op": "const", "value": 0},
        })
        with self.assertRaisesRegex(ValueError, "division by zero"):
            engine.rank_candidates_for_pair(
                [draw(0), draw(1)],
                target_date="2024-01-03",
                number_pool_size=60,
                execution_spec=bad_division,
            )
        with self.assertRaisesRegex(ValueError, "only the 6-of-60"):
            engine.rank_candidates_for_pair(
                [draw(0), draw(1)],
                target_date="2024-01-03",
                number_pool_size=49,
                execution_spec=score({"op": "const", "value": 1}),
            )

    def test_forward_walk_forward_uses_only_previous_two_rows_at_each_cutoff(self):
        discovery = rows(42, start="2024-01-01")
        validation = rows(3, start="2024-02-12")
        observed_pairs = []
        original = engine.rank_candidates_for_pair

        def spy(pair, **kwargs):
            observed_pairs.append([row["date"] for row in pair])
            return original(pair, **kwargs)

        with patch.object(engine, "rank_candidates_for_pair", side_effect=spy):
            report = engine.evaluate_holdout(
                discovery,
                validation,
                cutoff_date="2024-02-15",
                number_pool_size=60,
                execution_spec=score(bool_score({"op": "contains_candidate", "state": state("B")})),
                min_train_draws=40,
            )
        self.assertEqual(observed_pairs[0], [discovery[-2]["date"], discovery[-1]["date"]])
        self.assertEqual(observed_pairs[1], [discovery[-1]["date"], validation[0]["date"]])
        self.assertEqual(observed_pairs[2], [validation[0]["date"], validation[1]["date"]])
        self.assertEqual(report["validation_draw_count"], 3)
        self.assertFalse(report["current_target_numbers_exposed"])
        self.assertNotIn('"numbers":', json.dumps(report))

    def test_forward_evaluation_rejects_non_chronological_or_target_rows(self):
        discovery = rows(42, start="2024-01-01")
        validation = rows(2, start="2024-02-12")
        validation[1]["date"] = validation[0]["date"]
        with self.assertRaisesRegex(ValueError, "strictly chronological"):
            engine.evaluate_holdout(
                discovery, validation, cutoff_date="2024-02-15", number_pool_size=60,
                execution_spec=score({"op": "const", "value": 1}), min_train_draws=40,
            )

    def test_reverse_ranker_uses_nearest_later_rows_b_and_c_only(self):
        later = rows(42, start="2024-01-02")
        observed = []
        original = engine.rank_candidates_for_pair

        def spy(pair, **kwargs):
            observed.extend(row["date"] for row in pair)
            return original(pair, **kwargs)

        with patch.object(engine, "rank_candidates_for_pair", side_effect=spy):
            ranking = engine.rank_candidates_retrospective(
                later,
                target_date="2024-01-01",
                number_pool_size=60,
                execution_spec=score(bool_score({"op": "contains_candidate", "state": state("B")}), "reverse"),
                min_train_draws=40,
            )
        self.assertEqual(observed, [later[0]["date"], later[1]["date"]])
        self.assertEqual(len(ranking), 60)

    def test_reverse_stress_is_newest_to_oldest_and_never_predictive(self):
        history = rows(45, start="2024-01-01")
        report = engine.evaluate_reverse_retrospective_stress(
            history,
            cutoff_date="2024-02-15",
            number_pool_size=60,
            execution_spec=score(bool_score({"op": "contains_candidate", "state": state("C")}), "reverse"),
            test_draw_count=5,
            min_train_draws=40,
        )
        self.assertEqual(report["status"], "EVALUATED_RETROSPECTIVE_ONLY")
        self.assertFalse(report["predictive_evidence"])
        self.assertEqual(report["evaluation_order"], "newest_to_oldest")
        self.assertEqual(report["evaluated_draw_count"], 5)
        self.assertGreater(report["first_test_date"], report["last_test_date"])
        self.assertNotIn('"numbers":', json.dumps(report))
        self.assertTrue(all("target_numbers" not in row for row in report["per_draw"]))

    def test_reverse_stress_rejects_current_target_or_insufficient_later_rows(self):
        with self.assertRaisesRegex(ValueError, "isolated target"):
            engine.evaluate_reverse_retrospective_stress(
                rows(45), cutoff_date="2024-02-14", number_pool_size=60,
                execution_spec=score({"op": "const", "value": 1}, "reverse"),
                min_train_draws=40,
            )
        with self.assertRaisesRegex(ValueError, "eligible historical range"):
            engine.evaluate_reverse_retrospective_stress(
                rows(45), cutoff_date="2024-02-15", number_pool_size=60,
                execution_spec=score({"op": "const", "value": 1}, "reverse"),
                min_train_draws=40, skip_recent_test_draws=5,
            )

    def test_time_ordered_cross_validation_uses_contiguous_blocks(self):
        discovery = rows(42, start="2024-01-01")
        validation = rows(6, start="2024-02-12")
        report = engine.evaluate_time_ordered_cv(
            discovery, validation, cutoff_date="2024-02-20", number_pool_size=60,
            execution_spec=score({"op": "const", "value": 1}), folds=3, min_train_draws=40,
        )
        self.assertEqual(report["folds"], 3)
        self.assertEqual(report["validation_draw_count"], 6)
        self.assertLess(
            report["fold_reports"][0]["validation_end_date"],
            report["fold_reports"][1]["validation_start_date"],
        )
        self.assertFalse(report["automatic_candidate_selection"])

    def test_synthetic_contract_is_repeatable_and_does_not_mutate_inputs(self):
        spec = score(bool_score({"op": "contains_candidate", "state": state("A")}))
        first = engine.run_synthetic_contract_tests(spec)
        second = engine.run_synthetic_contract_tests(spec)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "PASS")
        self.assertFalse(first["real_archive_used"])
        self.assertFalse(first["target_values_used"])
        self.assertFalse(first["input_mutated"])

    def test_pre_scope_dates_and_invalid_draws_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "before the active research scope"):
            engine.rank_candidates_for_pair(
                [draw(0, start="2020-08-01"), draw(1, start="2020-08-01")],
                target_date="2020-08-04", number_pool_size=60,
                execution_spec=score({"op": "const", "value": 1}),
            )
        invalid = draw(0)
        invalid["numbers"] = [1, 2, 3, 4, 5, 61]
        with self.assertRaisesRegex(ValueError, "invalid six-number"):
            engine.rank_candidates_for_pair(
                [invalid, draw(1)], target_date="2024-01-03", number_pool_size=60,
                execution_spec=score({"op": "const", "value": 1}),
            )

    def test_input_pair_is_not_mutated(self):
        pair = [draw(0), draw(1)]
        before = json.dumps(pair, sort_keys=True)
        engine.rank_candidates_for_pair(
            pair, target_date="2024-01-03", number_pool_size=60,
            execution_spec=score(bool_score({"op": "contains_candidate", "state": state("A")})),
        )
        self.assertEqual(json.dumps(pair, sort_keys=True), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
