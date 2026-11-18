import unittest

import general_target_evaluation_ledger as schema


def _entry(
    claim_id="a" * 64,
    attempt_id="b" * 64,
    hit_count=3,
    completed_at="2026-11-18T12:00:00+00:00",
):
    return {
        "claim_id": claim_id,
        "attempt_id": attempt_id,
        "target_date": "2026-11-17",
        "archive_sha256": "c" * 64,
        "archive_record_count": 986,
        "forward_hypothesis_id": "HYP-001",
        "forward_spec_sha256": "d" * 64,
        "execution_spec_dsl_version": 2,
        "candidate_count": 6,
        "hit_count": hit_count,
        "uniform_random_expected_hits_out_of_6": 0.6,
        "evaluation_scope": "ONE_CURRENT_HIDDEN_TARGET",
        "completed_at_utc": completed_at,
    }


class PrivateTargetEvaluationLedgerTests(unittest.TestCase):
    def test_valid_number_free_result_and_initial_ledger(self):
        empty = schema.initial_ledger()
        self.assertEqual(schema.validate_ledger(empty), empty)
        result = _entry()
        self.assertEqual(schema.validate_entry(result), result)
        self.assertEqual(schema.validate_ledger({
            "schema_version": schema.SCHEMA_VERSION,
            "purpose": schema.PURPOSE,
            "entries": [result],
        })["entries"][0]["hit_count"], 3)

    def test_only_aggregate_hit_count_and_exact_metadata_schema_are_accepted(self):
        for value in (-1, 7, True, 3.0):
            with self.subTest(value=value), self.assertRaises(schema.TargetEvaluationLedgerError):
                schema.validate_entry(_entry(hit_count=value))
        raw = _entry()
        raw["target_numbers"] = [1, 2, 3, 4, 5, 6]
        with self.assertRaises(schema.TargetEvaluationLedgerError):
            schema.validate_entry(raw)

    def test_hashes_ids_and_forward_directional_provenance_are_validated(self):
        for field, value in (
            ("claim_id", "not-a-hash"),
            ("attempt_id", "not-a-hash"),
            ("archive_sha256", "not-a-hash"),
            ("forward_spec_sha256", "not-a-hash"),
            ("forward_hypothesis_id", "HYP-501"),
            ("execution_spec_dsl_version", 3),
            ("candidate_count", 5),
            ("evaluation_scope", "RESEARCH_MEMORY"),
        ):
            bad = _entry()
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(schema.TargetEvaluationLedgerError):
                schema.validate_entry(bad)

    def test_utc_and_chronological_append_rules_are_strict(self):
        bad = _entry(completed_at="2026-11-18T15:00:00+03:00")
        with self.assertRaises(schema.TargetEvaluationLedgerError):
            schema.validate_entry(bad)
        duplicate_attempt = {
            "schema_version": 1,
            "purpose": schema.PURPOSE,
            "entries": [_entry(), _entry(claim_id="e" * 64)],
        }
        with self.assertRaisesRegex(schema.TargetEvaluationLedgerError, "duplicate"):
            schema.validate_ledger(duplicate_attempt)
        out_of_order = {
            "schema_version": 1,
            "purpose": schema.PURPOSE,
            "entries": [
                _entry("a" * 64, "b" * 64, completed_at="2026-11-18T12:02:00+00:00"),
                _entry("e" * 64, "f" * 64, completed_at="2026-11-18T12:01:00+00:00"),
            ],
        }
        with self.assertRaisesRegex(schema.TargetEvaluationLedgerError, "chronological"):
            schema.validate_ledger(out_of_order)


if __name__ == "__main__":
    unittest.main()
