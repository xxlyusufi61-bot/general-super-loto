import copy
import hashlib
import unittest

from general_memory_pr_guard import MemoryProposalError, validate_memory_delta


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
