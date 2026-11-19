import copy
import unittest
from datetime import timezone
import general_target_evaluation_ledger as ledger


def entry(claim="a"*64, attempt="b"*64, at="2026-11-18T12:00:00+00:00"):
    return {
        "claim_id": claim, "attempt_id": attempt, "target_date": "2026-11-15",
        "archive_sha256": "c"*64, "archive_record_count": 1000,
        "forward_hypothesis_id": "HYP-001", "forward_spec_sha256": "d"*64,
        "execution_spec_dsl_version": 2, "candidate_count": 6,
        "uniform_random_expected_hits_out_of_6": 0.6,
        "evaluation_scope": "ONE_CURRENT_HIDDEN_TARGET", "completed_at_utc": at,
    }


class PrivateTargetEvaluationSchemaTests(unittest.TestCase):
    def test_valid_entry_contains_only_score_free_receipt_metadata(self):
        checked = ledger.validate_entry(entry())
        self.assertNotIn("hit_count", checked)
        self.assertNotIn("numbers", checked)

    def test_attempt_and_claim_ids_must_be_unique(self):
        value = {"schema_version": 1, "purpose": ledger.PURPOSE, "entries": [entry(), entry()]}
        with self.assertRaisesRegex(ledger.TargetEvaluationLedgerError, "duplicate"):
            ledger.validate_ledger(value)

    def test_bad_hits_hash_or_non_utc_timestamp_rejected(self):
        for bad in (entry(at="2026-11-18T12:00:00+03:00"),):
            with self.assertRaises(ledger.TargetEvaluationLedgerError):
                ledger.validate_entry(bad)
        bad = entry()
        bad["forward_spec_sha256"] = "invalid"
        with self.assertRaises(ledger.TargetEvaluationLedgerError):
            ledger.validate_entry(bad)

    def test_aggregate_score_is_rejected_from_shared_state_receipt(self):
        bad = entry()
        bad["hit_count"] = 4
        with self.assertRaisesRegex(ledger.TargetEvaluationLedgerError, "invalid schema"):
            ledger.validate_entry(bad)

    def test_missing_and_extra_fields_are_rejected(self):
        bad = entry()
        bad["target_numbers"] = [1, 2, 3, 4, 5, 6]
        with self.assertRaises(ledger.TargetEvaluationLedgerError):
            ledger.validate_entry(bad)


if __name__ == "__main__":
    unittest.main()
