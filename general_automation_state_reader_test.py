import base64
import copy
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import general_automation_state as contract
import general_automation_state_reader as reader


STAMP = "2026-11-04T12:00:00+00:00"
REPOSITORY = "owner/repository"
HEAD_SHA = "a" * 40
TREE_SHA = "b" * 40


class FakeReader(reader.GitHubStateStoreReader):
    def __init__(self, branch=None, tree=None, blobs=None, branch_sequence=None):
        super().__init__(REPOSITORY, token="test-token")
        self.branch = branch or self.make_branch(HEAD_SHA)
        self.tree = tree or self.make_tree([])
        self.blobs = blobs or {}
        self.branch_sequence = list(branch_sequence) if branch_sequence else None
        self.branch_calls = 0
        self.routes = []

    @staticmethod
    def make_branch(commit_sha, name=contract.STATE_BRANCH):
        return {
            "name": name,
            "commit": {
                "sha": commit_sha,
                "commit": {"tree": {"sha": TREE_SHA}},
            },
        }

    @staticmethod
    def make_tree(entries, tree_sha=TREE_SHA, truncated=False):
        return {"sha": tree_sha, "truncated": truncated, "tree": copy.deepcopy(entries)}

    def _get_json(self, route):
        self.routes.append(route)
        if route.endswith("/branches/" + contract.STATE_BRANCH):
            self.branch_calls += 1
            if self.branch_sequence:
                return copy.deepcopy(self.branch_sequence.pop(0))
            return copy.deepcopy(self.branch)
        expected = "repos/owner/repository/git/trees/" + TREE_SHA + "?recursive=1"
        if route == expected:
            return copy.deepcopy(self.tree)
        if "/git/blobs/" in route:
            sha = route.rsplit("/", 1)[-1]
            raw = self.blobs.get(sha)
            if raw is None:
                raise reader.StateStoreReadError("missing fake blob")
            return {
                "sha": sha,
                "encoding": "base64",
                "size": len(raw),
                "content": base64.b64encode(raw).decode("ascii"),
            }
        raise AssertionError("Unexpected GitHub API route: " + route)



MAIN_HEAD_SHA = "e" * 40
MAIN_TREE_SHA = "f" * 40


class FakeShadowReader(FakeReader):
    def __init__(
        self,
        state_client,
        main_documents,
        *,
        main_branch_sequence=None,
        state_branch_sequence=None,
        main_tree_truncated=False,
    ):
        super().__init__(
            branch=state_client.branch,
            tree=state_client.tree,
            blobs=state_client.blobs,
            branch_sequence=state_branch_sequence,
        )
        self.main_branch_sequence = (
            list(main_branch_sequence) if main_branch_sequence else None
        )
        self.main_branch_calls = 0
        self.main_branch = {
            "name": "main",
            "commit": {
                "sha": MAIN_HEAD_SHA,
                "commit": {"tree": {"sha": MAIN_TREE_SHA}},
            },
        }
        self.main_blobs = {}
        main_entries = []
        for object_key, content in main_documents.items():
            path = contract.STATE_OBJECTS[object_key]
            sha = contract.git_blob_sha(content)
            self.main_blobs[sha] = content.encode("utf-8")
            main_entries.append({
                "path": path, "type": "blob", "mode": "100644", "sha": sha,
            })
        self.blobs.update(self.main_blobs)
        self.main_tree = {
            "sha": MAIN_TREE_SHA,
            "truncated": main_tree_truncated,
            "tree": main_entries,
        }

    def _get_json(self, route):
        if route.endswith("/branches/main"):
            self.main_branch_calls += 1
            if self.main_branch_sequence:
                return copy.deepcopy(self.main_branch_sequence.pop(0))
            return copy.deepcopy(self.main_branch)
        expected = (
            "repos/owner/repository/git/trees/" + MAIN_TREE_SHA + "?recursive=1"
        )
        if route == expected:
            return copy.deepcopy(self.main_tree)
        return super()._get_json(route)


def blob_entry(path, content):
    return {
        "path": path,
        "type": "blob",
        "mode": "100644",
        "sha": contract.git_blob_sha(content),
    }


class GitHubStateStoreReaderTests(unittest.TestCase):
    def make_bootstrap_reader(self):
        manifest_text = json.dumps(contract.initial_manifest(), indent=2) + "\n"
        readme_text = "# GENERAL automation state\n"
        manifest_sha = contract.git_blob_sha(manifest_text)
        readme_sha = contract.git_blob_sha(readme_text)
        return FakeReader(
            tree=FakeReader.make_tree([
                blob_entry("README.md", readme_text),
                blob_entry(contract.MANIFEST_PATH, manifest_text),
            ]),
            blobs={
                manifest_sha: manifest_text.encode("utf-8"),
                readme_sha: readme_text.encode("utf-8"),
            },
        )

    def make_active_reader(self):
        content = '{"schema_version": 2, "entries": []}\n'
        entry = contract.build_manifest_entry(
            "research_memory", content, contract.git_blob_sha(content), 1, STAMP
        )
        manifest = contract.apply_manifest_update(
            contract.initial_manifest(), "research_memory", entry, 0
        )
        manifest_text = json.dumps(manifest, indent=2) + "\n"
        readme_text = "# GENERAL automation state\n"
        manifest_sha = contract.git_blob_sha(manifest_text)
        readme_sha = contract.git_blob_sha(readme_text)
        object_sha = contract.git_blob_sha(content)
        return FakeReader(
            tree=FakeReader.make_tree([
                blob_entry("README.md", readme_text),
                blob_entry(contract.MANIFEST_PATH, manifest_text),
                blob_entry("general_research_memory.json", content),
            ]),
            blobs={
                manifest_sha: manifest_text.encode("utf-8"),
                readme_sha: readme_text.encode("utf-8"),
                object_sha: content.encode("utf-8"),
            },
        )

    def test_reads_bootstrap_branch_and_confirms_second_head_read(self):
        client = self.make_bootstrap_reader()
        snapshot = client.read_snapshot()
        self.assertEqual(snapshot["branch"], contract.STATE_BRANCH)
        self.assertEqual(snapshot["commit_sha"], HEAD_SHA)
        self.assertEqual(snapshot["manifest"]["status"], "BOOTSTRAP_ONLY")
        self.assertEqual(snapshot["manifest"]["entries"], {})
        self.assertEqual(snapshot["documents"], {})
        self.assertEqual(snapshot["verified_objects"], [])
        self.assertIs(snapshot["read_only"], True)
        self.assertEqual(client.branch_calls, 2)

    def test_reads_active_state_only_when_manifest_and_tree_match(self):
        snapshot = self.make_active_reader().read_snapshot()
        self.assertEqual(snapshot["manifest"]["status"], "ACTIVE")
        self.assertEqual(snapshot["verified_objects"], ["research_memory"])
        self.assertEqual(snapshot["documents"]["research_memory"], {
            "schema_version": 2, "entries": []
        })

    def test_rejects_wrong_branch_identity(self):
        client = self.make_bootstrap_reader()
        client.branch["name"] = "main"
        with self.assertRaises(reader.StateStoreReadError):
            client.read_snapshot()

    def test_rejects_truncated_tree_unknown_paths_and_non_regular_files(self):
        client = self.make_bootstrap_reader()
        client.tree["truncated"] = True
        with self.assertRaises(reader.StateStoreReadError):
            client.read_snapshot()

        client = self.make_bootstrap_reader()
        client.tree["tree"].append({
            "path": "unreviewed.json", "type": "blob",
            "mode": "100644", "sha": "c" * 40,
        })
        with self.assertRaises(reader.StateStoreReadError):
            client.read_snapshot()

        client = self.make_bootstrap_reader()
        client.tree["tree"][0]["mode"] = "120000"
        with self.assertRaises(reader.StateStoreReadError):
            client.read_snapshot()

    def test_rejects_duplicate_tree_paths(self):
        client = self.make_bootstrap_reader()
        client.tree["tree"].append(copy.deepcopy(client.tree["tree"][0]))
        with self.assertRaises(reader.StateStoreReadError):
            client.read_snapshot()

    def test_rejects_unindexed_or_missing_state_files(self):
        client = self.make_bootstrap_reader()
        client.tree["tree"].append(blob_entry("general_research_memory.json", "{}"))
        with self.assertRaises(reader.StateStoreReadError):
            client.read_snapshot()

        client = self.make_active_reader()
        client.tree["tree"] = [
            item for item in client.tree["tree"]
            if item["path"] != "general_research_memory.json"
        ]
        with self.assertRaises(reader.StateStoreReadError):
            client.read_snapshot()

    def test_rejects_manifest_blob_that_differs_from_tree(self):
        client = self.make_bootstrap_reader()
        client.tree["tree"][1]["sha"] = "c" * 40
        with self.assertRaises(reader.StateStoreReadError):
            client.read_snapshot()

    def test_rejects_wrong_manifest_branch_and_duplicate_json_keys(self):
        client = self.make_bootstrap_reader()
        text = json.dumps(contract.initial_manifest()).replace(
            '"branch": "general-automation-state"', '"branch": "main"'
        )
        sha = contract.git_blob_sha(text)
        client.tree["tree"][1]["sha"] = sha
        client.blobs[sha] = text.encode("utf-8")
        with self.assertRaises(contract.AutomationStateError):
            client.read_snapshot()

        client = self.make_bootstrap_reader()
        text = '{"schema_version": 1, "schema_version": 1}'
        sha = contract.git_blob_sha(text)
        client.tree["tree"][1]["sha"] = sha
        client.blobs[sha] = text.encode("utf-8")
        with self.assertRaises(contract.AutomationStateError):
            client.read_snapshot()

    def test_rejects_tampered_state_blob(self):
        client = self.make_active_reader()
        object_sha = next(
            item["sha"] for item in client.tree["tree"]
            if item["path"] == "general_research_memory.json"
        )
        client.blobs[object_sha] = b'{"schema_version": 2, "entries": [1]}\n'
        with self.assertRaises(reader.StateStoreReadError):
            client.read_snapshot()

    def test_discards_snapshot_if_branch_moves_during_read(self):
        client = self.make_bootstrap_reader()
        client.branch_sequence = [
            FakeReader.make_branch(HEAD_SHA),
            FakeReader.make_branch("d" * 40),
        ]
        with self.assertRaises(reader.StateStoreReadError):
            client.read_snapshot()

    def test_rejects_bad_repository_and_timeout(self):
        with self.assertRaises(reader.StateStoreReadError):
            reader.GitHubStateStoreReader("not-a-repository")
        with self.assertRaises(reader.StateStoreReadError):
            reader.GitHubStateStoreReader(REPOSITORY, timeout=0)

    def test_transport_uses_https_get_and_bearer_token(self):
        class Response(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        response = Response(b'{"ok": true}')
        with patch.object(reader.urllib.request, "urlopen", return_value=response) as mocked:
            client = reader.GitHubStateStoreReader(REPOSITORY, token="secret-token")
            result = client._get_json("repos/owner/repository/example")
        request = mocked.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertTrue(request.full_url.startswith("https://api.github.com/"))
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")
        self.assertEqual(result, {"ok": True})


    @staticmethod
    def main_documents(research_memory='{"schema_version": 2, "entries": []}\n'):
        return {
            "archive": '{"schema_version": 1, "draws": []}\n',
            "evaluation_ledger": '{"schema_version": 1, "entries": []}\n',
            "autonomous_research_state": '{"schema_version": 1, "attempts": []}\n',
            "research_memory": research_memory,
            "nvidia_lock": '{"schema_version": 1, "locked": true}\n',
        }

    def test_bootstrap_shadow_read_reports_every_object_unregistered(self):
        client = FakeShadowReader(
            self.make_bootstrap_reader(),
            self.main_documents(),
        )
        result = client.read_shadow_comparison()
        self.assertEqual(result["mode"], "SHADOW_COMPARE")
        self.assertEqual(result["authoritative_source"], "main")
        self.assertEqual(result["diverged_count"], 0)
        self.assertEqual(result["matched_count"], 0)
        self.assertEqual(result["not_registered_count"], 5)
        self.assertTrue(all(v == "NOT_REGISTERED" for v in result["comparisons"].values()))
        self.assertIs(result["read_only"], True)
        self.assertNotIn("documents", result)
        self.assertEqual(client.main_branch_calls, 2)

    def test_shadow_read_reports_an_exact_registered_match(self):
        state = self.make_active_reader()
        memory = '{"schema_version": 2, "entries": []}\n'
        client = FakeShadowReader(
            state,
            self.main_documents(research_memory=memory),
        )
        result = client.read_shadow_comparison()
        self.assertEqual(result["comparisons"]["research_memory"], "MATCH")
        self.assertEqual(result["matched_count"], 1)
        self.assertEqual(result["not_registered_count"], 4)
        self.assertEqual(result["diverged_count"], 0)

    def test_shadow_read_reports_divergence_without_repairing_either_source(self):
        state = self.make_active_reader()
        original_state_blob = state.blobs.copy()
        client = FakeShadowReader(
            state,
            self.main_documents(research_memory='{"schema_version": 2, "entries": [{"different": true}]}\n'),
        )
        result = client.read_shadow_comparison()
        self.assertEqual(result["comparisons"]["research_memory"], "DIVERGED")
        self.assertEqual(result["diverged_count"], 1)
        self.assertEqual(result["authoritative_source"], "main")
        self.assertEqual(state.blobs, original_state_blob)

    def test_shadow_read_rejects_missing_main_object_and_truncated_tree(self):
        documents = self.main_documents()
        documents.pop("nvidia_lock")
        client = FakeShadowReader(self.make_bootstrap_reader(), documents)
        with self.assertRaises(reader.StateStoreReadError):
            client.read_shadow_comparison()

        client = FakeShadowReader(
            self.make_bootstrap_reader(),
            self.main_documents(),
            main_tree_truncated=True,
        )
        with self.assertRaises(reader.StateStoreReadError):
            client.read_shadow_comparison()

    def test_shadow_read_discards_result_when_either_branch_moves(self):
        main_changed = copy.deepcopy(FakeShadowReader(
            self.make_bootstrap_reader(), self.main_documents()
        ).main_branch)
        main_changed["commit"]["sha"] = "d" * 40
        client = FakeShadowReader(
            self.make_bootstrap_reader(),
            self.main_documents(),
            main_branch_sequence=[
                {
                    "name": "main",
                    "commit": {"sha": MAIN_HEAD_SHA, "commit": {"tree": {"sha": MAIN_TREE_SHA}}},
                },
                main_changed,
            ],
        )
        with self.assertRaises(reader.StateStoreReadError):
            client.read_shadow_comparison()

        state = self.make_bootstrap_reader()
        moved_state = FakeReader.make_branch("c" * 40)
        client = FakeShadowReader(
            state,
            self.main_documents(),
            state_branch_sequence=[
                FakeReader.make_branch(HEAD_SHA),
                FakeReader.make_branch(HEAD_SHA),
                moved_state,
            ],
        )
        with self.assertRaises(reader.StateStoreReadError):
            client.read_shadow_comparison()

    def test_scheduled_research_runs_shadow_read_before_authorization_without_token(self):
        workflow = (
            Path(__file__).resolve().parent
            / ".github" / "workflows" / "general-autonomous-research.yml"
        ).read_text(encoding="utf-8")
        marker = "      - name: Compare automation state in read-only shadow mode\n"
        start = workflow.find(marker)
        self.assertGreaterEqual(start, 0)
        end = workflow.find("\n      - name:", start + len(marker))
        self.assertGreater(end, start)
        step = workflow[start:end]
        status_step = workflow.find("      - name: Check explicit authorization and local global-lock state\n")
        self.assertLess(start, status_step)
        self.assertIn("general_automation_state_reader.py --shadow-compare", step)
        self.assertIn("GITHUB_REPOSITORY:", step)
        self.assertNotIn("GITHUB_TOKEN", step)
        self.assertIn("main remains authoritative", step)
        self.assertIn("shadow_status", step)
        self.assertNotIn("git push", step)
        self.assertNotIn("NVIDIA_API_KEY", step)



if __name__ == "__main__":
    unittest.main()
