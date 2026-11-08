import json
import tempfile
import unittest
from pathlib import Path

from general_sealed_evaluation import (
    SealedEvaluationError,
    _select_one_frozen_candidate,
)


def directional_spec(direction, slot):
    return {
        "schema_version": 2,
        "direction": direction,
        "score_expression": {
            "op": "bool_to_number",
            "arg": {
                "op": "contains_candidate",
                "state": {"op": "state_ref", "slot": slot},
            },
        },
    }


def write_json(path: Path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class SealedEvaluationDSLv2Tests(unittest.TestCase):
    def _write_documents(self, directory: Path, *, forward_direction="forward"):
        proposals_path = directory / "proposals.json"
        specs_path = directory / "specs.json"
        proposals = {
            "schema_version": 2,
            "status": "VALID_PROPOSALS",
            "proposals": [
                {"hypothesis_id": "HYP-001", "discovery_direction": "forward"},
                {"hypothesis_id": "HYP-002", "discovery_direction": "reverse"},
            ],
        }
        specs = {
            "status": "VALIDATED_SPECS",
            "dsl_validated": True,
            "synthetic_tests_passed": True,
            "specifications": [
                {
                    "hypothesis_id": "HYP-001",
                    "execution_spec": directional_spec(forward_direction, "A"),
                    "dsl_validated": True,
                    "synthetic_tests_passed": True,
                },
                {
                    "hypothesis_id": "HYP-002",
                    "execution_spec": directional_spec("reverse", "C"),
                    "dsl_validated": True,
                    "synthetic_tests_passed": True,
                },
            ],
        }
        write_json(proposals_path, proposals)
        write_json(specs_path, specs)
        return proposals_path, specs_path

    def test_sealed_freeze_requires_matching_directional_v2_specs(self):
        with tempfile.TemporaryDirectory() as temp:
            proposals, specs = self._write_documents(Path(temp))
            candidate = _select_one_frozen_candidate(proposals, specs)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["execution_spec_dsl_version"], 2)
        self.assertEqual(candidate["reverse_execution_spec_dsl_version"], 2)
        self.assertEqual(candidate["execution_spec"]["direction"], "forward")
        self.assertEqual(candidate["reverse_execution_spec"]["direction"], "reverse")
        self.assertEqual(candidate["execution_spec"]["score_expression"]["arg"]["state"]["slot"], "A")
        self.assertEqual(candidate["reverse_execution_spec"]["score_expression"]["arg"]["state"]["slot"], "C")
        self.assertTrue(candidate["spec_sha256"])
        self.assertTrue(candidate["reverse_spec_sha256"])

    def test_sealed_freeze_rejects_forward_proposal_with_reverse_spec(self):
        with tempfile.TemporaryDirectory() as temp:
            proposals, specs = self._write_documents(Path(temp), forward_direction="reverse")
            with self.assertRaisesRegex(SealedEvaluationError, "does not match"):
                _select_one_frozen_candidate(proposals, specs)

    def test_sealed_freeze_does_not_accept_target_slot_in_directional_spec(self):
        with tempfile.TemporaryDirectory() as temp:
            proposals, specs = self._write_documents(Path(temp))
            payload = json.loads(specs.read_text(encoding="utf-8"))
            payload["specifications"][0]["execution_spec"]["score_expression"]["arg"]["state"]["slot"] = "C"
            write_json(specs, payload)
            with self.assertRaisesRegex(ValueError, "hidden or direction-forbidden"):
                _select_one_frozen_candidate(proposals, specs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
