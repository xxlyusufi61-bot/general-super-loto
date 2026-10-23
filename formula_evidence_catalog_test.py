import json
import unittest
from pathlib import Path


CATALOG = Path(__file__).with_name("formula_evidence_catalog.json")
REGISTRY = Path(__file__).with_name("general_method_registry.json")


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

    def test_missing_legacy_sources_are_not_claimed_recovered(self):
        for filename in (
            "Super_Loto_V8_6_Donusum_Kesif.py",
            "Super_Loto_Sifre_Kesif_Motoru_v1.py",
        ):
            with self.subTest(source=filename):
                self.assertEqual(
                    self.catalog["source_inventory"][filename]["availability"],
                    "NOT_FOUND_IN_CURRENT_MAIN_FILE_TREE",
                )
        self.assertEqual(self.catalog["recovery_status"], "PARTIAL_SOURCE_EVIDENCE_ONLY")

    def test_old_weights_remain_unverified(self):
        for name in ("V19_best_stable_ABC_weight", "V0_3_1_crossover_weights"):
            with self.subTest(result=name):
                self.assertEqual(
                    self.catalog["historical_results"][name]["status"],
                    "UNVERIFIED_CONTEXT_ONLY",
                )

    def test_nested_cross_validation_not_claimed(self):
        self.assertFalse(self.catalog["evaluation_status"]["nested_time_series_cross_validation"] == "IMPLEMENTED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
