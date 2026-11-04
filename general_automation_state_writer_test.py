import copy
import json
import tempfile
import unittest
from pathlib import Path

import general_automation_state as contract
import general_automation_state_writer as writer_module


STAMP = "2026-11-04T13:00:00+00:00"
BASE_COMMIT = "a" * 40
BASE_TREE = "b" * 40
NEW_TREE = "c" * 40
NEW_COMMIT = "d" * 40


def make_documents():
    return {
        "archive": {"schema_version": 1, "draws": []},
        "evaluation_ledger": {"schema_version": 1, "purpose": "test", "entries": []},
        "autonomous_research_state": {
            "schema_version": 1,
            "project": "GENERAL",
            "authorization": {
                "enabled": False,
                "authorized_at_utc": None,
                "expires_at_utc": None,
                "authorization_days": 0,
                "free_usage_verified": False,
                "max_requests_per_target": 2,
                "last_paused_at_utc": None,
            },
            "attempts": [],
            "events": [],
        },
        "research_memory": {
            "schema_version": 1,
            "project": "GENERAL",
            "research_epoch": "test",
            "purpose": "test fixture",
            "entries": [],
        },
    }


def make_snapshot(documents=None, generation=1):
    docs = copy.deepcopy(documents or make_documents())
    manifest = contract.initial_manifest()
    manifest["generation"] = generation
    manifest["status"] = "ACTIVE"
    manifest["updated_at_utc"] = STAMP
    for key in writer_module.WRITABLE_OBJECTS:
        content = writer_module._canonical_json(docs[key])
        manifest["entries"][key] = contract.build_manifest_entry(
            key, content, contract.git_blob_sha(content), generation, STAMP
        )
    return {
        "branch": contract.STATE_BRANCH,
        "commit_sha": BASE_COMMIT,
        "manifest": contract.validate_manifest(manifest),
        "documents": docs,
        "verified_objects": list(writer_module.WRITABLE_OBJECTS),
        "read_only": True,
    }


class FakeReader:
    def __init__(self, snapshot=None, head_sequence=None):
        self.snapshot = copy.deepcopy(snapshot or make_snapshot())
        self.head_sequence = list(head_sequence or [(BASE_COMMIT, BASE_TREE)])
        self.head_calls = 0

    def read_snapshot(self):
        return copy.deepcopy(self.snapshot)

    def _branch_head(self, branch_name=contract.STATE_BRANCH):
        if branch_name != contract.STATE_BRANCH:
            raise AssertionError("writer attempted to read an unapproved branch")
        self.head_calls += 1
        if len(self.head_sequence) > 1:
            return self.head_sequence.pop(0)
        return self.head_sequence[0]


class FakeTransport:
    def __init__(self, patch_error=None):
        self.calls = []
        self.patch_error = patch_error

    def __call__(self, method, route, payload):
        self.calls.append((method, route, copy.deepcopy(payload)))
        if route.endswith("/git/trees"):
            return {"sha": NEW_TREE}
        if route.endswith("/git/commits"):
            return {"sha": NEW_COMMIT}
        if "/git/refs/heads/" in route:
            if self.patch_error:
                raise self.patch_error
            return {
                "ref": "refs/heads/" + contract.STATE_BRANCH,
                "object": {"sha": NEW_COMMIT},
            }
        raise AssertionError("Unexpected write route: " + route)


class GitHubStateStoreWriterTests(unittest.TestCase):
    def setUp(self):
        self.reader = FakeReader()
        self.transport = FakeTransport()
        self.writer = writer_module.GitHubStateStoreWriter(
            "owner/repository",
            "test-token",
            reader=self.reader,
            request_func=self.transport,
            domain_validator=lambda docs: None,
        )

    def test_single_object_publish_is_one_commit_and_one_generation(self):
        updated_archive = {"schema_version": 1, "draws": [{"date": "2026-11-03", "numbers": [1, 2, 3, 4, 5, 6]}]}
        result = self.writer.publish(
            {"archive": writer_module._canonical_json(updated_archive)},
            expected_generation=1,
            message="Update verified archive",
        )
        self.assertTrue(result["updated"])
        self.assertTrue(result["atomic_commit"])
        self.assertEqual(result["generation"], 2)
        self.assertEqual(result["updated_objects"], ["archive"])
        self.assertEqual([call[0] for call in self.transport.calls], ["POST", "POST", "PATCH"])
        tree_entries = self.transport.calls[0][2]["tree"]
        self.assertEqual({item["path"] for item in tree_entries}, {"super_loto_archive.json", "manifest.json"})
        self.assertEqual(self.transport.calls[0][2]["base_tree"], BASE_TREE)
        self.assertEqual(self.transport.calls[1][2]["parents"], [BASE_COMMIT])
        self.assertEqual(self.transport.calls[2][2], {"sha": NEW_COMMIT, "force": False})

    def test_multiple_objects_share_one_generation_and_commit(self):
        updates = {
            "evaluation_ledger": writer_module._canonical_json({"schema_version": 1, "purpose": "changed", "entries": []}),
            "research_memory": writer_module._canonical_json({
                "schema_version": 1, "project": "GENERAL", "research_epoch": "test",
                "purpose": "changed", "entries": [],
            }),
        }
        result = self.writer.publish(updates, expected_generation=1, message="Batch state update")
        self.assertEqual(result["generation"], 2)
        self.assertEqual(result["updated_objects"], ["evaluation_ledger", "research_memory"])
        self.assertEqual(len(self.transport.calls), 3)
        manifest_text = next(
            item["content"] for item in self.transport.calls[0][2]["tree"]
            if item["path"] == "manifest.json"
        )
        manifest = json.loads(manifest_text)
        self.assertEqual(manifest["generation"], 2)
        self.assertEqual(manifest["entries"]["evaluation_ledger"]["generation"], 2)
        self.assertEqual(manifest["entries"]["research_memory"]["generation"], 2)

    def test_stale_generation_fails_before_any_remote_write(self):
        with self.assertRaises(writer_module.StateStoreWriteConflict):
            self.writer.publish(
                {"archive": writer_module._canonical_json({"schema_version": 1, "draws": []})},
                expected_generation=0,
                message="Stale update",
            )
        self.assertEqual(self.transport.calls, [])

    def test_nvidia_lock_is_not_writable_by_this_writer(self):
        with self.assertRaises(writer_module.StateStoreWriteError):
            self.writer.publish(
                {"nvidia_lock": '{"schema_version":1,"locked":true}'},
                expected_generation=1,
                message="Forbidden lock update",
            )
        self.assertEqual(self.transport.calls, [])

    def test_invalid_json_is_rejected_before_any_remote_write(self):
        with self.assertRaises(contract.AutomationStateError):
            self.writer.publish(
                {"archive": '{"draws": []'},
                expected_generation=1,
                message="Invalid state",
            )
        self.assertEqual(self.transport.calls, [])

    def test_branch_moved_before_tree_creation_is_rejected(self):
        reader = FakeReader(head_sequence=[
            (BASE_COMMIT, BASE_TREE),
            ("e" * 40, "f" * 40),
        ])
        transport = FakeTransport()
        writer = writer_module.GitHubStateStoreWriter(
            "owner/repository", "test-token", reader=reader,
            request_func=transport, domain_validator=lambda docs: None,
        )
        with self.assertRaises(writer_module.StateStoreWriteConflict):
            writer.publish(
                {"archive": writer_module._canonical_json({"schema_version": 1, "draws": [{"date": "2026-11-03", "numbers": [1,2,3,4,5,6]}]})},
                expected_generation=1,
                message="Race test",
            )
        self.assertEqual(transport.calls, [])

    def test_failed_non_fast_forward_ref_update_is_not_retried(self):
        transport = FakeTransport(patch_error=writer_module.StateStoreWriteConflict("race"))
        writer = writer_module.GitHubStateStoreWriter(
            "owner/repository", "test-token", reader=self.reader,
            request_func=transport, domain_validator=lambda docs: None,
        )
        with self.assertRaises(writer_module.StateStoreWriteConflict):
            writer.publish(
                {"archive": writer_module._canonical_json({"schema_version": 1, "draws": [{"date": "2026-11-03", "numbers": [1,2,3,4,5,6]}]})},
                expected_generation=1,
                message="Non fast forward",
            )
        self.assertEqual([call[0] for call in transport.calls], ["POST", "POST", "PATCH"])

    def test_noop_does_not_create_a_commit(self):
        content = writer_module._canonical_json(self.reader.snapshot["documents"]["archive"])
        result = self.writer.publish(
            {"archive": content},
            expected_generation=1,
            message="No-op",
        )
        self.assertFalse(result["updated"])
        self.assertEqual(result["commit_sha"], BASE_COMMIT)
        self.assertEqual(self.transport.calls, [])

    def test_incomplete_state_snapshot_fails_closed(self):
        self.reader.snapshot["documents"].pop("research_memory")
        with self.assertRaises(writer_module.StateStoreWriteError):
            self.writer.publish(
                {"archive": writer_module._canonical_json({"schema_version": 1, "draws": []})},
                expected_generation=1,
                message="Incomplete snapshot",
            )
        self.assertEqual(self.transport.calls, [])

    def test_hydrate_writes_only_the_four_allowlisted_objects(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.writer.hydrate(temp)
            expected = {contract.STATE_OBJECTS[key] for key in writer_module.WRITABLE_OBJECTS}
            self.assertEqual({path.name for path in Path(temp).iterdir()}, expected)
            self.assertEqual(result["generation"], 1)
            self.assertNotIn("general_nvidia_lock.json", expected)

    def test_writer_rejects_empty_updates_and_bad_generation_type(self):
        with self.assertRaises(writer_module.StateStoreWriteError):
            self.writer.publish({}, expected_generation=1, message="Empty")
        with self.assertRaises(writer_module.StateStoreWriteError):
            self.writer.publish(
                {"archive": "{}"}, expected_generation=True, message="Boolean generation"
            )
        self.assertEqual(self.transport.calls, [])


if __name__ == "__main__":
    unittest.main()
