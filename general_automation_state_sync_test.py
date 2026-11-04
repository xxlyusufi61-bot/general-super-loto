import json
import tempfile
import unittest
from pathlib import Path

import general_automation_state as contract
import general_automation_state_sync as sync
import general_autonomous_research as autonomous
import general_nvidia_guard as nvidia_guard
import general_research_memory as research_memory


MAIN_SHA = "a" * 40
STATE_SHA = "b" * 40
STAMP = "2026-11-04T12:00:00+00:00"


class StateSyncPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.documents = self._documents()
        self._write_documents()
        self.shadow = {
            "mode": "SHADOW_COMPARE",
            "main_commit_sha": MAIN_SHA,
            "state_store_commit_sha": STATE_SHA,
            "state_store_status": "BOOTSTRAP_ONLY",
            "state_store_generation": 0,
            "comparisons": {key: "NOT_REGISTERED" for key in contract.STATE_OBJECTS},
            "matched_count": 0,
            "not_registered_count": len(contract.STATE_OBJECTS),
            "diverged_count": 0,
            "diverged": False,
            "authoritative_source": "main",
            "read_only": True,
        }

    def tearDown(self):
        self.temp.cleanup()

    def _documents(self):
        lock = nvidia_guard.make_locked_state(
            nvidia_guard.initial_lock_state(),
            "Synthetic test lock; no provider call is authorized.",
            stamp=STAMP,
            locked_by="unit-test",
        )
        years = range(2020, 2027)
        dates = [f"{year}-01-02" for year in years]
        archive = {
            "source": "synthetic fixture",
            "cross_check_source": "synthetic independent source",
            "cross_check_validation": {
                "year": 2025,
                "primary_record_count": 1,
                "independent_record_count": 1,
                "matching_dates_and_number_sets": 1,
                "result": "PASS",
                "checked_at": STAMP,
                "pages_checked": ["synthetic"],
            },
            "multi_year_cross_source_validation": {
                str(year): {
                    "result": "PASS",
                    "archive_compared_count_before_merge": 1,
                    "independent_source_count": 1,
                    "records_added_from_independent_source": 0,
                    "matching_dates_and_number_sets_after_merge": 1,
                    "draw_numbers_in_report": False,
                    "source": "synthetic independent source",
                    "pages_checked": ["synthetic"],
                    "checked_at": STAMP,
                }
                for year in years
            },
            "fetched_at": STAMP,
            "corrections_applied": [],
            "draws": [
                {"date": draw_date, "numbers": [1, 2, 3, 4, 5, 6]}
                for draw_date in dates
            ],
        }
        return {
            "archive": archive,
            "evaluation_ledger": {
                "schema_version": 1,
                "purpose": "Synthetic empty ledger.",
                "entries": [],
            },
            "autonomous_research_state": autonomous.initial_state(),
            "research_memory": research_memory.empty_memory(),
            "nvidia_lock": lock,
        }

    def _write_documents(self):
        for key, value in self.documents.items():
            (self.root / contract.STATE_OBJECTS[key]).write_text(
                json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    def _plan(self):
        return sync.build_sync_preflight_plan(self.root, MAIN_SHA, self.shadow)

    def test_builds_deterministic_metadata_only_plan(self):
        first = self._plan()
        second = self._plan()
        self.assertEqual(first, second)
        self.assertEqual(first["mode"], "READ_ONLY_STATE_SYNC_PREFLIGHT")
        self.assertEqual(first["source"]["commit_sha"], MAIN_SHA)
        self.assertEqual(first["target_store"]["status"], "BOOTSTRAP_ONLY")
        self.assertEqual(first["target_store"]["generation"], 0)
        self.assertEqual(first["future_atomic_transfer_order"], list(sync.COPY_ORDER))
        self.assertEqual(first["deferred_objects"], ["nvidia_lock"])
        self.assertFalse(first["safety"]["data_transferred"])
        self.assertEqual(first["safety"]["github_write_operations"], 0)
        self.assertEqual(first["safety"]["nvidia_requests"], 0)
        self.assertFalse(first["safety"]["target_values_rendered"])

    def test_plan_contains_hashes_but_never_source_payloads_or_target_identifiers(self):
        plan = self._plan()
        rendered = json.dumps(plan, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("2026-01-02", rendered)
        self.assertNotIn('"numbers"', rendered)
        self.assertNotIn('"draws"', rendered)
        self.assertNotIn("Synthetic test lock", rendered)
        for item in plan["objects"]:
            self.assertEqual(len(item["git_blob_sha"]), 40)
            self.assertEqual(len(item["content_sha256"]), 64)
            self.assertGreater(item["byte_length"], 0)

    def test_global_lock_must_be_locked_and_is_never_in_transfer_order(self):
        self.documents["nvidia_lock"]["locked"] = False
        self._write_documents()
        with self.assertRaises(sync.SyncPreflightError):
            self._plan()
        self.assertNotIn("nvidia_lock", sync.COPY_ORDER)

    def test_autonomous_research_must_be_paused(self):
        state = self.documents["autonomous_research_state"]
        state["authorization"]["enabled"] = True
        state["authorization"].update({
            "authorized_at_utc": "2026-11-04T11:00:00+00:00",
            "expires_at_utc": "2026-11-05T11:00:00+00:00",
            "authorization_days": 1,
            "free_usage_verified": True,
        })
        self._write_documents()
        with self.assertRaises(sync.SyncPreflightError):
            self._plan()

    def test_rejects_mismatched_main_head(self):
        with self.assertRaises(sync.SyncPreflightError):
            sync.build_sync_preflight_plan(self.root, "c" * 40, self.shadow)

    def test_rejects_state_store_that_is_not_empty_bootstrap(self):
        self.shadow["state_store_generation"] = 1
        self.shadow["state_store_status"] = "ACTIVE"
        with self.assertRaises(sync.SyncPreflightError):
            self._plan()

    def test_rejects_any_registered_or_diverged_object(self):
        self.shadow["comparisons"]["archive"] = "MATCH"
        self.shadow["not_registered_count"] = len(contract.STATE_OBJECTS) - 1
        self.shadow["matched_count"] = 1
        with self.assertRaises(sync.SyncPreflightError):
            self._plan()

    def test_rejects_archive_without_cross_source_evidence(self):
        self.documents["archive"]["cross_check_validation"]["result"] = "UNKNOWN"
        self._write_documents()
        with self.assertRaises(sync.SyncPreflightError):
            self._plan()

    def test_rejects_noncanonical_archive_order_or_values(self):
        self.documents["archive"]["draws"].reverse()
        self._write_documents()
        with self.assertRaises(sync.SyncPreflightError):
            self._plan()

    def test_rejects_multi_year_source_counts_that_do_not_reconcile(self):
        self.documents["archive"]["multi_year_cross_source_validation"]["2025"][
            "matching_dates_and_number_sets_after_merge"
        ] = 2
        self._write_documents()
        with self.assertRaises(sync.SyncPreflightError):
            self._plan()

    def test_rejects_audit_timestamp_later_than_archive_fetch(self):
        self.documents["archive"]["multi_year_cross_source_validation"]["2024"][
            "checked_at"
        ] = "2026-11-05T12:00:00+00:00"
        self._write_documents()
        with self.assertRaises(sync.SyncPreflightError):
            self._plan()

    def test_nvidia_rate_limit_state_remains_excluded(self):
        plan = self._plan()
        self.assertIn("nvidia_rate_limit", plan["excluded_inputs"])
        self.assertEqual(
            plan["excluded_inputs"]["nvidia_rate_limit"]["branch"],
            "general-nvidia-rate-limit-state",
        )
        self.assertFalse(plan["safety"]["rate_limit_branch_touched"])


if __name__ == "__main__":
    unittest.main()
