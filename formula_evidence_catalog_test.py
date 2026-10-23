import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "formula_evidence_catalog.json"
REGISTRY = ROOT / "general_method_registry.json"


class FormulaEvidenceCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
        cls.registry = json.loads(REGISTRY.read_text(encoding="utf-8"))

    def test_catalog_does_not_change_binding_rules(self):
        self.assertIs(self.catalog["binding_rules_changed"], False)
        self.assertIs(self.registry["binding_rules_changed"], False)

    def test_canonical_methods_remain_open(self):
        for name in ("A->B->C", "A->T", "MOD5->T"):
            with self.subTest(method=name):
                self.assertEqual(self.catalog["methods"][name]["status"], "OPEN_DEFINITION")
                self.assertEqual(self.registry["methods"][name]["status"], "OPEN_DEFINITION")
                self.assertIsNone(self.registry["methods"][name]["canonical_definition"])
                self.assertIs(self.registry["methods"][name]["execution_ready"], False)

    def test_candidate_source_and_version_are_explicit(self):
        for name in ("A->B->C", "A->T", "MOD5->T"):
            with self.subTest(method=name):
                candidate = self.catalog["methods"][name]["current_candidate"]
                self.assertEqual(candidate["source_file"], "general_method_candidates.py")
                self.assertEqual(candidate["source_version"], "0.1.0")
                self.assertTrue(candidate["function"])
                self.assertTrue(candidate["definition"])
                self.assertIn("not", candidate["known_limit"].lower())

    def test_recovered_legacy_sources_exist_and_remain_untrusted(self):
        expected = {
            "Super_Loto_V8_6_Donusum_Kesif.py":
                "legacy_sources/Super_Loto_V8_6_Donusum_Kesif.py",
            "Super_Loto_Sifre_Kesif_Motoru_v1.py":
                "legacy_sources/Super_Loto_Sifre_Kesif_Motoru_v1.py",
            "Super_Loto_NEAR_STATE_Yapi_Dogrulama_Motoru_v5_0.py":
                "legacy_sources/Super_Loto_NEAR_STATE_Yapi_Dogrulama_Motoru_v5_0.py",
        }
        self.assertEqual(
            self.catalog["recovery_status"],
            "LEGACY_SOURCE_FILES_RECOVERED_PENDING_AUDIT"
        )
        for filename, relative_path in expected.items():
            with self.subTest(source=filename):
                entry = self.catalog["source_inventory"][filename]
                self.assertEqual(entry["availability"], "RECOVERED_FROM_CHATGPT_LIBRARY")
                self.assertEqual(entry["preserved_path"], relative_path)
                self.assertTrue((ROOT / relative_path).is_file())
                self.assertIn("AUDIT_REQUIRED", entry["recovery_status"])

    def test_known_v86_leakage_is_explicitly_blocked(self):
        entry = self.catalog["source_inventory"]["Super_Loto_V8_6_Donusum_Kesif.py"]
        self.assertIn("target-B leakage", " ".join(entry["known_defects"]))
        self.assertIn("Do not run", entry["run_policy"])

    def test_old_weights_remain_unverified(self):
        for name in ("V19_best_stable_ABC_weight", "V0_3_1_crossover_weights"):
            with self.subTest(result=name):
                self.assertEqual(
                    self.catalog["historical_results"][name]["status"],
                    "UNVERIFIED_CONTEXT_ONLY",
                )

    def test_nested_cross_validation_not_claimed(self):
        self.assertNotEqual(
            self.catalog["evaluation_status"]["nested_time_series_cross_validation"],
            "IMPLEMENTED"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
