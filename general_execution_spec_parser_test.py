import json
import unittest

from general_execution_spec_parser import (
    END_MARKER,
    START_MARKER,
    parse_execution_spec_block,
)


def specification(hypothesis_id="HYP-001"):
    return {
        "hypothesis_id": hypothesis_id,
        "execution_spec": {
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
        },
    }


def wrapped(items):
    payload = {"schema_version": 1, "specifications": items}
    return START_MARKER + "\n" + json.dumps(payload) + "\n" + END_MARKER


class ExecutionSpecParserTests(unittest.TestCase):
    def test_valid_spec_is_dsl_validated_but_not_execution_approved(self):
        result = parse_execution_spec_block(
            wrapped([specification()]),
            allowed_hypothesis_ids={"HYP-001"},
        )
        self.assertEqual(result["status"], "VALIDATED_SPECS")
        self.assertTrue(result["dsl_validated"])
        self.assertFalse(result["execution_ready"])
        self.assertFalse(result["method_registry_modified"])
        self.assertEqual(len(result["specifications"]), 1)
        self.assertTrue(result["specifications"][0]["dsl_validated"])
        self.assertFalse(result["specifications"][0]["execution_ready"])
        self.assertFalse(result["specifications"][0]["registry_write_allowed"])

    def test_empty_specification_list_is_valid(self):
        result = parse_execution_spec_block(wrapped([]))
        self.assertEqual(result["status"], "VALID_EMPTY")
        self.assertEqual(result["specifications"], [])
        self.assertFalse(result["execution_ready"])

    def test_missing_or_malformed_block_fails_closed(self):
        self.assertEqual(
            parse_execution_spec_block("No structured block.")["status"],
            "MISSING_BLOCK",
        )
        malformed = START_MARKER + "\n{bad json}\n" + END_MARKER
        result = parse_execution_spec_block(malformed)
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["specifications"], [])

    def test_unknown_or_unmatched_hypothesis_id_is_rejected(self):
        result = parse_execution_spec_block(
            wrapped([specification("HYP-002")]),
            allowed_hypothesis_ids={"HYP-001"},
        )
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["specifications"], [])

    def test_duplicate_ids_reject_entire_block(self):
        result = parse_execution_spec_block(
            wrapped([specification("HYP-001"), specification("HYP-001")])
        )
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["specifications"], [])

    def test_source_code_and_unapproved_fields_are_rejected(self):
        item = specification()
        item["source_code"] = "import os"
        result = parse_execution_spec_block(wrapped([item]))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["specifications"], [])

        item = specification()
        item["execution_spec"]["score_expression"]["source"] = "exec('x')"
        result = parse_execution_spec_block(wrapped([item]))
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["specifications"], [])

    def test_more_than_five_specs_is_rejected(self):
        result = parse_execution_spec_block(
            wrapped([specification(f"HYP-{index:03d}") for index in range(1, 7)])
        )
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["specifications"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
