import json
import unittest
from datetime import date, timedelta
from pathlib import Path

from general_formula_proposals import (
    score_abc_transition,
    score_a_to_t,
    score_mod5_to_t,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "general_method_registry.json"
PROPOSAL_PATH = ROOT / "formula_archive" / "method_definition_proposals.json"


def synthetic_rows(count=25):
    states = (
        (1, 11, 21, 31, 41, 51),
        (2, 12, 22, 32, 42, 52),
        (3, 13, 23, 33, 43, 53),
        (4, 14, 24, 34, 44, 54),
    )
    start = date(2026, 1, 1)
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "numbers": list(states[index % len(states)]),
        }
        for index in range(count)
    ]


def add_hidden_target_and_future(rows):
    cutoff = date.fromisoformat(rows[-1]["date"]) + timedelta(days=1)
    return rows + [
        {"date": cutoff.isoformat(), "numbers": "TARGET_NUMBERS_MUST_NOT_BE_READ"},
        {"date": (cutoff + timedelta(days=1)).isoformat(), "numbers": None},
    ], cutoff.isoformat()


class MethodDefinitionProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        cls.proposals = json.loads(PROPOSAL_PATH.read_text(encoding="utf-8"))

    def test_all_proposals_are_unapproved_and_not_execution_ready(self):
        self.assertIs(self.proposals["binding_policy"]["canonical_registry_changed"], False)
        self.assertIs(self.proposals["binding_policy"]["execution_enabled"], False)
        methods = {item["method_id"]: item for item in self.proposals["proposals"]}
        self.assertEqual(set(methods), {"A->B->C", "A->T", "MOD5->T"})
        for name in methods:
            with self.subTest(method=name):
                self.assertEqual(methods[name]["status"], "PROPOSED_FOR_SYNTHETIC_TESTING_ONLY")
                self.assertIs(methods[name]["execution_ready"], False)
                reg = self.registry["methods"][name]
                self.assertEqual(reg["status"], "OPEN_DEFINITION")
                self.assertIsNone(reg["canonical_definition"])
                self.assertIs(reg["execution_ready"], False)

    def test_abc_transition_uses_following_c_not_b(self):
        rows = synthetic_rows()
        target_date = "2026-01-26"
        result = score_abc_transition(rows, target_date)
        expected_c = set(rows[1]["numbers"])  # the same earlier A->B transition was followed by this C
        self.assertEqual(result["status"], "scored")
        self.assertEqual(set(result["ranked_numbers"][:6]), expected_c)
        self.assertEqual(result["diagnostics"]["training_triples"], len(rows) - 2)
        self.assertLessEqual(result["diagnostics"]["neighbors_used"], 5)
        self.assertIs(result["diagnostics"]["target_draw_used"], False)

    def test_a_to_t_counts_only_observed_following_draw_pairs(self):
        rows = synthetic_rows()
        result = score_a_to_t(rows, "2026-01-26")
        expected_next = set(rows[1]["numbers"])
        self.assertEqual(result["status"], "scored")
        self.assertEqual(set(result["ranked_numbers"][:6]), expected_next)
        expected_score = 6 * (len(rows) // 4)
        for number in expected_next:
            self.assertGreater(result["scores"][number], 0)
        self.assertGreaterEqual(result["scores"][next(iter(expected_next))], expected_score)
        self.assertEqual(result["diagnostics"]["training_transitions"], len(rows) - 1)

    def test_mod5_profile_selects_matching_residue_patterns_and_next_draw(self):
        rows = synthetic_rows()
        result = score_mod5_to_t(rows, "2026-01-26")
        expected_next = set(rows[1]["numbers"])
        self.assertEqual(result["status"], "scored")
        self.assertEqual(set(result["ranked_numbers"][:6]), expected_next)
        self.assertEqual(result["diagnostics"]["neighbors_used"], 5)
        self.assertEqual(result["diagnostics"]["minimum_distance"], 0)

    def test_target_and_future_number_fields_do_not_affect_any_method(self):
        rows = synthetic_rows()
        target_date = "2026-01-26"
        full_rows, same_target_date = add_hidden_target_and_future(rows)
        self.assertEqual(target_date, same_target_date)
        for scorer in (score_abc_transition, score_a_to_t, score_mod5_to_t):
            with self.subTest(scorer=scorer.__name__):
                before = scorer(rows, target_date)
                after = scorer(full_rows, target_date)
                self.assertEqual(before, after)

        # If target/future numbers are never parsed, intentionally invalid fields remain harmless.
        mutated = [dict(row) for row in full_rows]
        mutated[-2]["numbers"] = [999, 999, 999]
        mutated[-1]["numbers"] = {"should": "never be inspected"}
        for scorer in (score_abc_transition, score_a_to_t, score_mod5_to_t):
            with self.subTest(scorer=scorer.__name__, hidden_mutation=True):
                self.assertEqual(
                    scorer(full_rows, target_date),
                    scorer(mutated, target_date),
                )

    def test_insufficient_history_returns_no_signal_not_fabricated_numbers(self):
        rows = synthetic_rows(count=4)
        target_date = "2026-01-03"  # only two draws are strictly pre-target
        for scorer in (score_abc_transition, score_a_to_t, score_mod5_to_t):
            with self.subTest(scorer=scorer.__name__):
                result = scorer(rows, target_date)
                self.assertEqual(result["status"], "no_signal")
                self.assertEqual(result["ranked_numbers"], [])
                self.assertTrue(result["reason"])

    def test_date_order_and_draw_integrity_are_enforced(self):
        rows = synthetic_rows(count=25)
        reversed_rows = list(reversed(rows))
        with self.assertRaises(ValueError):
            score_abc_transition(reversed_rows, "2026-01-26")

        malformed = [dict(row) for row in rows]
        malformed[0]["numbers"] = [1, 1, 3, 4, 5, 6]
        with self.assertRaises(ValueError):
            score_mod5_to_t(malformed, "2026-01-26")

        duplicate_date = [dict(row) for row in rows]
        duplicate_date[1]["date"] = duplicate_date[0]["date"]
        with self.assertRaises(ValueError):
            score_a_to_t(duplicate_date, "2026-01-26")

    def test_output_contains_only_valid_number_candidates_with_stable_order(self):
        rows = synthetic_rows()
        for scorer in (score_abc_transition, score_a_to_t, score_mod5_to_t):
            with self.subTest(scorer=scorer.__name__):
                result = scorer(rows, "2026-01-26")
                self.assertTrue(all(1 <= number <= 60 for number in result["ranked_numbers"]))
                values = [item["score"] for item in result["ranked_scores"]]
                self.assertEqual(values, sorted(values, reverse=True))
                self.assertEqual(len(result["ranked_numbers"]), len(set(result["ranked_numbers"])))


if __name__ == "__main__":
    unittest.main()
