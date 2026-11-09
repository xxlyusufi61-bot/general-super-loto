import unittest

import general_execution_spec_dispatch as dispatch


def v1_spec():
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


def v2_spec():
    return {
        "schema_version": 2,
        "direction": "forward",
        "score_expression": {
            "op": "bool_to_number",
            "arg": {
                "op": "contains_candidate",
                "state": {"op": "state_ref", "slot": "A"},
            },
        },
    }


class ExecutionSpecDispatchTests(unittest.TestCase):
    def test_v1_uses_legacy_validator_and_synthetic_contract(self):
        spec = v1_spec()
        checked = dispatch.validate_execution_spec(spec)
        self.assertEqual(checked, spec)
        self.assertEqual(dispatch.execution_spec_version(spec), 1)
        self.assertIsNone(dispatch.execution_spec_direction(spec))
        self.assertEqual(dispatch.run_synthetic_contract_tests(spec)["status"], "PASS")

    def test_v2_uses_typed_directional_validator_and_synthetic_contract(self):
        spec = v2_spec()
        checked = dispatch.validate_execution_spec(spec)
        self.assertEqual(checked, spec)
        self.assertEqual(dispatch.execution_spec_version(spec), 2)
        self.assertEqual(dispatch.execution_spec_direction(spec), "forward")
        result = dispatch.run_synthetic_contract_tests(spec)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["execution_dsl_version"], 2)

    def test_unknown_or_non_integer_versions_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "integer schema_version"):
            dispatch.validate_execution_spec({"schema_version": True})
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            dispatch.validate_execution_spec({"schema_version": 3})

    def test_v1_semantics_are_not_implicitly_upgraded(self):
        spec = v1_spec()
        self.assertEqual(dispatch.validate_execution_spec(spec)["schema_version"], 1)
        self.assertNotIn("direction", dispatch.validate_execution_spec(spec))


if __name__ == "__main__":
    unittest.main(verbosity=2)
