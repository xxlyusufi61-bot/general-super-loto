import json
import unittest
from pathlib import Path


REGISTRY = Path(__file__).with_name("general_method_registry.json")
EXPECTED_METHODS = {"A->B->C", "A->T", "MOD5->T"}


class MethodRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(REGISTRY.read_text(encoding="utf-8"))

    def test_binding_rules_not_changed(self):
        self.assertIs(self.data["binding_rules_changed"], False)

    def test_only_open_research_family_labels_remain(self):
        self.assertEqual(set(self.data["methods"]), EXPECTED_METHODS)
        for name, method in self.data["methods"].items():
            with self.subTest(method=name):
                self.assertEqual(method["status"], "OPEN_DEFINITION")
                self.assertIsNone(method["canonical_definition"])
                self.assertIs(method["execution_ready"], False)
                self.assertTrue(method["research_scope"].startswith("Research-family label only."))

    def test_no_inherited_formula_results_or_implementation_pointers(self):
        self.assertEqual(self.data["historical_results"], {})
        serialized = json.dumps(self.data, ensure_ascii=False)
        forbidden = (
            "legacy_candidate", "source_artifact", "legacy_sources/",
            "V19_best_stable_ABC_weight", "V0_3_1_crossover_weights",
            "1.171946", "0.106117", "0.102668", "0.10094", "0.099944",
        )
        for marker in forbidden:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, serialized)

    def test_promotion_requirements_preserve_chronology_and_baselines(self):
        text = "\n".join(self.data["promotion_requirements"]).lower()
        self.assertIn("pre-target", text)
        self.assertIn("walk-forward", text)
        self.assertIn("cross-validation", text)
        self.assertIn("null baseline", text)
        self.assertIn("explicit user approval", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
