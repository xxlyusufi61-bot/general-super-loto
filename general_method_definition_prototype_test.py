import copy
import unittest

from general_method_definition_prototype import (
    score_a_b_c,
    score_a_to_t,
    score_mod5_to_t,
)


def draw(day, numbers):
    return {"date": day, "numbers": sorted(numbers)}


A = [1, 2, 3, 4, 5, 6]
B = [7, 8, 9, 10, 11, 12]
C = [13, 14, 15, 16, 17, 18]


class MethodDefinitionSyntheticTests(unittest.TestCase):
    def test_a_to_t_hand_calculated_pair_conditionals(self):
        history = [
            draw("2026-01-01", A),
            draw("2026-01-03", B),
            draw("2026-01-05", A),
            draw("2026-01-07", C),
            draw("2026-01-09", A),
            draw("2026-01-11", B),
        ]
        before = copy.deepcopy(history)
        result = score_a_to_t(history)

        self.assertEqual(result["status"], "PROPOSAL_ONLY")
        self.assertIs(result["execution_ready"], False)
        self.assertEqual(result["method"], "A->T")
        self.assertEqual(result["support"]["current_antecedents_with_zero_exposure"], 0)
        ranked = result["ranking"]
        self.assertEqual([item["number"] for item in ranked[:6]], A)
        for item in ranked[:6]:
            self.assertAlmostEqual(item["score"], 2 / 3)
        for item in ranked[6:]:
            self.assertAlmostEqual(item["score"], 1 / 3)
        self.assertEqual(history, before)

    def test_a_b_c_hand_calculated_two_step_pair_conditionals(self):
        history = [
            draw("2026-02-01", A),
            draw("2026-02-03", B),
            draw("2026-02-05", C),
            draw("2026-02-07", A),
            draw("2026-02-09", B),
            draw("2026-02-11", C),
            draw("2026-02-13", A),
            draw("2026-02-15", B),
        ]
        result = score_a_b_c(history)

        self.assertEqual(result["method"], "A->B->C")
        self.assertEqual(result["support"]["current_context_count"], 36)
        self.assertEqual(result["support"]["current_contexts_with_history"], 36)
        self.assertEqual([item["number"] for item in result["ranking"][:6]], C)
        for item in result["ranking"][:6]:
            self.assertAlmostEqual(item["score"], 3 / 4)
        for item in result["ranking"][6:]:
            self.assertAlmostEqual(item["score"], 1 / 4)

    def test_mod5_zero_residue_counts_once_per_source_draw(self):
        multiples_of_five = [5, 10, 15, 20, 25, 30]
        target_one = [1, 2, 3, 4, 6, 7]
        target_two = [8, 9, 11, 12, 13, 14]
        history = [
            draw("2026-03-01", multiples_of_five),
            draw("2026-03-03", target_one),
            draw("2026-03-05", multiples_of_five),
            draw("2026-03-07", target_two),
            draw("2026-03-09", multiples_of_five),
            draw("2026-03-11", target_one),
        ]
        result = score_mod5_to_t(history)

        self.assertEqual(result["method"], "MOD5->T")
        self.assertEqual(result["support"]["active_residues"], [0])
        self.assertEqual(result["support"]["exposure_count_by_residue"]["0"], 3)
        self.assertEqual([item["number"] for item in result["ranking"][:6]], target_one)
        for item in result["ranking"][:6]:
            self.assertAlmostEqual(item["score"], 3 / 5)
        for item in result["ranking"][6:12]:
            self.assertAlmostEqual(item["score"], 2 / 5)
        for item in result["ranking"][12:]:
            self.assertAlmostEqual(item["score"], 1 / 5)

    def test_exact_ties_break_by_ascending_number(self):
        history = [
            draw("2026-04-01", A),
            draw("2026-04-03", B),
        ]
        result = score_a_to_t(history)
        self.assertEqual(
            [item["number"] for item in result["ranking"][:6]],
            [1, 2, 3, 4, 5, 6],
        )
        self.assertTrue(all(item["score"] == 0.5 for item in result["ranking"]))

    def test_rejects_mixed_r54_r60_regime(self):
        history = [
            draw("2020-08-01", A),
            draw("2020-08-02", B),
        ]
        with self.assertRaisesRegex(ValueError, "crosses the R54/R60 boundary"):
            score_a_to_t(history)

    def test_rejects_number_above_54_in_r54(self):
        history = [
            draw("2020-07-25", [1, 2, 3, 4, 5, 55]),
            draw("2020-07-30", B),
        ]
        with self.assertRaisesRegex(ValueError, "above 54"):
            score_a_to_t(history)

    def test_rejects_bad_order_duplicate_dates_and_malformed_draws(self):
        unordered = [
            draw("2026-05-03", A),
            draw("2026-05-01", B),
        ]
        with self.assertRaises(ValueError):
            score_a_to_t(unordered)

        duplicate = [
            draw("2026-05-01", A),
            draw("2026-05-01", B),
        ]
        with self.assertRaises(ValueError):
            score_a_to_t(duplicate)

        malformed = [
            draw("2026-05-01", [1, 2, 3, 4, 5, 6]),
            draw("2026-05-03", [1, 2, 3, 4, 5]),
        ]
        with self.assertRaises(ValueError):
            score_a_to_t(malformed)

    def test_minimum_history_is_enforced(self):
        one = [draw("2026-06-01", A)]
        two = [
            draw("2026-06-01", A),
            draw("2026-06-03", B),
        ]
        with self.assertRaisesRegex(ValueError, "at least two"):
            score_a_to_t(one)
        with self.assertRaisesRegex(ValueError, "at least two"):
            score_mod5_to_t(one)
        with self.assertRaisesRegex(ValueError, "at least three"):
            score_a_b_c(two)

    def test_outputs_remain_explicitly_proposal_only(self):
        history = [
            draw("2026-07-01", A),
            draw("2026-07-03", B),
            draw("2026-07-05", C),
        ]
        for result in (
            score_a_to_t(history),
            score_mod5_to_t(history),
            score_a_b_c(history),
        ):
            self.assertEqual(result["status"], "PROPOSAL_ONLY")
            self.assertIs(result["execution_ready"], False)
            self.assertIs(result["real_archive_evaluation_performed"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
