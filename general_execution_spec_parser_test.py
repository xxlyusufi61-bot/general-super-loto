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
        self.assertTrue(result["synthetic_tests_passed"])
        self.assertFalse(result["execution_ready"])
        self.assertFalse(result["method_registry_modified"])
        self.assertEqual(len(result["specifications"]), 1)
        self.assertTrue(result["specifications"][0]["dsl_validated"])
        self.assertTrue(result["specifications"][0]["synthetic_tests_passed"])
        self.assertEqual(
            result["specifications"][0]["synthetic_test_summary"]["regimes_checked"],
            ["R54", "R60"],
        )
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

    def test_duplicate_json_keys_and_nonstandard_numbers_are_rejected(self):
        duplicate = (
            START_MARKER
            + '\n{"schema_version":1,"schema_version":1,"specifications":[]}\n'
            + END_MARKER
        )
        duplicate_result = parse_execution_spec_block(duplicate)
        self.assertEqual(duplicate_result["status"], "REJECTED")
        self.assertEqual(duplicate_result["specifications"], [])

        nonstandard = (
            START_MARKER
            + '\n{"schema_version":1,"specifications":[{"hypothesis_id":"HYP-001",'
            + '"execution_spec":{"schema_version":1,"score_expression":{"op":"aggregate_history_numbers",'
            + '"aggregate":"sum","expression":{"op":"const","value":NaN}}}]}\n'
            + END_MARKER
        )
        nonstandard_result = parse_execution_spec_block(nonstandard)
        self.assertEqual(nonstandard_result["status"], "REJECTED")
        self.assertEqual(nonstandard_result["specifications"], [])

    def test_oversized_execution_block_is_rejected_before_json_parsing(self):
        oversized = START_MARKER + (' ' * 16_385) + END_MARKER
        result = parse_execution_spec_block(oversized)
        self.assertEqual(result["status"], "REJECTED")
        self.assertIn("exceeds the size limit", result["rejection_reasons"][0])

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

    def test_dynamic_runtime_fault_fails_synthetic_preflight(self):
        item = specification()
        item["execution_spec"]["score_expression"] = {
            "op": "div",
            "left": {
                "op": "aggregate_history_numbers",
                "aggregate": "count",
                "expression": {
                    "op": "eq",
                    "left": {"op": "const", "value": 1},
                    "right": {"op": "const", "value": 2},
                },
            },
            "right": {
                "op": "sub",
                "left": {"op": "var", "name": "candidate"},
                "right": {"op": "const", "value": 2},
            },
        }
        result = parse_execution_spec_block(wrapped([item]))
        self.assertEqual(result["status"], "REJECTED")
        self.assertFalse(result["synthetic_tests_passed"])
        self.assertEqual(result["specifications"], [])

    def test_more_than_five_specs_is_rejected(self):
        result = parse_execution_spec_block(
            wrapped([specification(f"HYP-{index:03d}") for index in range(1, 7)])
        )
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["specifications"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
