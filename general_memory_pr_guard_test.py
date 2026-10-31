import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import general_core as core
from general_memory_pr_guard import (
    MemoryProposalError,
    main as guard_main,
    validate_memory_delta,
)


RULES = [
    "Target draw must remain hidden during discovery and training.",
    "Evaluation must be chronological and leakage-free.",
]


def baseline_memory():
    return {
        "schema_version": 1,
        "project": "GENERAL Super Loto",
        "created_at": "2026-10-01T00:00:00+00:00",
        "binding_rules": list(RULES),
        "entries": [
            {
                "id": 1,
                "timestamp": "2026-10-02T00:00:00+00:00",
                "category": "project_note",
                "title": "Initial note",
                "content": "Preserve chronological evaluation.",
                "source": "GENERAL",
                "status": "verified",
            }
        ],
    }


def report_entry(entry_id=2, agent="research", content="Hypothesis is unverified."):
    return {
        "id": entry_id,
        "timestamp": "2026-10-29T00:00:00+00:00",
        "category": "agent_report",
        "title": agent + " report",
        "content": content,
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "source": "GENERAL/" + agent,
        "status": "unverified",
        "research_epoch": "fresh-start-2026-10-29",
    }


class MemoryDeltaGuardTests(unittest.TestCase):
    def candidate_with(self, *entries):
        value = copy.deepcopy(baseline_memory())
        value["entries"].extend(entries)
        value["updated_at"] = "2026-10-29T00:01:00+00:00"
        return value

    def test_accepts_one_unverified_report_without_changing_binding_rules(self):
        candidate = self.candidate_with(report_entry())
        result = validate_memory_delta(baseline_memory(), candidate)
        self.assertTrue(result["valid"])
        self.assertEqual(result["new_entries_count"], 1)
        self.assertTrue(result["has_new_entries"])
        self.assertFalse(result["binding_rules_changed"])
        self.assertFalse(result["auto_merge"])

    def test_accepts_at_most_three_coordinator_reports(self):
        candidate = self.candidate_with(
            report_entry(2, "validation", "Audit summary."),
            report_entry(3, "research", "Method hypothesis."),
            report_entry(4, "crossover", "Comparison summary."),
        )
        self.assertEqual(validate_memory_delta(baseline_memory(), candidate)["new_entries_count"], 3)

    def test_rejects_binding_rule_change(self):
        candidate = self.candidate_with(report_entry())
        candidate["binding_rules"].append("Unapproved rule")
        with self.assertRaisesRegex(MemoryProposalError, "binding_rules"):
            validate_memory_delta(baseline_memory(), candidate)

    def test_rejects_modified_existing_entry(self):
        base = baseline_memory()
        candidate = self.candidate_with(report_entry())
        candidate["entries"][0]["content"] = "Changed original entry."
        with self.assertRaisesRegex(MemoryProposalError, "append-only"):
            validate_memory_delta(base, candidate)

    def test_rejects_report_marked_verified(self):
        entry = report_entry()
        entry["status"] = "verified"
        candidate = self.candidate_with(entry)
        with self.assertRaisesRegex(MemoryProposalError, "remain unverified"):
            validate_memory_delta(baseline_memory(), candidate)

    def test_rejects_report_without_fresh_start_epoch(self):
        entry = report_entry()
        entry["research_epoch"] = "legacy"
        candidate = self.candidate_with(entry)
        with self.assertRaisesRegex(MemoryProposalError, "fresh-start research epoch"):
            validate_memory_delta(baseline_memory(), candidate)

    def test_rejects_bad_content_hash(self):
        entry = report_entry()
        entry["content_hash"] = "0" * 64
        candidate = self.candidate_with(entry)
        with self.assertRaisesRegex(MemoryProposalError, "content hash"):
            validate_memory_delta(baseline_memory(), candidate)

    def test_rejects_more_than_three_new_reports(self):
        candidate = self.candidate_with(
            report_entry(2, "validation", "One."),
            report_entry(3, "research", "Two."),
            report_entry(4, "crossover", "Three."),
            report_entry(5, "debug", "Four."),
        )
        with self.assertRaisesRegex(MemoryProposalError, "no more than three"):
            validate_memory_delta(baseline_memory(), candidate)

    def test_rejects_unapproved_agent_source(self):
        entry = report_entry()
        entry["source"] = "GENERAL/archive"
        entry["title"] = "archive report"
        candidate = self.candidate_with(entry)
        with self.assertRaisesRegex(MemoryProposalError, "allowed coordinator agent"):
            validate_memory_delta(baseline_memory(), candidate)

    def test_rejects_invalid_timestamp_timezone(self):
        entry = report_entry()
        entry["timestamp"] = "2026-10-29T00:00:00"
        candidate = self.candidate_with(entry)
        with self.assertRaisesRegex(MemoryProposalError, "timezone"):
            validate_memory_delta(baseline_memory(), candidate)

    def test_no_new_reports_is_valid_but_does_not_request_a_pr(self):
        candidate = copy.deepcopy(baseline_memory())
        candidate["updated_at"] = "2026-10-29T00:01:00+00:00"
        result = validate_memory_delta(baseline_memory(), candidate)
        self.assertTrue(result["valid"])
        self.assertFalse(result["has_new_entries"])
        self.assertEqual(result["new_entries_count"], 0)

    def test_rejects_duplicate_or_out_of_order_entry_ids(self):
        candidate = self.candidate_with(report_entry(2))
        candidate["entries"][1]["id"] = 1
        with self.assertRaisesRegex(MemoryProposalError, "unique and increasing"):
            validate_memory_delta(baseline_memory(), candidate)




class MemoryProposalExportTests(unittest.TestCase):
    def _candidate_with(self, *entries):
        value = copy.deepcopy(baseline_memory())
        value["entries"].extend(entries)
        value["updated_at"] = "2026-10-29T00:01:00+00:00"
        return value

    def _run_cli(self, baseline_path, candidate_path, proposal_path):
        argv = [
            "general_memory_pr_guard.py",
            "--baseline", str(baseline_path),
            "--candidate", str(candidate_path),
            "--proposal-output", str(proposal_path),
        ]
        output = io.StringIO()
        with patch("sys.argv", argv), redirect_stdout(output):
            return guard_main(), json.loads(output.getvalue())

    def _write_inputs(self, root, candidate):
        baseline = baseline_memory()
        baseline_path = root / "baseline.json"
        candidate_path = root / "candidate.json"
        baseline_path.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        candidate_path.write_text(
            json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return baseline, baseline_path, candidate_path

    def test_cli_exports_only_new_entries_and_requires_human_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = self._candidate_with(report_entry())
            baseline, baseline_path, candidate_path = self._write_inputs(root, candidate)
            proposal_path = root / "review" / "memory-delta.json"

            code, summary = self._run_cli(baseline_path, candidate_path, proposal_path)

            self.assertEqual(code, 0)
            self.assertTrue(summary["valid"])
            self.assertTrue(summary["proposal_written"])
            self.assertTrue(summary["human_review_required"])
            proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
            self.assertEqual(proposal["new_entries_count"], 1)
            self.assertEqual(len(proposal["new_entries"]), 1)
            self.assertEqual(proposal["new_entries"][0]["id"], 2)
            self.assertEqual(proposal["new_entries"][0]["status"], "unverified")
            self.assertTrue(proposal["all_new_entries_unverified"])
            self.assertTrue(proposal["human_review_required"])
            self.assertFalse(proposal["auto_merge"])
            self.assertFalse(proposal["binding_rules_changed"])
            self.assertNotIn("entries", proposal)
            self.assertNotIn("binding_rules", proposal)
            self.assertNotIn("Preserve chronological evaluation.", proposal_path.read_text(encoding="utf-8"))
            self.assertEqual(len(proposal["baseline_sha256"]), 64)

    def test_invalid_delta_does_not_create_proposal_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = self._candidate_with(report_entry())
            candidate["binding_rules"].append("Unapproved binding rule.")
            _, baseline_path, candidate_path = self._write_inputs(root, candidate)
            proposal_path = root / "memory-delta.json"

            code, summary = self._run_cli(baseline_path, candidate_path, proposal_path)

            self.assertEqual(code, 1)
            self.assertFalse(summary["valid"])
            self.assertFalse(summary["proposal_written"])
            self.assertFalse(proposal_path.exists())

    def test_cli_refuses_to_overwrite_existing_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = self._candidate_with(report_entry())
            _, baseline_path, candidate_path = self._write_inputs(root, candidate)
            proposal_path = root / "memory-delta.json"
            proposal_path.write_text("preserve-me", encoding="utf-8")

            code, summary = self._run_cli(baseline_path, candidate_path, proposal_path)

            self.assertEqual(code, 1)
            self.assertFalse(summary["valid"])
            self.assertIn("refusing to overwrite", summary["reason"])
            self.assertEqual(proposal_path.read_text(encoding="utf-8"), "preserve-me")

    def test_no_new_entries_does_not_create_empty_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidate = copy.deepcopy(baseline_memory())
            candidate["updated_at"] = "2026-10-29T00:01:00+00:00"
            _, baseline_path, candidate_path = self._write_inputs(root, candidate)
            proposal_path = root / "memory-delta.json"

            code, summary = self._run_cli(baseline_path, candidate_path, proposal_path)

            self.assertEqual(code, 0)
            self.assertTrue(summary["valid"])
            self.assertFalse(summary["has_new_entries"])
            self.assertFalse(summary["proposal_written"])
            self.assertFalse(summary["human_review_required"])
            self.assertFalse(proposal_path.exists())


class CoreMemoryWriterIntegrationTests(unittest.TestCase):
    def test_core_memory_write_passes_proposal_guard(self):
        """The real memory writer emits only a valid, unverified append proposal."""
        baseline = {
            "schema_version": 1,
            "project": "GENERAL Super Loto",
            "created_at": "2026-10-01T00:00:00+00:00",
            "binding_rules": list(core.RULES),
            "entries": [
                {
                    "id": 1,
                    "timestamp": "2026-10-02T00:00:00+00:00",
                    "category": "project_note",
                    "title": "Initial note",
                    "content": "Preserve chronological evaluation.",
                    "source": "GENERAL",
                    "status": "verified",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            memory_path = Path(tmp) / "general_memory.json"
            memory_path.write_text(
                json.dumps(baseline, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with patch.object(core, "MEMORY", memory_path):
                core.add_memory("research", "Mock hypothesis; not verified.")

            candidate = json.loads(memory_path.read_text(encoding="utf-8"))

        result = validate_memory_delta(baseline, candidate)
        self.assertTrue(result["valid"])
        self.assertEqual(result["new_entries_count"], 1)
        self.assertTrue(result["has_new_entries"])
        self.assertTrue(result["all_new_entries_unverified"])
        self.assertFalse(result["binding_rules_changed"])
        self.assertFalse(result["auto_merge"])
        self.assertEqual(candidate["entries"][-1]["source"], "GENERAL/research")
        self.assertEqual(candidate["entries"][-1]["status"], "unverified")


if __name__ == "__main__":
    unittest.main(verbosity=2)
