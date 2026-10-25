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
SCORERS = (score_abc_transition, score_a_to_t, score_mod5_to_t)


def synthetic_rows(count=25):
    """Create pre-target historical draws with a known repeating transition."""
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


def add_hidden_target_and_future(rows, include_future=True):
    cutoff = date.fromisoformat(rows[-1]["date"]) + timedelta(days=1)
    result = rows + [
        {"date": cutoff.isoformat(), "numbers": "TARGET_NUMBERS_MUST_NOT_BE_READ"},
    ]
    if include_future:
        result.append({
            "date": (cutoff + timedelta(days=1)).isoformat(),
            "numbers": {"should": "never be inspected"},
        })
    return result, cutoff.isoformat()


class ProtectedNumberRow(dict):
    """A mapping that raises if code tries to access a target/future draw's numbers."""
    def __init__(self, row, cutoff):
        super().__init__(row)
        self._cutoff = date.fromisoformat(cutoff)

    def _protect(self, key):
        if key == "numbers":
            day = date.fromisoformat(dict.get(self, "date"))
            if day >= self._cutoff:
                raise AssertionError("target/future numbers were accessed")

    def get(self, key, default=None):
        self._protect(key)
        return super().get(key, default)

    def __getitem__(self, key):
        self._protect(key)
        return super().__getitem__(key)


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
        history = synthetic_rows()
        archive, target_date = add_hidden_target_and_future(history)
        result = score_abc_transition(archive, target_date)
        expected_c = set(history[1]["numbers"])
        self.assertEqual(result["status"], "scored")
        self.assertEqual(set(result["ranked_numbers"][:6]), expected_c)
        self.assertEqual(result["diagnostics"]["training_triples"], len(history) - 2)
        self.assertLessEqual(result["diagnostics"]["neighbors_used"], 5)
        self.assertIs(result["diagnostics"]["target_draw_used"], False)

    def test_a_to_t_counts_only_observed_following_draw_pairs(self):
        history = synthetic_rows()
        archive, target_date = add_hidden_target_and_future(history)
        result = score_a_to_t(archive, target_date)
        expected_next = set(history[1]["numbers"])
        self.assertEqual(result["status"], "scored")
        self.assertEqual(set(result["ranked_numbers"][:6]), expected_next)
        expected_score = 6 * (len(history) // 4)
        for number in expected_next:
            self.assertGreater(result["scores"][number], 0)
        self.assertGreaterEqual(result["scores"][next(iter(expected_next))], expected_score)
        self.assertEqual(result["diagnostics"]["training_transitions"], len(history) - 1)

    def test_mod5_profile_selects_matching_residue_patterns_and_next_draw(self):
        history = synthetic_rows()
        archive, target_date = add_hidden_target_and_future(history)
        result = score_mod5_to_t(archive, target_date)
        expected_next = set(history[1]["numbers"])
        self.assertEqual(result["status"], "scored")
        self.assertEqual(set(result["ranked_numbers"][:6]), expected_next)
        self.assertEqual(result["diagnostics"]["neighbors_used"], 5)
        self.assertEqual(result["diagnostics"]["minimum_distance"], 0)

    def test_target_date_must_be_present_exactly_once(self):
        history = synthetic_rows()
        archive, target_date = add_hidden_target_and_future(history, include_future=False)
        missing_target = archive[:-1]
        duplicated_target = archive + [dict(archive[-1])]
        for scorer in SCORERS:
            with self.subTest(scorer=scorer.__name__, condition="missing"):
                with self.assertRaisesRegex(ValueError, "target date must appear exactly once"):
                    scorer(missing_target, target_date)
            with self.subTest(scorer=scorer.__name__, condition="duplicate"):
                with self.assertRaises(ValueError):
                    scorer(duplicated_target, target_date)

    def test_target_and_future_numbers_are_never_read_or_used(self):
        history = synthetic_rows()
        archive, target_date = add_hidden_target_and_future(history)
        mutated = [dict(row) for row in archive]
        mutated[-2]["numbers"] = [999, 999, 999]
        mutated[-1]["numbers"] = {"mutated": object()}
        guarded = [ProtectedNumberRow(row, target_date) for row in archive]

        for scorer in SCORERS:
            with self.subTest(scorer=scorer.__name__):
                expected = scorer(archive, target_date)
                self.assertEqual(expected, scorer(mutated, target_date))
                # The protected mappings throw immediately if either hidden number set is read.
                self.assertEqual(expected, scorer(guarded, target_date))

    def test_insufficient_history_returns_no_signal_not_fabricated_numbers(self):
        history = synthetic_rows(count=2)
        archive, target_date = add_hidden_target_and_future(history)
        for scorer in SCORERS:
            with self.subTest(scorer=scorer.__name__):
                result = scorer(archive, target_date)
                self.assertEqual(result["status"], "no_signal")
                self.assertEqual(result["ranked_numbers"], [])
                self.assertTrue(result["reason"])

    def test_date_order_and_draw_integrity_are_enforced(self):
        history = synthetic_rows(count=25)
        archive, target_date = add_hidden_target_and_future(history)
        with self.assertRaises(ValueError):
            score_abc_transition(list(reversed(archive)), target_date)

        malformed = [dict(row) for row in archive]
        malformed[0]["numbers"] = [1, 1, 3, 4, 5, 6]
        with self.assertRaises(ValueError):
            score_mod5_to_t(malformed, target_date)

        duplicate_date = [dict(row) for row in archive]
        duplicate_date[1]["date"] = duplicate_date[0]["date"]
        with self.assertRaises(ValueError):
            score_a_to_t(duplicate_date, target_date)

    def test_output_contains_only_valid_number_candidates_with_stable_order(self):
        history = synthetic_rows()
        archive, target_date = add_hidden_target_and_future(history)
        for scorer in SCORERS:
            with self.subTest(scorer=scorer.__name__):
                result = scorer(archive, target_date)
                self.assertTrue(all(1 <= number <= 60 for number in result["ranked_numbers"]))
                values = [item["score"] for item in result["ranked_scores"]]
                self.assertEqual(values, sorted(values, reverse=True))
                self.assertEqual(len(result["ranked_numbers"]), len(set(result["ranked_numbers"])))
                if result["status"] == "no_signal":
                    self.assertTrue(result["reason"])


if __name__ == "__main__":
    unittest.main()
