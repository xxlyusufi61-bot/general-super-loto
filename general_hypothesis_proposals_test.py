import json
import unittest

from general_hypothesis_proposals import (
    END_MARKER,
    START_MARKER,
    parse_hypothesis_proposals,
)


def candidate(**overrides):
    item = {
        "hypothesis_id": "HYP-001",
        "proposed_family": "unclassified historical relationship",
        "hypothesis": "A reproducible relationship may remain after controlling for chronology.",
        "exact_definition": "Define the state from strictly earlier rows, then derive a fixed candidate ranking without using target values.",
        "inputs": "Only draws with dates strictly earlier than the scoring date.",
        "output_rule": "Return a deterministic ranking of 60 numbers and score the top six only on later test draws.",
        "parameter_policy": "Freeze all parameters before the final chronological evaluation interval.",
        "selection_policy": "Select only within the development interval using nested time-ordered validation.",
        "falsification_criterion": "Reject if the advantage does not persist across forward folds or against matched uniform random 6-of-60 baselines.",
        "known_risks": ["Multiple testing", "Regime drift and unstable relationships"],
        "evaluation_protocols": [
            "forward_walk_forward",
            "reverse_retrospective_stress_test",
            "time_ordered_cross_validation",
            "uniform_random_6_of_60",
        ],
    }
    item.update(overrides)
    return item


def wrapped(proposals):
    return (
        "Report text.\n"
        + START_MARKER
        + "\n"
        + json.dumps({"schema_version": 1, "proposals": proposals})
        + "\n"
        + END_MARKER
        + "\nEnd text."
    )


class HypothesisProposalContractTests(unittest.TestCase):
    def test_accepts_valid_proposal_as_non_executable_only(self):
        result = parse_hypothesis_proposals(wrapped([candidate()]))
        self.assertEqual(result["status"], "VALID_PROPOSALS")
        self.assertEqual(len(result["proposals"]), 1)
        proposal = result["proposals"][0]
        self.assertEqual(proposal["status"], "PROPOSAL_ONLY")
        self.assertIs(proposal["execution_ready"], False)
        self.assertIs(proposal["registry_write_allowed"], False)
        self.assertIs(result["registry_modified"], False)

    def test_empty_proposal_list_is_a_valid_no_signal_result(self):
        result = parse_hypothesis_proposals(wrapped([]))
        self.assertEqual(result["status"], "VALID_EMPTY")
        self.assertEqual(result["proposals"], [])
        self.assertIs(result["execution_ready"], False)

    def test_missing_block_fails_closed(self):
        result = parse_hypothesis_proposals("A prose-only report with no structured block.")
        self.assertEqual(result["status"], "MISSING_BLOCK")
        self.assertEqual(result["proposals"], [])
        self.assertIs(result["execution_ready"], False)

    def test_invalid_json_rejected(self):
        result = parse_hypothesis_proposals(
            START_MARKER + "\n{broken json}\n" + END_MARKER
        )
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["proposals"], [])

    def test_extra_executable_or_prediction_fields_are_rejected(self):
        item = candidate()
        item["source_code"] = "import os"
        result = parse_hypothesis_proposals(wrapped([item]))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["proposals"], [])

        item = candidate()
        item["predicted_numbers"] = [1, 2, 3, 4, 5, 6]
        result = parse_hypothesis_proposals(wrapped([item]))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["proposals"], [])

    def test_duplicate_ids_reject_entire_block(self):
        first = candidate()
        second = candidate(hypothesis="A different hypothesis, with the same ID.")
        result = parse_hypothesis_proposals(wrapped([first, second]))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["proposals"], [])

    def test_missing_required_evaluation_protocol_rejects_entire_block(self):
        item = candidate(evaluation_protocols=["forward_walk_forward"])
        result = parse_hypothesis_proposals(wrapped([item]))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["proposals"], [])

    def test_more_than_five_proposals_is_rejected(self):
        items = [candidate(hypothesis_id=f"HYP-{i:03d}") for i in range(1, 7)]
        result = parse_hypothesis_proposals(wrapped(items))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["proposals"], [])

    def test_family_remains_free_text_and_is_not_a_registry_write(self):
        item = candidate(proposed_family="novel and not in the method registry")
        result = parse_hypothesis_proposals(wrapped([item]))
        self.assertEqual(result["status"], "VALID_PROPOSALS")
        self.assertEqual(result["proposals"][0]["proposed_family"], item["proposed_family"])
        self.assertIs(result["proposals"][0]["registry_write_allowed"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
