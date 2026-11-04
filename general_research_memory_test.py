import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import general_research_memory as memory


def _hash_entry(entry):
    body = {key: value for key, value in entry.items() if key != "content_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def sample_entry(direction="forward", entry_id=1, target="2026-10-20", status="HYPOTHESES_CAPTURED"):
    proposal = {
        "hypothesis_id": "HYP-001" if direction == "forward" else "HYP-501",
        "discovery_direction": direction,
        "proposed_family": "bounded transition hypothesis",
        "hypothesis": "Earlier-state counts may rank later values.",
        "exact_definition": "Count previous occurrences before each chronological cutoff.",
        "inputs": "Strictly earlier R60 history only.",
        "output_rule": "Rank candidates by descending count with a fixed tie-break.",
        "parameter_policy": "No fitted thresholds.",
        "selection_policy": "Select only on the direction's discovery interval.",
        "falsification_criterion": "Reject if forward performance does not exceed the null baseline.",
        "known_risks": ["Multiple testing and nonstationarity."],
        "evaluation_protocols": [
            "forward_walk_forward", "reverse_retrospective_stress_test",
            "time_ordered_cross_validation", "uniform_random_6_of_N",
        ],
    }
    entry = {
        "entry_id": entry_id,
        "cycle_id": hashlib.sha256(f"cycle-{entry_id}-{direction}".encode()).hexdigest(),
        "source": memory.SOURCE,
        "research_epoch": memory.RESEARCH_EPOCH,
        "direction": direction,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_date": target,
        "archive_sha256": "a" * 64,
        "runner_version": "0.7.0",
        "status": status,
        "research_report_sha256": "b" * 64,
        "proposal_status": "VALID_PROPOSALS",
        "proposals": [proposal],
        "execution_spec_status": "VALID_EMPTY",
        "execution_spec": None,
        "execution_spec_sha256": None,
        "learning_notes": {"observations": ["The candidate is simple and reproducible."], "failed_approaches": [], "next_tests": ["Compare against a uniform random baseline."]},
        "evaluation_feedback": None,
        "failure": None,
        "discovery_window": {"start_date": "2026-09-01", "end_date": "2026-10-01", "draw_count": 20, "holdout_dates_copied": False, "archive_rows_copied": False},
    }
    entry["content_sha256"] = _hash_entry(entry)
    return entry


class ResearchMemoryValidationTests(unittest.TestCase):
    def test_empty_memory_is_fresh_and_has_no_legacy_import(self):
        value = memory.empty_memory()
        self.assertEqual(value["entries"], [])
        self.assertEqual(value["research_epoch"], memory.RESEARCH_EPOCH)
        self.assertNotIn("general_memory", json.dumps(value).lower())

    def test_valid_entry_passes_schema_and_hash(self):
        entry = sample_entry()
        self.assertEqual(memory._validate_entry(entry, 1), entry)

    def test_legacy_source_or_epoch_is_rejected(self):
        entry = sample_entry()
        entry["source"] = "GENERAL/legacy"
        entry["content_sha256"] = _hash_entry(entry)
        with self.assertRaisesRegex(memory.ResearchMemoryError, "GENERAL research engine"):
            memory._validate_entry(entry, 1)

    def test_wrong_direction_proposal_is_rejected(self):
        entry = sample_entry("forward")
        entry["proposals"][0]["discovery_direction"] = "reverse"
        entry["content_sha256"] = _hash_entry(entry)
        with self.assertRaisesRegex(memory.ResearchMemoryError, "another direction"):
            memory._validate_entry(entry, 1)

    def test_six_number_set_is_rejected_from_memory(self):
        entry = sample_entry()
        entry["learning_notes"]["observations"] = ["Example output: 3, 9, 15, 24, 36, 50"]
        entry["content_sha256"] = _hash_entry(entry)
        with self.assertRaisesRegex(memory.ResearchMemoryError, "six-number value set"):
            memory._validate_entry(entry, 1)

    def test_consumed_feedback_must_not_be_relabelled_as_independent(self):
        entry = sample_entry()
        entry["evaluation_feedback"] = {
            "holdout_consumed": False,
            "retrospective_only": False,
            "holdout": {},
            "cross_validation": {},
        }
        entry["content_sha256"] = _hash_entry(entry)
        with self.assertRaisesRegex(memory.ResearchMemoryError, "permanently consumed"):
            memory._validate_entry(entry, 1)

    def test_memory_rejects_tampered_content_hash(self):
        entry = sample_entry()
        entry["learning_notes"]["observations"] = ["Changed after hashing."]
        value = memory.empty_memory()
        value["entries"] = [entry]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(memory.ResearchMemoryError, "hash mismatch"):
                memory.load_memory(path)


class DirectionalMemoryContextTests(unittest.TestCase):
    def test_context_uses_only_same_direction_and_older_target_entries(self):
        value = memory.empty_memory()
        value["entries"] = [
            sample_entry("forward", 1, "2026-10-20"),
            sample_entry("reverse", 2, "2026-10-20"),
            sample_entry("forward", 3, "2026-11-04"),
            sample_entry("forward", 4, "2026-11-05"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            forward = memory.direction_context("forward", "2026-11-04", memory_path=path)
            reverse = memory.direction_context("reverse", "2026-11-04", memory_path=path)
        self.assertEqual(len(forward), 2)
        self.assertEqual(forward[0]["direction"], "forward")
        self.assertEqual(forward[0]["prior_target_date"], "2026-11-04")
        self.assertEqual(len(reverse), 1)
        self.assertEqual(reverse[0]["direction"], "reverse")
        self.assertNotIn("2026-11-05", json.dumps(forward))

    def test_direction_context_refuses_invalid_direction(self):
        with self.assertRaisesRegex(memory.ResearchMemoryError, "Direction"):
            memory.direction_context("both", "2026-11-04")

    def test_learning_notes_block_is_structured_and_number_safe(self):
        report = (
            memory.LESSONS_START
            + json.dumps({
                "schema_version": 1,
                "observations": ["A fixed tie-break improves reproducibility."],
                "failed_approaches": ["A moving threshold was not reproducible."],
                "next_tests": ["Compare the fixed rule with a null baseline."],
            })
            + memory.LESSONS_END
        )
        notes = memory._parse_learning_notes(report)
        self.assertEqual(len(notes["observations"]), 1)
        bad = report.replace("A fixed tie-break improves reproducibility.", "Prediction: 3, 9, 15, 24, 36, 50")
        with self.assertRaisesRegex(memory.ResearchMemoryError, "six-number value set"):
            memory._parse_learning_notes(bad)


def directional_report(direction):
    source_id = "HYP-001"
    proposal = {
        "hypothesis_id": source_id,
        "discovery_direction": direction,
        "proposed_family": "bounded transition hypothesis",
        "hypothesis": "Earlier-state counts may rank later values.",
        "exact_definition": "Count previous occurrences before each chronological cutoff.",
        "inputs": "Strictly earlier R60 history only.",
        "output_rule": "Rank candidates by descending count with a fixed tie-break.",
        "parameter_policy": "No fitted thresholds.",
        "selection_policy": "Select only on the direction's discovery interval.",
        "falsification_criterion": "Reject if the result does not exceed a uniform random baseline.",
        "known_risks": ["Multiple testing and nonstationarity."],
        "evaluation_protocols": [
            "forward_walk_forward", "reverse_retrospective_stress_test",
            "time_ordered_cross_validation", "uniform_random_6_of_N",
        ],
    }
    spec = {
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
    return (
        "<HYPOTHESIS_PROPOSALS_JSON>"
        + json.dumps({"schema_version": 2, "proposals": [proposal]})
        + "</HYPOTHESIS_PROPOSALS_JSON>"
        + "<HYPOTHESIS_EXECUTION_SPECS_JSON>"
        + json.dumps({"schema_version": 1, "specifications": [{"hypothesis_id": source_id, "execution_spec": spec}]})
        + "</HYPOTHESIS_EXECUTION_SPECS_JSON>"
        + memory.LESSONS_START
        + json.dumps({
            "schema_version": 1,
            "observations": ["The fixed definition is reproducible."],
            "failed_approaches": ["A moving threshold was not stable."],
            "next_tests": ["Compare the candidate against a null baseline."],
        })
        + memory.LESSONS_END
    )


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ResearchMemoryPersistenceTests(unittest.TestCase):
    def test_append_only_writer_refuses_to_modify_existing_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            first = sample_entry("forward", 1)
            payload = memory.empty_memory()
            payload["entries"] = [first]
            write_json(path, payload)
            appended = copy.deepcopy(payload)
            appended["entries"].append(sample_entry("reverse", 2))
            memory._write_memory(appended, path)
            tampered = copy.deepcopy(appended)
            tampered["entries"][0]["learning_notes"]["observations"] = ["Old record changed."]
            tampered["entries"][0]["content_sha256"] = _hash_entry(tampered["entries"][0])
            tampered["entries"].append(sample_entry("forward", 3, "2026-10-22"))
            with self.assertRaisesRegex(memory.ResearchMemoryError, "not append-only"):
                memory._write_memory(tampered, path)

    def test_record_cycle_persists_only_directional_hypotheses_and_evaluation_feedback(self):
        from general_sealed_evaluation import load_archive

        rows = [
            {"date": "2026-11-01", "numbers": [1, 7, 13, 22, 34, 48]},
            {"date": "2026-11-02", "numbers": [2, 8, 14, 23, 35, 49]},
            {"date": "2026-11-03", "numbers": [3, 9, 15, 24, 36, 50]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "archive.json"
            archive_bytes = json.dumps({"draws": rows}, ensure_ascii=False, indent=2).encode("utf-8")
            archive_path.write_bytes(archive_bytes)
            archive = load_archive(archive_path)
            target = archive["last_date"]
            archive_hash = archive["archive_sha256"]
            ledger_path = root / "ledger.json"
            claim_id = "d" * 64
            write_json(ledger_path, {
                "schema_version": 1,
                "purpose": "append-only test ledger",
                "entries": [{
                    "claim_id": claim_id,
                    "state": "EVALUATED",
                    "target_date": target,
                    "archive_sha256": archive_hash,
                    "validation_dates": ["2026-10-20", "2026-10-21"],
                    "reverse_validation_dates": ["2026-10-10", "2026-10-11"],
                }],
            })
            write_json(root / "run-status.json", {
                "schema_version": 1,
                "status": "RESEARCH_COMPLETE",
                "stage": "research_complete",
                "target_date": target,
                "archive_sha256": archive_hash,
                "failed_direction": None,
                "error_type": None,
                "target_numbers_stored": False,
                "archive_rows_stored": False,
            })
            (root / "forward.md").write_text(directional_report("forward"), encoding="utf-8")
            (root / "reverse.md").write_text(directional_report("reverse"), encoding="utf-8")
            write_json(root / "sealed-plan.json", {
                "regimes": {
                    "R60": {
                        "discovery_start_date": "2026-09-01",
                        "discovery_end_date": "2026-10-01",
                        "discovery_count": 20,
                    }
                }
            })
            write_json(root / "claim.json", {"claim_created": True, "claim_id": claim_id})

            spec = json.loads(
                directional_report("forward").split("<HYPOTHESIS_EXECUTION_SPECS_JSON>")[1]
                .split("</HYPOTHESIS_EXECUTION_SPECS_JSON>")[0]
            )["specifications"][0]["execution_spec"]
            spec_hash = hashlib.sha256(
                json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest()
            evaluation = {
                "status": "EVALUATED_EXPLORATORY_FIXED_SPEC",
                "claim_id": claim_id,
                "archive_sha256": archive_hash,
                "target_date": target,
                "current_isolated_target_included": False,
                "current_target_numbers_exposed": False,
                "target_or_future_rows_used": False,
                "per_draw_target_numbers_reported": False,
                "candidate_rankings_reported": False,
                "candidate_selection": {
                    "forward_hypothesis_id": "HYP-001",
                    "forward_spec_sha256": spec_hash,
                    "reverse_hypothesis_id": "HYP-501",
                    "reverse_spec_sha256": spec_hash,
                    "selection_used_validation_scores": False,
                },
                "forward_walk_forward": {
                    "mean_hits_out_of_6": 0.71,
                    "standard_error_of_mean_hits": 0.08,
                    "uniform_random_expected_hits_out_of_6": 0.6,
                    "validation_draw_count": 10,
                    "hit_distribution": {"0_hits": 1, "1_hit": 3, "2_hits": 4, "3_or_more_hits": 2},
                    "per_draw": [{"test_date": "2026-10-20", "hit_count": 1}],
                },
                "time_ordered_cross_validation": {
                    "folds": 5,
                    "validation_draw_count": 10,
                    "mean_hits_out_of_6": 0.70,
                    "uniform_random_expected_hits_out_of_6": 0.6,
                },
                "reverse_discovered_candidate_holdout": {
                    "test_draw_count": 10,
                    "mean_hits_out_of_6": 0.62,
                    "uniform_random_expected_hits_out_of_6": 0.6,
                    "retrospective_only": True,
                },
                "reverse_time_ordered_cross_validation": {
                    "folds": 5,
                    "validation_draw_count": 10,
                    "mean_hits_out_of_6": 0.61,
                    "uniform_random_expected_hits_out_of_6": 0.6,
                    "predictive_evidence": False,
                },
            }
            write_json(root / "evaluation.json", evaluation)

            memory_path = root / "general_research_memory.json"
            result = memory.record_cycle(
                memory_path=memory_path,
                archive_path=archive_path,
                ledger_path=ledger_path,
                run_status_path=root / "run-status.json",
                forward_report_path=root / "forward.md",
                reverse_report_path=root / "reverse.md",
                sealed_plan_path=root / "sealed-plan.json",
                claim_path=root / "claim.json",
                evaluation_path=root / "evaluation.json",
                run_id="123456789",
                run_attempt="1",
            )
            saved = memory.load_memory(memory_path)
            serialized = json.dumps(saved, ensure_ascii=False)
            forward_context = memory.direction_context("forward", "2026-11-04", memory_path=memory_path)
            reverse_context = memory.direction_context("reverse", "2026-11-04", memory_path=memory_path)

        self.assertEqual(result["entries_added"], 2)
        self.assertEqual([item["status"] for item in saved["entries"]], ["EVALUATED", "EVALUATED"])
        self.assertEqual(saved["entries"][0]["proposals"][0]["hypothesis_id"], "HYP-001")
        self.assertEqual(saved["entries"][1]["proposals"][0]["hypothesis_id"], "HYP-501")
        self.assertTrue(saved["entries"][0]["evaluation_feedback"]["holdout_consumed"])
        self.assertTrue(not saved["entries"][0]["evaluation_feedback"]["retrospective_only"])
        self.assertTrue(saved["entries"][1]["evaluation_feedback"]["holdout_consumed"])
        self.assertFalse(saved["entries"][1]["evaluation_feedback"]["retrospective_only"])
        self.assertNotIn("per_draw", serialized)
        self.assertNotIn("validation_dates", serialized)
        self.assertNotIn("reverse_validation_dates", serialized)
        self.assertNotIn("numbers", saved["entries"][0])
        self.assertEqual(len(forward_context), 1)
        self.assertEqual(len(reverse_context), 1)
        self.assertEqual(forward_context[0]["direction"], "forward")
        self.assertEqual(reverse_context[0]["direction"], "reverse")

        # A local evaluation report is not trusted while the append-only
        # ledger still says the claim is only CLAIMED.
        claimed_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        claimed_ledger["entries"][0]["state"] = "CLAIMED"
        write_json(ledger_path, claimed_ledger)
        claimed_memory_path = root / "claimed-memory.json"
        claimed_result = memory.record_cycle(
            memory_path=claimed_memory_path,
            archive_path=archive_path,
            ledger_path=ledger_path,
            run_status_path=root / "run-status.json",
            forward_report_path=root / "forward.md",
            reverse_report_path=root / "reverse.md",
            sealed_plan_path=root / "sealed-plan.json",
            claim_path=root / "claim.json",
            evaluation_path=root / "evaluation.json",
            run_id="123456790",
            run_attempt="1",
        )
        claimed_memory = memory.load_memory(claimed_memory_path)
        self.assertEqual(claimed_result["entries_added"], 2)
        self.assertTrue(all(item["status"] == "CLAIMED_EVALUATION_FAILED" for item in claimed_memory["entries"]))
        self.assertTrue(all(item["evaluation_feedback"] is None for item in claimed_memory["entries"]))
        self.assertTrue(all(item["failure"]["failure_metadata_only"] for item in claimed_memory["entries"]))

        with self.assertRaisesRegex(memory.ResearchMemoryError, "already exists"):
            memory.record_cycle(
                memory_path=memory_path,
                archive_path=archive_path,
                ledger_path=ledger_path,
                run_status_path=root / "run-status.json",
                forward_report_path=root / "forward.md",
                reverse_report_path=root / "reverse.md",
                sealed_plan_path=root / "sealed-plan.json",
                claim_path=root / "claim.json",
                evaluation_path=root / "evaluation.json",
                run_id="123456789",
                run_attempt="1",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
