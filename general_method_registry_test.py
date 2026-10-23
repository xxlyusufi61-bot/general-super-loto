import json
import unittest
from pathlib import Path


REGISTRY = Path(__file__).with_name("general_method_registry.json")


class MethodRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(REGISTRY.read_text(encoding="utf-8"))

    def test_binding_rules_not_changed(self):
        self.assertIs(self.data["binding_rules_changed"], False)

    def test_all_three_methods_remain_open_until_approved(self):
        for name in ("A->B->C", "A->T", "MOD5->T"):
            with self.subTest(method=name):
                method = self.data["methods"][name]
                self.assertEqual(method["status"], "OPEN_DEFINITION")
                self.assertIsNone(method["canonical_definition"])
                self.assertIs(method["execution_ready"], False)

    def test_legacy_code_not_mislabeled_canonical(self):
        for name in ("A->B->C", "A->T", "MOD5->T"):
            with self.subTest(method=name):
                candidate = self.data["methods"][name]["legacy_candidate"]
                self.assertEqual(candidate["classification"], "LEGACY_CANDIDATE_NOT_CANONICAL")
                self.assertTrue(candidate["source_artifact"])

    def test_old_weights_are_unverified_context_only(self):
        hist = self.data["historical_results"]
        self.assertEqual(hist["V19_best_stable_ABC_weight"]["status"], "UNVERIFIED_CONTEXT_ONLY")
        self.assertEqual(hist["V0_3_1_crossover_weights"]["status"], "UNVERIFIED_CONTEXT_ONLY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
