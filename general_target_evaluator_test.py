import json
import tempfile
import unittest
from pathlib import Path
from datetime import date, timedelta

import general_autonomous_research as auto
import general_hypothesis_execution_v2 as execution_v2
import general_research_memory as research_memory
import general_sealed_evaluation as sealed
from general_target_evaluator import (
    TargetEvaluationError,
    evaluate_frozen_target,
    recover_evaluated_claim_for_attempt,
    _write_github_output,
)
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


def _write_evaluated_memory_pair(path, claim, specs, target_date, archive_sha, created_at):
    memory = research_memory.empty_memory()
    for direction, spec, hypothesis_id in (
        ("forward", specs[0], "HYP-001"),
        ("reverse", specs[1], "HYP-501"),
    ):
        raw_proposal = {
            "hypothesis_id": hypothesis_id,
            "discovery_direction": direction,
            "proposed_family": "bounded transition hypothesis",
            "research_rationale": "A fixed formal definition can be independently replayed.",
            "known_risks": ["The single-target score must remain isolated."],
            "formal_execution_spec": spec,
        }
        proposal = research_memory._validate_proposal(raw_proposal)
        proposal = {key: proposal[key] for key in research_memory.PROPOSAL_KEYS}
        spec_hash = research_memory._sha(research_memory._canonical(spec))
        feedback = {
            "status": "EVALUATED_EXPLORATORY_FIXED_SPEC",
            "claim_id": claim["claim_id"],
            "candidate_id": hypothesis_id,
            "spec_sha256": spec_hash,
            "holdout": {},
            "cross_validation": {},
            "holdout_consumed": True,
            "retrospective_only": direction == "reverse",
            "raw_holdout_dates_stored": False,
            "per_draw_results_stored": False,
            "draw_number_values_stored": False,
            "interpretation": "Frozen historical feedback only; the sealed interval is consumed.",
        }
        entry_id = len(memory["entries"]) + 1
        entry = {
            "entry_id": entry_id,
            "cycle_id": research_memory._sha(f"recovery-{claim['claim_id']}-{direction}".encode()),
            "source": research_memory.SOURCE,
            "research_epoch": research_memory.RESEARCH_EPOCH,
            "direction": direction,
            "created_at_utc": created_at,
            "target_date": target_date,
            "archive_sha256": archive_sha,
            "runner_version": "0.9.0",
            "status": "EVALUATED",
            "research_report_sha256": "b" * 64,
            "research_report_content": None,
            "research_report_content_sha256": None,
            "research_report_content_status": "OMITTED_NUMERIC_CONTENT",
            "proposal_status": "VALID_PROPOSALS",
            "proposals": [proposal],
            "execution_spec_status": "VALIDATED_SPECS",
            "execution_spec": spec,
            "execution_spec_sha256": spec_hash,
            "learning_notes": {
                "observations": ["The formal specification is deterministic."],
                "failed_approaches": ["Unbounded selection was rejected."],
                "next_tests": ["Repeat the exact frozen evaluation locally."],
            },
            "evaluation_feedback": feedback,
            "failure": None,
            "discovery_window": {
                "start_date": (date.fromisoformat(target_date) - timedelta(days=80)).isoformat(),
                "end_date": (date.fromisoformat(target_date) - timedelta(days=1)).isoformat(),
                "draw_count": 80,
                "holdout_dates_copied": False,
                "archive_rows_copied": False,
            },
        }
        entry["content_sha256"] = research_memory._entry_hash(entry)
        memory["entries"].append(entry)
    research_memory._write_memory(memory, path)


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

    def test_interrupted_local_score_recovers_exact_evaluated_pair_without_provider(self):
        state = auto.read_state(self.state_path)
        first_attempt = {
            "attempt_id": "a" * 64,
            "target_date": self.claim["target_date"],
            "archive_sha256": state["target_queue"]["active"]["archive_sha256"],
            "archive_record_count": len(self.rows),
            "claimed_at_utc": "2026-11-18T11:00:00+00:00",
            "github_run_id": "10000",
        }
        second_attempt = {
            **first_attempt,
            "attempt_id": self.attempt_id,
            "claimed_at_utc": "2026-11-18T12:30:00+00:00",
            "github_run_id": "10001",
        }
        state["attempts"] = [first_attempt, second_attempt]
        state["events"] = [{
            "attempt_id": first_attempt["attempt_id"],
            "outcome": "FAILED",
            "recorded_at_utc": "2026-11-18T12:00:00+00:00",
            "reason_code": "SEALED_EVALUATION_INCOMPLETE",
        }]
        auto.write_state(self.state_path, state)

        spec_doc = json.loads(self.specs.read_text(encoding="utf-8"))
        specs = [item["execution_spec"] for item in spec_doc["specifications"]]
        memory_path = self.root / "research-memory.json"
        _write_evaluated_memory_pair(
            memory_path,
            self.claim,
            specs,
            self.claim["target_date"],
            self.claim["archive_sha256"],
            "2026-11-18T11:30:00+00:00",
        )
        claim_output = self.root / "recovered-claim.json"
        recovered = recover_evaluated_claim_for_attempt(
            self.attempt_id,
            scheduler_state_path=self.state_path,
            ledger_path=self.ledger_path,
            memory_path=memory_path,
            proposals_path=self.proposals,
            specs_path=self.specs,
            claim_output_path=claim_output,
        )
        self.assertTrue(recovered["recovery_required"])
        self.assertTrue(recovered["recovery_available"])
        self.assertEqual(recovered["claim_id"], self.claim["claim_id"])
        recovered_claim = json.loads(claim_output.read_text(encoding="utf-8"))
        self.assertTrue(recovered_claim["claim_reused"])
        self.assertEqual(recovered_claim["claim_id"], self.claim["claim_id"])

        # The recovered files feed the ordinary frozen evaluator. No model
        # client or provider call is needed to reconstruct the candidate pair.
        report = evaluate_frozen_target(
            recovered_claim["claim_id"],
            self.attempt_id,
            self.ledger_path,
            self.proposals,
            self.specs,
            scheduler_state_path=self.state_path,
            snapshot_directory=self.snapshot_directory,
        )
        self.assertEqual(report["status"], "LOCAL_TARGET_EVALUATION_COMPLETED")
        self.assertIn(report["hit_count"], range(7))
        persisted = auto.read_state(self.state_path)["target_evaluations"]
        self.assertEqual(len(persisted), 1)
        self.assertNotIn("hit_count", persisted[0])

    def test_local_score_recovery_fails_closed_when_exact_pair_is_missing(self):
        state = auto.read_state(self.state_path)
        first_attempt = {
            "attempt_id": "a" * 64,
            "target_date": self.claim["target_date"],
            "archive_sha256": state["target_queue"]["active"]["archive_sha256"],
            "archive_record_count": len(self.rows),
            "claimed_at_utc": "2026-11-18T11:00:00+00:00",
            "github_run_id": "10000",
        }
        second_attempt = {
            **first_attempt,
            "attempt_id": self.attempt_id,
            "claimed_at_utc": "2026-11-18T12:30:00+00:00",
            "github_run_id": "10001",
        }
        state["attempts"] = [first_attempt, second_attempt]
        state["events"] = [{
            "attempt_id": first_attempt["attempt_id"],
            "outcome": "FAILED",
            "recorded_at_utc": "2026-11-18T12:00:00+00:00",
            "reason_code": "SEALED_EVALUATION_INCOMPLETE",
        }]
        auto.write_state(self.state_path, state)
        empty_memory_path = self.root / "empty-memory.json"
        empty_memory_path.write_text(
            json.dumps(research_memory.empty_memory()), encoding="utf-8"
        )
        result = recover_evaluated_claim_for_attempt(
            self.attempt_id,
            scheduler_state_path=self.state_path,
            ledger_path=self.ledger_path,
            memory_path=empty_memory_path,
            proposals_path=self.proposals,
            specs_path=self.specs,
            claim_output_path=self.root / "missing-recovery-claim.json",
        )
        self.assertTrue(result["recovery_required"])
        self.assertFalse(result["recovery_available"])
        self.assertFalse((self.root / "missing-recovery-claim.json").exists())

    def test_github_output_recovery_values_are_written_as_separate_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output.txt"
            _write_github_output(str(output), {
                "recovery_required": True,
                "recovery_available": False,
                "claim_id": "",
            })
            self.assertEqual(
                output.read_text(encoding="utf-8").splitlines(),
                [
                    "recovery_required=true",
                    "recovery_available=false",
                    "claim_id=",
                ],
            )

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
