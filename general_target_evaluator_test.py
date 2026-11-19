import json
import tempfile
import unittest
from pathlib import Path

import general_autonomous_research as auto
import general_hypothesis_execution_v2 as execution_v2
import general_sealed_evaluation as sealed
from general_target_evaluator import TargetEvaluationError, evaluate_frozen_target
from general_sealed_evaluation_test import make_rows
import general_target_queue as target_queue


def write_archive(path, rows):
    path.write_text(
        json.dumps({"draws": rows}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def _v2_spec(direction):
    slot = "B" if direction == "forward" else "C"
    return {
        "schema_version": 2, "direction": direction,
        "score_expression": {
            "op": "bool_to_number", "arg": {
                "op": "contains_candidate", "state": {"op": "state_ref", "slot": slot},
            },
        },
    }


def _write_v2_documents(directory):
    proposals, specs = directory / "proposals.json", directory / "specs.json"
    forward, reverse = _v2_spec("forward"), _v2_spec("reverse")
    for spec in (forward, reverse):
        result = execution_v2.run_synthetic_contract_tests(spec)
        if result.get("status") != "PASS" or result.get("real_archive_used") is not False:
            raise AssertionError("v2 synthetic contract test did not pass")
    proposals.write_text(json.dumps({
        "schema_version": 2, "status": "VALID_PROPOSALS",
        "proposals": [
            {"hypothesis_id": "HYP-901", "discovery_direction": "forward"},
            {"hypothesis_id": "HYP-902", "discovery_direction": "reverse"},
        ],
    }), encoding="utf-8")
    specs.write_text(json.dumps({
        "schema_version": 1, "status": "VALIDATED_SPECS",
        "dsl_validated": True, "synthetic_tests_passed": True,
        "specifications": [
            {"hypothesis_id": "HYP-901", "execution_spec": forward, "dsl_validated": True, "synthetic_tests_passed": True},
            {"hypothesis_id": "HYP-902", "execution_spec": reverse, "dsl_validated": True, "synthetic_tests_passed": True},
        ],
    }), encoding="utf-8")
    return proposals, specs


class LocalTargetEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.rows = make_rows()
        self.archive_path = self.root / "archive.json"
        write_archive(self.archive_path, self.rows)
        self.ledger_path = self.root / "evaluation-ledger.json"
        self.snapshot_directory = self.root / "general_evaluation_snapshots"
        self.proposals, self.specs = _write_v2_documents(self.root)
        source_archive = sealed.load_archive(self.archive_path)
        snapshot_item = target_queue._make_item(
            self.rows, source_archive_sha256=source_archive["archive_sha256"],
            enqueued_at_utc="2026-11-18T12:00:00+00:00",
        )
        active_queue = {
            "schema_version": 2,
            "observed_through_date": self.rows[-1]["date"],
            "active": snapshot_item,
            "latest_snapshot": snapshot_item,
            "completed": [],
        }
        target_queue.materialize_front(active_queue, self.archive_path)
        self.claim = sealed.create_claim(
            self.archive_path, self.ledger_path, self.proposals, self.specs,
            snapshot_directory=self.snapshot_directory,
        )
        self.assertTrue(self.claim["claim_created"])
        sealed.evaluate_claim(
            self.claim["claim_id"], self.ledger_path, self.proposals, self.specs,
            output_path=self.root / "sealed-report.json",
        )

        self.state_path = self.root / "general_autonomous_research_state.json"
        state = auto.initial_state()
        state["target_queue"] = active_queue
        self.attempt_id = "e" * 64
        state["attempts"].append({
            "attempt_id": self.attempt_id,
            "target_date": self.claim["target_date"],
            "archive_sha256": snapshot_item["archive_sha256"],
            "archive_record_count": len(self.rows),
            "claimed_at_utc": "2026-11-18T12:00:00+00:00",
            "github_run_id": "10001",
        })
        auto.write_state(self.state_path, state)
        self.output_path = self.root / "target-evaluation-report.json"

    def tearDown(self):
        self.temp.cleanup()

    def test_hidden_target_scored_locally_without_persisting_aggregate_score(self):
        report = evaluate_frozen_target(
            self.claim["claim_id"], self.attempt_id, self.ledger_path,
            self.proposals, self.specs,
            scheduler_state_path=self.state_path,
            snapshot_directory=self.snapshot_directory,
            output_path=self.output_path,
        )
        self.assertEqual(report["status"], "LOCAL_TARGET_EVALUATION_COMPLETED")
        self.assertEqual(report["candidate_count"], 6)
        self.assertIn(report["hit_count"], range(7))
        self.assertFalse(report["target_numbers_exposed"])
        self.assertFalse(report["candidate_numbers_exposed"])
        self.assertFalse(report["target_values_sent_to_model"])
        self.assertFalse(report["target_score_sent_to_model"])
        self.assertFalse(report["target_score_saved_to_research_memory"])
        self.assertFalse(report["reverse_spec_used_for_prediction"])
        state = auto.read_state(self.state_path)
        self.assertEqual(len(state["target_evaluations"]), 1)
        self.assertNotIn("hit_count", state["target_evaluations"][0])
        stored = state["target_evaluations"][0]
        self.assertNotIn("numbers", stored)
        self.assertNotIn("candidate_numbers", stored)
        self.assertNotIn("target_numbers", stored)
        self.assertTrue(report["persistent_result_status"] == "RECORDED")

    def test_local_target_result_is_idempotent_for_exact_attempt_claim_pair(self):
        first = evaluate_frozen_target(
            self.claim["claim_id"], self.attempt_id, self.ledger_path,
            self.proposals, self.specs, scheduler_state_path=self.state_path,
            snapshot_directory=self.snapshot_directory,
        )
        state = auto.read_state(self.state_path)
        again = auto.record_target_evaluation(state, first)
        self.assertEqual(len(again["target_evaluations"]), 1)

    def test_target_evaluation_requires_evaluated_claim(self):
        doc = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        doc["entries"][0]["state"] = "CLAIMED"
        self.ledger_path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaisesRegex(TargetEvaluationError, "not persisted as EVALUATED"):
            evaluate_frozen_target(
                self.claim["claim_id"], self.attempt_id, self.ledger_path,
                self.proposals, self.specs, scheduler_state_path=self.state_path,
                snapshot_directory=self.snapshot_directory,
            )

    def test_target_evaluation_rejects_attempt_or_archive_mismatch(self):
        state = auto.read_state(self.state_path)
        state["attempts"][0]["archive_sha256"] = "f" * 64
        auto.write_state(self.state_path, state)
        with self.assertRaisesRegex(TargetEvaluationError, "could not be persisted"):
            evaluate_frozen_target(
                self.claim["claim_id"], self.attempt_id, self.ledger_path,
                self.proposals, self.specs, scheduler_state_path=self.state_path,
                snapshot_directory=self.snapshot_directory,
            )

    def test_target_scoring_uses_observed_prior_draws_only_as_frozen_conditioning_context(self):
        docs = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        rows = sealed.load_archive(self.archive_path)["rows"]
        current_claim = docs["entries"][0]
        prior_dates = sorted([rows[-2]["date"], rows[-3]["date"]])

        # Model two already-consumed dates as a real preceding ledger claim.
        # The current claim must exclude those dates from its own holdout windows.
        current_claim["validation_dates"] = [
            day for day in current_claim["validation_dates"] if day not in prior_dates
        ]
        current_claim["reverse_validation_dates"] = [
            day for day in current_claim.get("reverse_validation_dates", [])
            if day not in prior_dates
        ]
        prior_claim = {
            "claim_id": "a" * 64,
            "validation_dates": prior_dates,
            "reverse_validation_dates": [],
            "state": "EVALUATED",
        }
        current_claim["previously_consumed_validation_dates"] = prior_dates
        docs["entries"] = [prior_claim, current_claim]
        self.ledger_path.write_text(json.dumps(docs), encoding="utf-8")
        report = evaluate_frozen_target(
            self.claim["claim_id"], self.attempt_id, self.ledger_path,
            self.proposals, self.specs, scheduler_state_path=self.state_path,
            snapshot_directory=self.snapshot_directory,
        )
        self.assertEqual(report["status"], "LOCAL_TARGET_EVALUATION_COMPLETED")
        self.assertTrue(report["prior_draws_used_only_as_conditioning_context"])
        self.assertFalse(report["prior_holdouts_reused_as_evaluation_targets"])
        self.assertFalse(report["prior_draws_reused_for_discovery_or_tuning"])
        self.assertFalse(report["target_score_sent_to_model"])

    def test_private_target_score_report_is_excluded_from_workflow_artifacts(self):
        workflow_path = (
            Path(__file__).resolve().parent
            / ".github"
            / "workflows"
            / "general-autonomous-research.yml"
        )
        workflow = workflow_path.read_text(encoding="utf-8")
        artifact_step = workflow.split(
            "- name: Upload number-safe autonomous cycle artifacts", 1
        )[1]
        artifact_paths = artifact_step.split("retention-days:", 1)[0]
        self.assertNotIn("open_research_target_evaluation_report.json", artifact_paths)

    def test_snapshot_path_cannot_escape_approved_directory(self):
        doc = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        doc["entries"][0]["archive_snapshot_path"] = "../outside/" + doc["entries"][0]["archive_sha256"] + ".json"
        self.ledger_path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaisesRegex(TargetEvaluationError, "escapes"):
            evaluate_frozen_target(
                self.claim["claim_id"], self.attempt_id, self.ledger_path,
                self.proposals, self.specs, scheduler_state_path=self.state_path,
                snapshot_directory=self.snapshot_directory,
            )


if __name__ == "__main__":
    unittest.main()
