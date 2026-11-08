import copy
import unittest
from pathlib import Path
from unittest.mock import patch

import general_automation_state as contract
import general_automation_state_runtime_audit as audit


STAMP = "2026-11-04T13:00:00+00:00"
HEAD = "a" * 40


def make_fixture():
    documents = {
        "archive": {"schema_version": 1, "draws": []},
        "evaluation_ledger": {"schema_version": 1, "purpose": "fixture", "entries": []},
        "autonomous_research_state": {
            "schema_version": 1,
            "project": "GENERAL",
            "authorization": {"enabled": False},
        },
        "research_memory": {"schema_version": 1, "project": "GENERAL", "entries": []},
    }
    manifest = contract.initial_manifest()
    manifest.update({
        "status": "ACTIVE",
        "generation": 1,
        "updated_at_utc": STAMP,
    })
    for key in audit.EXPECTED_OBJECTS:
        content = __import__("json").dumps(documents[key], ensure_ascii=False, indent=2) + "\n"
        manifest["entries"][key] = contract.build_manifest_entry(
            key, content, contract.git_blob_sha(content), 1, STAMP
        )
    snapshot = {
        "branch": contract.STATE_BRANCH,
        "commit_sha": HEAD,
        "manifest": contract.validate_manifest(manifest),
        "documents": documents,
        "verified_objects": sorted(audit.EXPECTED_OBJECTS),
        "read_only": True,
    }
    shadow = {
        "mode": "SHADOW_COMPARE",
        "state_store_commit_sha": HEAD,
        "authoritative_source": contract.STATE_BRANCH,
        "comparison_source": "main_legacy_snapshot",
        "read_only": True,
        "comparisons": {
            "archive": "MATCH",
            "evaluation_ledger": "MATCH",
            "autonomous_research_state": "MATCH",
            "research_memory": "MATCH",
            "nvidia_lock": "NOT_REGISTERED",
        },
        "matched_count": 4,
        "not_registered_count": 1,
        "diverged_count": 0,
        "diverged": False,
    }
    lock = {
        "schema_version": 1,
        "locked": True,
        "reason": "test lock",
        "locked_at": STAMP,
        "locked_by": "unit-test",
        "last_lock_reason": "test lock",
        "last_unlocked_at": None,
        "updated_at": STAMP,
    }
    return snapshot, shadow, lock


class RuntimeStateAuditTests(unittest.TestCase):
    def setUp(self):
        self.snapshot, self.shadow, self.lock = make_fixture()
        self.validator_calls = []

    def validator(self, documents):
        self.validator_calls.append(set(documents))

    def build(self):
        return audit.build_runtime_audit_report(
            self.snapshot, self.shadow, self.lock,
            mode="pre-cutover-diagnostic",
            domain_validator=self.validator,
        )

    def test_state_authoritative_audit_does_not_depend_on_legacy_main_parity(self):
        report = audit.build_runtime_audit_report(
            self.snapshot, None, self.lock,
            domain_validator=self.validator,
        )
        self.assertEqual(
            report["authoritative_source"], contract.STATE_BRANCH
        )
        self.assertEqual(report["audit_mode"], "STATE_AUTHORITATIVE")
        self.assertEqual(
            report["main_state_parity"],
            "NOT_REQUIRED_STATE_STORE_AUTHORITATIVE",
        )
        self.assertFalse(report["legacy_main_comparison_performed"])
        self.assertFalse(report["legacy_main_copies_authoritative"])
        self.assertFalse(report["legacy_main_copies_consumed"])
        self.assertTrue(report["read_only"])
        self.assertFalse(report["target_values_rendered"])

    def test_rejects_unknown_runtime_audit_mode(self):
        with self.assertRaises(audit.RuntimeStateAuditError):
            audit.build_runtime_audit_report(
                self.snapshot, None, self.lock,
                mode="unknown",
                domain_validator=self.validator,
            )

    def test_accepts_active_migrated_state_with_matching_main_parity(self):
        report = self.build()
        self.assertEqual(report["mode"], "ACTIVE_STATE_RUNTIME_AUDIT")
        self.assertEqual(report["state_store_status"], "ACTIVE")
        self.assertEqual(report["state_store_generation"], 1)
        self.assertEqual(report["audit_mode"], "LEGACY_MAIN_PARITY_DIAGNOSTIC_ONLY")
        self.assertEqual(report["main_state_parity"], "PASS_LEGACY_DIAGNOSTIC_ONLY")
        self.assertTrue(report["legacy_main_comparison_performed"])
        self.assertFalse(report["legacy_main_copies_authoritative"])
        self.assertEqual(report["nvidia_lock"], "LOCKED_ON_MAIN_NOT_REGISTERED")
        self.assertTrue(report["read_only"])
        self.assertFalse(report["target_values_rendered"])
        self.assertEqual(len(self.validator_calls), 1)

    def test_rejects_bootstrap_store_after_data_migration(self):
        self.snapshot["manifest"] = contract.initial_manifest()
        self.snapshot["documents"] = {}
        self.snapshot["verified_objects"] = []
        with self.assertRaises(audit.RuntimeStateAuditError):
            self.build()

    def test_rejects_missing_or_extra_registered_objects(self):
        invalid = copy.deepcopy(self.snapshot)
        invalid["manifest"]["entries"].pop("research_memory")
        with self.assertRaises(audit.RuntimeStateAuditError):
            audit.build_runtime_audit_report(
                invalid, self.shadow, self.lock, domain_validator=self.validator
            )
        invalid = copy.deepcopy(self.snapshot)
        invalid["manifest"]["entries"]["nvidia_lock"] = {}
        with self.assertRaises((audit.RuntimeStateAuditError, contract.AutomationStateError)):
            audit.build_runtime_audit_report(
                invalid, self.shadow, self.lock, domain_validator=self.validator
            )

    def test_rejects_shadow_divergence_or_nonmatching_main_data(self):
        shadow = copy.deepcopy(self.shadow)
        shadow["comparisons"]["archive"] = "DIVERGED"
        shadow["diverged"] = True
        shadow["diverged_count"] = 1
        with self.assertRaises(audit.RuntimeStateAuditError):
            audit.build_runtime_audit_report(
                self.snapshot, shadow, self.lock,
                mode="pre-cutover-diagnostic", domain_validator=self.validator
            )

    def test_rejects_nvidia_lock_registered_in_state_store(self):
        shadow = copy.deepcopy(self.shadow)
        shadow["comparisons"]["nvidia_lock"] = "MATCH"
        shadow["matched_count"] = 5
        shadow["not_registered_count"] = 0
        with self.assertRaises(audit.RuntimeStateAuditError):
            audit.build_runtime_audit_report(
                self.snapshot, shadow, self.lock,
                mode="pre-cutover-diagnostic", domain_validator=self.validator
            )

    def test_rejects_unlocked_nvidia_global_lock(self):
        self.lock["locked"] = False
        with self.assertRaises(audit.RuntimeStateAuditError):
            self.build()

    def test_rejects_enabled_autonomous_research(self):
        self.snapshot["documents"]["autonomous_research_state"]["authorization"]["enabled"] = True
        with self.assertRaises(audit.RuntimeStateAuditError):
            self.build()

    def test_domain_validation_is_mandatory(self):
        def fail(_documents):
            raise ValueError("invalid domain data")
        with self.assertRaises(ValueError):
            audit.build_runtime_audit_report(
                self.snapshot, self.shadow, self.lock, domain_validator=fail
            )

    def test_audit_output_contains_no_draw_rows_or_number_sets(self):
        report = self.build()
        rendered = __import__("json").dumps(report, sort_keys=True)
        self.assertNotIn("draws", rendered)
        self.assertNotIn("numbers", rendered)
        self.assertNotIn("target_date", rendered)


    def test_cli_defaults_to_state_authoritative_without_reading_main_copies(self):
        with (
            patch.object(audit, "GitHubStateStoreReader") as reader_class,
            patch.object(Path, "read_text", return_value='{"locked": true}'),
            patch.object(
                audit, "build_runtime_audit_report",
                return_value={"read_only": True},
            ) as build_report,
            patch("builtins.print"),
        ):
            client = reader_class.return_value
            client.read_snapshot.return_value = self.snapshot
            self.assertEqual(audit.main(["--repository", "owner/repository"]), 0)
            client.read_shadow_comparison.assert_not_called()
            self.assertIsNone(build_report.call_args.args[1])
            self.assertEqual(build_report.call_args.kwargs["mode"], "state-authoritative")

    def test_cli_runs_legacy_parity_only_when_explicitly_selected(self):
        with (
            patch.object(audit, "GitHubStateStoreReader") as reader_class,
            patch.object(Path, "read_text", return_value='{"locked": true}'),
            patch.object(
                audit, "build_runtime_audit_report",
                return_value={"read_only": True},
            ) as build_report,
            patch("builtins.print"),
        ):
            client = reader_class.return_value
            client.read_snapshot.return_value = self.snapshot
            client.read_shadow_comparison.return_value = self.shadow
            self.assertEqual(
                audit.main([
                    "--repository", "owner/repository",
                    "--pre-cutover-diagnostic",
                ]),
                0,
            )
            client.read_shadow_comparison.assert_called_once_with()
            self.assertIs(build_report.call_args.args[1], self.shadow)
            self.assertEqual(
                build_report.call_args.kwargs["mode"],
                "pre-cutover-diagnostic",
            )


if __name__ == "__main__":
    unittest.main()
