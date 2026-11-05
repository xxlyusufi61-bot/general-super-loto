import copy
import json
import unittest

import general_automation_state as contract
import general_automation_state_integrity_audit as audit


STAMP = "2026-11-05T00:00:00+00:00"
HEAD = "a" * 40


def make_snapshot(*, authorization_enabled=True):
    documents = {
        "archive": {"schema_version": 1, "draws": [{"numbers": [1, 2, 3, 4, 5, 6]}]},
        "evaluation_ledger": {"schema_version": 1, "purpose": "fixture", "entries": []},
        "autonomous_research_state": {
            "schema_version": 1,
            "project": "GENERAL Super Loto",
            "authorization": {"enabled": authorization_enabled},
        },
        "research_memory": {"schema_version": 2, "project": "GENERAL Super Loto", "entries": []},
    }
    manifest = contract.initial_manifest()
    manifest.update({
        "status": "ACTIVE",
        "generation": 1,
        "updated_at_utc": STAMP,
    })
    for key in audit.EXPECTED_OBJECTS:
        content = json.dumps(documents[key], ensure_ascii=False, indent=2) + "\n"
        manifest["entries"][key] = contract.build_manifest_entry(
            key, content, contract.git_blob_sha(content), 1, STAMP
        )
    return {
        "branch": contract.STATE_BRANCH,
        "commit_sha": HEAD,
        "manifest": contract.validate_manifest(manifest),
        "documents": documents,
        "verified_objects": sorted(audit.EXPECTED_OBJECTS),
        "read_only": True,
    }


class StateStoreIntegrityAuditTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = make_snapshot(authorization_enabled=True)
        self.validator_calls = []

    def validator(self, documents):
        self.validator_calls.append(set(documents))

    def build(self, snapshot=None):
        return audit.build_state_integrity_report(
            self.snapshot if snapshot is None else snapshot,
            domain_validator=self.validator,
        )

    def test_accepts_active_authoritative_store_when_research_is_authorized(self):
        report = self.build()
        self.assertEqual(report["mode"], "ACTIVE_STATE_INTEGRITY_AUDIT")
        self.assertEqual(report["authoritative_source"], contract.STATE_BRANCH)
        self.assertEqual(report["state_store_status"], "ACTIVE")
        self.assertEqual(report["state_store_generation"], 1)
        self.assertTrue(report["authorization_policy_checked"] is False)
        self.assertTrue(report["nvidia_lock_policy_checked"] is False)
        self.assertTrue(report["read_only"])
        self.assertEqual(report["github_writes"], 0)
        self.assertEqual(report["nvidia_requests"], 0)
        self.assertEqual(len(self.validator_calls), 1)

    def test_accepts_authorized_and_paused_state_without_changing_integrity_result(self):
        authorized = self.build()
        paused_snapshot = make_snapshot(authorization_enabled=False)
        paused = self.build(paused_snapshot)
        self.assertEqual(authorized["domain_validation"], paused["domain_validation"])
        self.assertEqual(authorized["verified_objects"], paused["verified_objects"])

    def test_rejects_unexpected_branch(self):
        invalid = copy.deepcopy(self.snapshot)
        invalid["branch"] = "main"
        with self.assertRaises(audit.StateStoreIntegrityError):
            self.build(invalid)

    def test_rejects_invalid_snapshot_identity_or_non_read_only_source(self):
        invalid = copy.deepcopy(self.snapshot)
        invalid["commit_sha"] = "not-a-commit"
        with self.assertRaises(audit.StateStoreIntegrityError):
            self.build(invalid)
        invalid = copy.deepcopy(self.snapshot)
        invalid["read_only"] = False
        with self.assertRaises(audit.StateStoreIntegrityError):
            self.build(invalid)

    def test_rejects_bootstrap_store_or_nonpositive_generation(self):
        invalid = copy.deepcopy(self.snapshot)
        invalid["manifest"]["status"] = "BOOTSTRAP_ONLY"
        invalid["manifest"]["entries"] = {}
        invalid["manifest"]["generation"] = 0
        invalid["manifest"]["updated_at_utc"] = None
        invalid["documents"] = {}
        invalid["verified_objects"] = []
        with self.assertRaises(audit.StateStoreIntegrityError):
            self.build(invalid)

    def test_rejects_missing_or_extra_registered_objects_and_documents(self):
        invalid = copy.deepcopy(self.snapshot)
        invalid["manifest"]["entries"].pop("research_memory")
        with self.assertRaises(audit.StateStoreIntegrityError):
            self.build(invalid)
        invalid = copy.deepcopy(self.snapshot)
        invalid["documents"]["unexpected"] = {}
        with self.assertRaises(audit.StateStoreIntegrityError):
            self.build(invalid)

    def test_rejects_incomplete_reader_verification(self):
        invalid = copy.deepcopy(self.snapshot)
        invalid["verified_objects"].remove("research_memory")
        with self.assertRaises(audit.StateStoreIntegrityError):
            self.build(invalid)

    def test_domain_validation_is_mandatory_and_fail_closed(self):
        def fail(_documents):
            raise ValueError("synthetic invalid state")
        with self.assertRaises(audit.StateStoreIntegrityError):
            audit.build_state_integrity_report(self.snapshot, domain_validator=fail)

    def test_report_does_not_render_archive_rows_or_number_values(self):
        report = self.build()
        rendered = json.dumps(report, sort_keys=True)
        self.assertNotIn("draws", rendered)
        self.assertNotIn("numbers", rendered)
        self.assertNotIn("target_date", rendered)

    def test_integrity_audit_does_not_expose_a_lock_or_authorization_override(self):
        report = self.build()
        self.assertEqual(
            report["runtime_policy_checks"],
            "DELEGATED_TO_CALLING_WORKFLOW",
        )
        self.assertNotIn("global_lock", report)
        self.assertNotIn("unlock", report)


if __name__ == "__main__":
    unittest.main()
