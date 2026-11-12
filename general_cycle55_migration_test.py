import hashlib
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import general_automation_state as state_contract
import general_cycle55_migration as migration


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _synthetic_source(generation, commit_sha):
    state_raw = _json_bytes({
        "schema_version": 2,
        "project": "GENERAL Super Loto",
        "authorization": {},
        "attempts": [],
        "events": [],
        "target_queue": {},
    })
    manifest = state_contract.initial_manifest()
    manifest["generation"] = generation
    manifest["status"] = "ACTIVE"
    manifest["updated_at_utc"] = "2026-11-01T00:00:00+00:00"
    manifest["entries"] = {}
    objects = {
        "archive": _json_bytes({"test_placeholder": "archive"}).decode("utf-8"),
        "evaluation_ledger": _json_bytes({"test_placeholder": "evaluation_ledger"}).decode("utf-8"),
        "autonomous_research_state": state_raw.decode("utf-8"),
        "research_memory": _json_bytes({"test_placeholder": "research_memory"}).decode("utf-8"),
    }
    for key, content in objects.items():
        entry_generation = generation if key == "autonomous_research_state" else 1
        blob_sha = state_contract.git_blob_sha(content)
        manifest["entries"][key] = state_contract.build_manifest_entry(
            key,
            content,
            blob_sha,
            entry_generation,
            "2026-11-01T00:00:00+00:00",
        )
    manifest = state_contract.validate_manifest(manifest)
    return _json_bytes(manifest), state_raw, {
        "commit_sha": commit_sha,
        "generation": generation,
        "blob_sha": migration._git_blob_sha(state_raw),
        "content_sha256": hashlib.sha256(state_raw).hexdigest(),
    }


class FakeActionsAPI:
    def __init__(self, *, state="active", pages=None):
        self.state = state
        self.pages = pages or [[]]
        self.calls = []
        self.fail_pause = False

    def request(self, method, path, token):
        self.calls.append((method, path))
        base = "/repos/test/repo/actions/workflows/" + migration.WORKFLOW_ID
        if path == base and method == "GET":
            return 200, _json_bytes({
                "id": 123,
                "path": migration.WORKFLOW_FILE,
                "state": self.state,
            })
        if path == base + "/disable" and method == "PUT":
            if self.fail_pause:
                raise OSError("synthetic uncertain transport")
            self.state = "disabled_manually"
            return 204, b""
        if path.startswith(base + "/runs?") and method == "GET":
            query = parse_qs(urlsplit(path).query)
            page = int(query["page"][0])
            if "event" in query or query.get("per_page") != ["100"]:
                raise AssertionError("run query must include all event types and paginate")
            items = self.pages[page - 1] if page <= len(self.pages) else []
            return 200, _json_bytes({"total_count": sum(map(len, self.pages)), "workflow_runs": items})
        raise AssertionError("unexpected request: " + method + " " + path)


def _run(run_id, status="completed", event="schedule"):
    return {"id": run_id, "event": event, "status": status}


class WorkflowPauseGateTests(unittest.TestCase):
    def make_client(self, api):
        return migration.GitHubActionsWorkflowClient(
            "test/repo",
            "not-a-real-token",
            request_func=api.request,
        )

    def test_pause_disables_workflow_but_never_cancels_existing_runs(self):
        api = FakeActionsAPI(
            state="active",
            pages=[[_run(10, "queued"), _run(11, "in_progress")]],
        )
        client = self.make_client(api)
        result = client.pause()
        self.assertEqual(result["status"], "WORKFLOW_PAUSED_DRAINING")
        self.assertEqual(result["active_workflow_run_count"], 2)
        self.assertEqual(result["runs_cancelled"], 0)
        self.assertEqual([call[0] for call in api.calls].count("PUT"), 1)
        self.assertFalse(any("cancel" in path for _, path in api.calls))
        with self.assertRaisesRegex(migration.MigrationGateError, "still active"):
            client.assert_ready()

    def test_assert_ready_succeeds_only_after_all_workflow_runs_complete(self):
        api = FakeActionsAPI(
            state="disabled_manually",
            pages=[[_run(10, "completed"), _run(11, "completed")]],
        )
        result = self.make_client(api).assert_ready()
        self.assertEqual(result["status"], "MIGRATION_GATE_READY")
        self.assertEqual(result["active_workflow_run_count"], 0)

    def test_already_manually_disabled_workflow_is_not_disabled_again(self):
        api = FakeActionsAPI(state="disabled_manually", pages=[[]])
        result = self.make_client(api).pause()
        self.assertEqual(result["status"], "WORKFLOW_PAUSED_READY")
        self.assertEqual([method for method, _ in api.calls].count("PUT"), 0)

    def test_workflow_disabled_for_other_reason_is_not_reclassified_or_changed(self):
        api = FakeActionsAPI(state="disabled_inactivity", pages=[[]])
        with self.assertRaisesRegex(migration.MigrationGateError, "other than manual pause"):
            self.make_client(api).pause()
        self.assertEqual([method for method, _ in api.calls].count("PUT"), 0)

    def test_unknown_workflow_state_fails_closed(self):
        api = FakeActionsAPI(state="disabled_unknown", pages=[[]])
        with self.assertRaisesRegex(migration.MigrationGateError, "unknown or unsupported"):
            self.make_client(api).status()

    def test_pagination_scans_every_workflow_run_page(self):
        first = [_run(index + 1, "completed") for index in range(100)]
        second = [_run(101, "queued")]
        api = FakeActionsAPI(state="disabled_manually", pages=[first, second])
        result = self.make_client(api).status()
        run_calls = [path for method, path in api.calls if method == "GET" and "/runs?" in path]
        self.assertEqual(len(run_calls), 2)
        self.assertEqual(result["workflow_run_count"], 101)
        self.assertEqual(result["active_workflow_run_count"], 1)

    def test_duplicate_run_ids_fail_closed(self):
        api = FakeActionsAPI(
            state="disabled_manually",
            pages=[[_run(7), _run(7)]],
        )
        with self.assertRaisesRegex(migration.MigrationGateError, "duplicated"):
            self.make_client(api).status()

    def test_manual_dispatch_run_is_included_in_drain_status(self):
        api = FakeActionsAPI(
            state="disabled_manually",
            pages=[[_run(8, "in_progress", event="workflow_dispatch")]],
        )
        result = self.make_client(api).status()
        self.assertEqual(result["active_workflow_run_count"], 1)
        self.assertEqual(result["active_workflow_runs"][0]["event"], "workflow_dispatch")
        with self.assertRaisesRegex(migration.MigrationGateError, "still active"):
            self.make_client(api).assert_ready()

    def test_missing_run_event_fails_closed(self):
        api = FakeActionsAPI(
            state="disabled_manually",
            pages=[[{"id": 8, "status": "completed"}]],
        )
        with self.assertRaisesRegex(migration.MigrationGateError, "event is missing"):
            self.make_client(api).status()

    def test_uncertain_pause_is_not_retried_automatically(self):
        api = FakeActionsAPI(state="active", pages=[[]])
        api.fail_pause = True
        client = self.make_client(api)
        with self.assertRaisesRegex(migration.MigrationGateError, "uncertain"):
            client.pause()
        self.assertEqual([method for method, _ in api.calls].count("PUT"), 1)

    def test_incomplete_or_unknown_run_status_blocks_readiness(self):
        api = FakeActionsAPI(
            state="disabled_manually",
            pages=[[_run(12, "waiting")]],
        )
        with self.assertRaisesRegex(migration.MigrationGateError, "still active"):
            self.make_client(api).assert_ready()

    def test_run_arriving_during_quiet_window_blocks_migration(self):
        api = FakeActionsAPI(state="disabled_manually", pages=[[]])
        sleeps = []

        def inject_run(seconds):
            sleeps.append(seconds)
            api.pages = [[_run(13, "queued")]]

        client = migration.GitHubActionsWorkflowClient(
            "test/repo",
            "not-a-real-token",
            request_func=api.request,
            sleep_func=inject_run,
        )
        with self.assertRaisesRegex(migration.MigrationGateError, "quiet window"):
            client.assert_ready(settle_seconds=60)
        self.assertEqual(sleeps, [60.0])

    def test_invalid_drain_quiet_window_is_rejected(self):
        api = FakeActionsAPI(state="disabled_manually", pages=[[]])
        with self.assertRaisesRegex(migration.MigrationGateError, "between 0 and 300"):
            self.make_client(api).assert_ready(settle_seconds=301)


class PinnedSourceVerificationTests(unittest.TestCase):
    def test_generation_51_and_52_sources_require_exact_commit_and_blob_pins(self):
        for generation in (51, 52):
            expected_commit = migration.PINNED_STATE_PROVENANCE[generation]["commit_sha"]
            manifest_raw, state_raw, test_pin = _synthetic_source(generation, expected_commit)
            with patch.dict(migration.PINNED_STATE_PROVENANCE, {generation: test_pin}):
                result = migration.verify_pinned_state_source(
                    manifest_raw,
                    state_raw,
                    generation=generation,
                    commit_sha=expected_commit,
                )
            self.assertTrue(result["verified"])
            self.assertEqual(result["generation"], generation)
            self.assertEqual(result["raw_schema_version"], 2)
            self.assertFalse(result["draw_rows_rendered"])
            self.assertFalse(result["target_values_rendered"])

    def test_generation_52_requires_pinned_commit_anchor(self):
        with self.assertRaisesRegex(migration.MigrationGateError, "pinned Git history"):
            migration.verify_pinned_state_source(
                b"{}",
                b"{}",
                generation=52,
                commit_sha="f" * 40,
            )

    def test_generation_other_than_51_or_52_is_rejected(self):
        with self.assertRaisesRegex(migration.MigrationGateError, "Only the pinned"):
            migration.verify_pinned_state_source(
                b"{}",
                b"{}",
                generation=50,
                commit_sha="f" * 40,
            )

    def test_tampered_state_bytes_are_rejected(self):
        manifest_raw, state_raw, pin = _synthetic_source(
            51,
            migration.PINNED_STATE_PROVENANCE[51]["commit_sha"],
        )
        with patch.dict(migration.PINNED_STATE_PROVENANCE, {51: pin}):
            with self.assertRaisesRegex(migration.MigrationGateError, "do not match"):
                migration.verify_pinned_state_source(
                    manifest_raw,
                    state_raw + b" ",
                    generation=51,
                    commit_sha=pin["commit_sha"],
                )

    def test_tampered_manifest_generation_is_rejected(self):
        manifest_raw, state_raw, pin = _synthetic_source(
            51,
            migration.PINNED_STATE_PROVENANCE[51]["commit_sha"],
        )
        manifest = json.loads(manifest_raw)
        manifest["generation"] = 50
        with patch.dict(migration.PINNED_STATE_PROVENANCE, {51: pin}):
            with self.assertRaisesRegex(migration.MigrationGateError, "generation"):
                migration.verify_pinned_state_source(
                    _json_bytes(manifest),
                    state_raw,
                    generation=51,
                    commit_sha=pin["commit_sha"],
                )

    def test_duplicate_manifest_keys_are_rejected(self):
        with self.assertRaisesRegex(migration.MigrationGateError, "duplicate"):
            migration._strict_json_bytes(b'{"schema_version":1,"schema_version":1}', "test")

    def test_manifest_missing_a_runtime_object_is_rejected(self):
        manifest_raw, state_raw, pin = _synthetic_source(
            51,
            migration.PINNED_STATE_PROVENANCE[51]["commit_sha"],
        )
        manifest = json.loads(manifest_raw)
        manifest["entries"].pop("research_memory")
        with patch.dict(migration.PINNED_STATE_PROVENANCE, {51: pin}):
            with self.assertRaisesRegex(migration.MigrationGateError, "exact four"):
                migration.verify_pinned_state_source(
                    _json_bytes(manifest),
                    state_raw,
                    generation=51,
                    commit_sha=pin["commit_sha"],
                )

    def test_wrong_raw_schema_is_rejected(self):
        state_raw = _json_bytes({"schema_version": 3})
        manifest_raw, _, pin = _synthetic_source(
            52,
            migration.PINNED_STATE_PROVENANCE[52]["commit_sha"],
        )
        manifest = json.loads(manifest_raw)
        entry = manifest["entries"]["autonomous_research_state"]
        entry["blob_sha"] = migration._git_blob_sha(state_raw)
        entry["content_sha256"] = hashlib.sha256(state_raw).hexdigest()
        entry["byte_length"] = len(state_raw)
        pin = {
            **pin,
            "blob_sha": entry["blob_sha"],
            "content_sha256": entry["content_sha256"],
        }
        with patch.dict(migration.PINNED_STATE_PROVENANCE, {52: pin}):
            with self.assertRaisesRegex(migration.MigrationGateError, "schema v2"):
                migration.verify_pinned_state_source(
                    _json_bytes(manifest),
                    state_raw,
                    generation=52,
                    commit_sha=pin["commit_sha"],
                )


if __name__ == "__main__":
    unittest.main()
