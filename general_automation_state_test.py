import copy
import json
import unittest

import general_automation_state as state


STAMP = "2026-11-04T12:00:00+00:00"


class AutomationStateContractTests(unittest.TestCase):
    def setUp(self):
        self.manifest = state.initial_manifest()

    def entry(self, key="research_memory", content=None, generation=1):
        content = content if content is not None else '{"schema_version": 2, "entries": []}\n'
        return state.build_manifest_entry(key, content, state.git_blob_sha(content), generation, STAMP)

    def test_initial_manifest_is_empty_bootstrap_contract(self):
        self.assertEqual(state.validate_manifest(self.manifest), self.manifest)
        self.assertEqual(self.manifest["branch"], "general-automation-state")
        self.assertEqual(self.manifest["generation"], 0)
        self.assertEqual(self.manifest["status"], "BOOTSTRAP_ONLY")
        self.assertEqual(self.manifest["entries"], {})

    def test_allowlist_contains_only_persistent_runtime_state(self):
        self.assertEqual(
            state.STATE_OBJECTS,
            {
                "archive": "super_loto_archive.json",
                "evaluation_ledger": "general_evaluation_ledger.json",
                "autonomous_research_state": "general_autonomous_research_state.json",
                "research_memory": "general_research_memory.json",
                "nvidia_lock": "general_nvidia_lock.json",
            },
        )
        for excluded in ("general_memory.json", "general_method_registry.json",
                         "general_nvidia_rate_limit.json", "../secret.json"):
            with self.subTest(excluded=excluded):
                with self.assertRaises(state.AutomationStateError):
                    state.validate_state_document(excluded, "{}")

    def test_rejects_non_json_and_non_object_documents(self):
        for content in ("{", "[]", "null", '{"x": NaN}'):
            with self.subTest(content=content):
                with self.assertRaises(state.AutomationStateError):
                    state.validate_state_document("research_memory", content)

    def test_entry_binds_path_git_blob_digest_size_and_generation(self):
        content = '{"schema_version": 2, "entries": []}\n'
        entry = self.entry(content=content)
        self.assertEqual(entry["path"], "general_research_memory.json")
        self.assertEqual(entry["blob_sha"], state.git_blob_sha(content))
        self.assertEqual(entry["byte_length"], len(content.encode("utf-8")))
        self.assertEqual(entry["generation"], 1)
        self.assertEqual(len(entry["content_sha256"]), 64)

    def test_rejects_invalid_blob_sha_generation_and_timestamp(self):
        with self.assertRaises(state.AutomationStateError):
            state.build_manifest_entry("research_memory", "{}", "a" * 40, 1, STAMP)
        with self.assertRaises(state.AutomationStateError):
            state.build_manifest_entry("research_memory", "{}", state.git_blob_sha("{}"), 0, STAMP)
        with self.assertRaises(state.AutomationStateError):
            state.build_manifest_entry("research_memory", "{}", state.git_blob_sha("{}"), 1, "not-a-date")
        with self.assertRaises(state.AutomationStateError):
            state.build_manifest_entry("research_memory", "{}", state.git_blob_sha("{}"), True, STAMP)

    def test_successful_manifest_update_advances_generation(self):
        entry = self.entry()
        updated = state.apply_manifest_update(self.manifest, "research_memory", entry, 0)
        self.assertEqual(updated["generation"], 1)
        self.assertEqual(updated["status"], "ACTIVE")
        self.assertEqual(updated["entries"]["research_memory"], entry)
        self.assertEqual(self.manifest["generation"], 0)

    def test_stale_writer_is_rejected_without_mutating_manifest(self):
        entry = self.entry()
        current = state.apply_manifest_update(self.manifest, "research_memory", entry, 0)
        second = self.entry(key="nvidia_lock", content='{"schema_version": 1}', generation=2)
        with self.assertRaises(state.StaleGenerationError):
            state.apply_manifest_update(current, "nvidia_lock", second, 0)
        self.assertEqual(current["generation"], 1)
        self.assertNotIn("nvidia_lock", current["entries"])

    def test_entry_must_use_next_generation(self):
        with self.assertRaises(state.AutomationStateError):
            state.apply_manifest_update(
                self.manifest, "research_memory", self.entry(generation=2), 0
            )

    def test_manifest_rejects_unknown_fields_identity_and_branch_mismatch(self):
        invalid = copy.deepcopy(self.manifest)
        invalid["unexpected"] = True
        with self.assertRaises(state.AutomationStateError):
            state.validate_manifest(invalid)
        invalid = copy.deepcopy(self.manifest)
        invalid["branch"] = "main"
        with self.assertRaises(state.AutomationStateError):
            state.validate_manifest(invalid)
        invalid = copy.deepcopy(self.manifest)
        invalid["store_id"] = "OTHER"
        with self.assertRaises(state.AutomationStateError):
            state.validate_manifest(invalid)

    def test_manifest_rejects_unknown_objects_and_wrong_paths(self):
        invalid = copy.deepcopy(self.manifest)
        invalid["entries"]["general_memory"] = {}
        with self.assertRaises(state.AutomationStateError):
            state.validate_manifest(invalid)
        entry = self.entry()
        invalid = state.apply_manifest_update(self.manifest, "research_memory", entry, 0)
        invalid["entries"]["research_memory"]["path"] = "../general_memory.json"
        with self.assertRaises(state.AutomationStateError):
            state.validate_manifest(invalid)

    def test_manifest_rejects_generation_and_status_inconsistency(self):
        invalid = copy.deepcopy(self.manifest)
        invalid["generation"] = True
        with self.assertRaises(state.AutomationStateError):
            state.validate_manifest(invalid)
        invalid = copy.deepcopy(self.manifest)
        invalid["status"] = "ACTIVE"
        with self.assertRaises(state.AutomationStateError):
            state.validate_manifest(invalid)

    def test_manifest_rejects_invalid_digest_size_and_future_generation(self):
        invalid = state.apply_manifest_update(
            self.manifest, "research_memory", self.entry(), 0
        )
        for field, value in (("content_sha256", "bad"), ("blob_sha", "bad"),
                             ("byte_length", -1), ("generation", 2)):
            candidate = copy.deepcopy(invalid)
            candidate["entries"]["research_memory"][field] = value
            with self.subTest(field=field):
                with self.assertRaises(state.AutomationStateError):
                    state.validate_manifest(candidate)

    def test_object_content_verification_detects_tampering(self):
        content = '{"schema_version": 2, "entries": []}\n'
        entry = self.entry(content=content)
        self.assertTrue(state.verify_object_content("research_memory", entry, content))
        with self.assertRaises(state.AutomationStateError):
            state.verify_object_content("research_memory", entry, content + " ")
        with self.assertRaises(state.AutomationStateError):
            state.verify_object_content("archive", entry, content)

    def test_manifest_update_rejects_path_swapping_and_unknown_key(self):
        entry = self.entry()
        wrong = copy.deepcopy(entry)
        wrong["path"] = "general_nvidia_lock.json"
        with self.assertRaises(state.AutomationStateError):
            state.apply_manifest_update(self.manifest, "research_memory", wrong, 0)
        with self.assertRaises(state.AutomationStateError):
            state.apply_manifest_update(self.manifest, "legacy_formula", entry, 0)

    def test_timestamp_must_include_timezone(self):
        with self.assertRaises(state.AutomationStateError):
            state.build_manifest_entry("research_memory", "{}", state.git_blob_sha("{}"), 1, "2026-11-04T12:00:00")


if __name__ == "__main__":
    unittest.main()
