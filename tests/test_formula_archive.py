import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_PATH = ROOT / "formula_archive" / "legacy_candidates.json"
REGISTRY_PATH = ROOT / "general_method_registry.json"


class LegacyFormulaArchiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.archive = json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
        cls.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    def test_archive_schema_and_counts(self):
        self.assertEqual(self.archive["schema_version"], 1)
        self.assertEqual(self.archive["project"], "GENERAL Super Loto")
        self.assertEqual(self.archive["archive_version"], "0.1.0")
        self.assertEqual(self.archive["counts"]["core_method_candidates"], 3)
        self.assertEqual(self.archive["counts"]["generator_candidates"], 21)
        self.assertEqual(len(self.archive["generator_candidates"]), 21)
        self.assertEqual(len(self.archive["transition_signature_features"]), 12)

    def test_source_hashes_are_recorded_and_well_formed(self):
        sources = self.archive["sources"]
        self.assertEqual(len(sources), 2)
        for source in sources:
            self.assertEqual(len(source["sha256"]), 64)
            int(source["sha256"], 16)
            self.assertGreater(source["line_count"], 0)
            self.assertTrue(source["relevant_functions"])
        by_id = {item["source_id"]: item for item in sources}
        self.assertEqual(
            by_id["v8_6_transition_discovery"]["sha256"],
            "47dd3164def60ca0ea29cdbb07d11be061d10e8cc7a20dfa5f0cd72fc3a4f1aa",
        )
        self.assertEqual(
            by_id["cipher_motor_v1"]["sha256"],
            "1d6df432818693d764a028a4be190e497a42b7068c309585cabf6fe2aaa0b6b2",
        )
        self.assertTrue(by_id["cipher_motor_v1"]["has_embedded_draw_archive"])

    def test_project_binding_methods_stay_open_and_execution_disabled(self):
        registry_methods = self.registry.get("methods")
        self.assertIsInstance(registry_methods, dict)
        archived_methods = {item["registry_key"]: item for item in self.archive["core_method_candidates"]}
        self.assertEqual(set(archived_methods), {"A->B->C", "A->T", "MOD5->T"})
        self.assertEqual(set(archived_methods), set(registry_methods))
        for name, registry_entry in registry_methods.items():
            with self.subTest(method=name):
                self.assertEqual(registry_entry.get("status"), "OPEN_DEFINITION")
                self.assertIsNone(registry_entry.get("canonical_definition"))
                self.assertIs(registry_entry.get("execution_ready"), False)
                self.assertIs(archived_methods[name]["execution_ready"], False)
                self.assertEqual(archived_methods[name]["status"], "OPEN_DEFINITION")

    def test_every_generator_remains_unapproved(self):
        names = [item["name"] for item in self.archive["generator_candidates"]]
        self.assertEqual(len(names), len(set(names)))
        for item in self.archive["generator_candidates"]:
            with self.subTest(generator=item["name"]):
                self.assertIs(item["execution_ready"], False)
                self.assertEqual(item["status"], "LEGACY_CANDIDATE_NOT_VALIDATED")
                self.assertIn(item["source_id"], {s["source_id"] for s in self.archive["sources"]})
                self.assertEqual(len(item["source_sha256"]), 64)
                self.assertTrue(item["definition"])
                self.assertTrue(item["risks"])

    def test_holdout_and_embedded_archive_warnings_are_preserved(self):
        text = json.dumps(self.archive, ensure_ascii=False).lower()
        self.assertIn("holdout", text)
        self.assertIn("embedded", text)
        self.assertIn("random/null baseline", text)
        self.assertIs(self.archive["binding_policy"]["canonical_method_definition_changes_require_explicit_user_approval"], True)
        self.assertIs(self.archive["binding_policy"]["formulas_execution_enabled"], False)

    def test_no_live_draw_list_was_copied_into_catalogue(self):
        text = ARCHIVE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("20260001|", text)
        self.assertNotIn("20260031|", text)
        self.assertNotIn("20260107|", text)


if __name__ == "__main__":
    unittest.main()
