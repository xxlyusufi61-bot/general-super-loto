import copy
import json
import unittest

from general_hypothesis_proposals import (
    END_MARKER,
    REQUIRED_PROTOCOLS,
    START_MARKER,
    execution_spec_matches_proposal,
    parse_hypothesis_proposals,
)


def execution_spec():
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


def candidate(**overrides):
    item = {
        "hypothesis_id": "HYP-001",
        "discovery_direction": "forward",
        "proposed_family": "unclassified historical relationship",
        "research_rationale": "A repeatable historical association deserves a controlled chronological test.",
        "known_risks": ["Multiple testing", "Regime drift and unstable relationships"],
        "formal_execution_spec": execution_spec(),
    }
    item.update(overrides)
    return item


def wrapped(proposals, schema_version=3):
    return (
        "Report text.\n"
        + START_MARKER
        + "\n"
        + json.dumps({"schema_version": schema_version, "proposals": proposals})
        + "\n"
        + END_MARKER
        + "\nEnd text."
    )


class HypothesisProposalContractTests(unittest.TestCase):
    def test_accepts_one_valid_proposal_as_non_executable_only(self):
        result = parse_hypothesis_proposals(wrapped([candidate()]))
        self.assertEqual(result["status"], "VALID_PROPOSALS")
        self.assertEqual(len(result["proposals"]), 1)
        proposal = result["proposals"][0]
        self.assertEqual(proposal["evaluation_protocols"], REQUIRED_PROTOCOLS)
        self.assertEqual(proposal["formal_execution_spec"], execution_spec())
        self.assertIn("Formal DSL definition:", proposal["exact_definition"])
        self.assertIn("formal DSL expression", proposal["hypothesis"])
        self.assertNotIn("A repeatable historical association", proposal["exact_definition"])
        self.assertEqual(proposal["status"], "PROPOSAL_ONLY")
        self.assertIs(proposal["execution_ready"], False)
        self.assertIs(proposal["registry_write_allowed"], False)
        self.assertIs(result["registry_modified"], False)

    def test_empty_proposal_list_is_a_valid_no_signal_result(self):
        result = parse_hypothesis_proposals(wrapped([]))
        self.assertEqual(result["status"], "VALID_EMPTY")
        self.assertEqual(result["proposals"], [])
        self.assertIs(result["execution_ready"], False)

    def test_missing_block_and_old_schema_fail_closed(self):
        self.assertEqual(
            parse_hypothesis_proposals("A prose-only report with no structured block.")["status"],
            "MISSING_BLOCK",
        )
        old_schema = parse_hypothesis_proposals(wrapped([], schema_version=2))
        self.assertEqual(old_schema["status"], "REJECTED")
        self.assertEqual(old_schema["proposals"], [])

    def test_invalid_json_rejected(self):
        result = parse_hypothesis_proposals(
            START_MARKER + "\n{broken json}\n" + END_MARKER
        )
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["proposals"], [])

    def test_extra_executable_or_prediction_fields_are_rejected(self):
        item = candidate()
        item["source_code"] = "import os"
        self.assertEqual(parse_hypothesis_proposals(wrapped([item]))["status"], "REJECTED")

        item = candidate()
        item["predicted_numbers"] = [1, 2, 3, 4, 5, 6]
        self.assertEqual(parse_hypothesis_proposals(wrapped([item]))["status"], "REJECTED")

    def test_model_cannot_choose_or_omit_required_protocols(self):
        item = candidate()
        item["evaluation_protocols"] = ["forward_walk_forward"]
        result = parse_hypothesis_proposals(wrapped([item]))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["proposals"], [])

        item = candidate()
        item.pop("formal_execution_spec")
        result = parse_hypothesis_proposals(wrapped([item]))
        self.assertEqual(result["status"], "REJECTED")

    def test_more_than_one_proposal_rejects_the_entire_atomic_block(self):
        first = candidate()
        second = candidate(hypothesis_id="HYP-002", hypothesis="A different hypothesis.")
        result = parse_hypothesis_proposals(wrapped([first, second]))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["proposals"], [])

    def test_invalid_proposal_does_not_allow_partial_acceptance_of_same_block(self):
        first = candidate()
        second = candidate(hypothesis_id="HYP-002", proposed_family="")
        result = parse_hypothesis_proposals(wrapped([first, second]))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["proposals"], [])

    def test_unrepresentable_hypothesis_can_be_stored_but_is_not_executable(self):
        result = parse_hypothesis_proposals(
            wrapped([candidate(formal_execution_spec=None)])
        )
        self.assertEqual(result["status"], "VALID_PROPOSALS")
        self.assertIsNone(result["proposals"][0]["formal_execution_spec"])
        self.assertIn("no faithful representation", result["proposals"][0]["hypothesis"])
        self.assertIn("No candidate ranking", result["proposals"][0]["output_rule"])

    def test_invalid_formal_execution_definition_is_rejected(self):
        result = parse_hypothesis_proposals(
            wrapped([candidate(formal_execution_spec={"schema_version": 1, "source": "exec('x')"})])
        )
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["proposals"], [])

    def test_execution_spec_must_exactly_match_the_formal_definition(self):
        proposal = parse_hypothesis_proposals(wrapped([candidate()]))["proposals"][0]
        self.assertTrue(execution_spec_matches_proposal(proposal, execution_spec()))
        altered = copy.deepcopy(execution_spec())
        altered["score_expression"]["aggregate"] = "sum"
        self.assertFalse(execution_spec_matches_proposal(proposal, altered))
        self.assertFalse(execution_spec_matches_proposal(
            candidate(formal_execution_spec=None), execution_spec()
        ))

    def test_family_remains_free_text_and_is_not_a_registry_write(self):
        item = candidate(proposed_family="novel and not in the method registry")
        result = parse_hypothesis_proposals(wrapped([item]))
        self.assertEqual(result["status"], "VALID_PROPOSALS")
        self.assertEqual(result["proposals"][0]["proposed_family"], item["proposed_family"])
        self.assertIs(result["proposals"][0]["registry_write_allowed"], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
