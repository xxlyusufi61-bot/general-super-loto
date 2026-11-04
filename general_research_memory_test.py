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
        "hypothesis_id": "HYP-001",
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
        self.assertNotIn("pydroid", json.dumps(value).lower())

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
            "predictive_evidence": True,
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
            sample_entry("forward", 3, "2026-11-05"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            forward = memory.direction_context("forward", "2026-11-04", memory_path=path)
            reverse = memory.direction_context("reverse", "2026-11-04", memory_path=path)
        self.assertEqual(len(forward), 1)
        self.assertEqual(forward[0]["direction"], "forward")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
