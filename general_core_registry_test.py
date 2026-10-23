import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import general_core


class MethodRegistryContextTests(unittest.TestCase):
    def write_registry(self, directory, payload):
        path = Path(directory) / "general_method_registry.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def sample_registry(self):
        return {
            "schema_version": 1,
            "project": "GENERAL Super Loto",
            "binding_rules_changed": False,
            "methods": {
                "old_method": {
                    "available_from": "2020-01-01",
                    "status": "OPEN_DEFINITION",
                    "canonical_definition": None
                },
                "undated_method": {
                    "status": "OPEN_DEFINITION",
                    "canonical_definition": None
                },
                "same_day_method": {
                    "available_from": "2026-09-20",
                    "status": "OPEN_DEFINITION",
                    "canonical_definition": None
                }
            },
            "historical_results": {
                "old_result": {"available_from": "2020-01-01", "status": "UNVERIFIED"},
                "undated_result": {"status": "UNVERIFIED"}
            },
            "promotion_requirements": ["Do not promote without approval."]
        }

    def test_target_context_excludes_undated_and_same_day_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_registry(tmp, self.sample_registry())
            with patch.object(general_core, "METHOD_REGISTRY", path):
                context = general_core.load_method_registry_context("2026-09-20")

        self.assertEqual(list(context["methods"]), ["old_method"])
        self.assertEqual(list(context["historical_results"]), ["old_result"])
        self.assertEqual(
            set(context["withheld_post_target_or_undated_methods"]),
            {"undated_method", "same_day_method"}
        )
        self.assertEqual(
            context["withheld_post_target_or_undated_results"],
            ["undated_result"]
        )
        self.assertEqual(context["registry_mode"], "target_date_filtered")

    def test_system_context_can_inspect_full_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_registry(tmp, self.sample_registry())
            with patch.object(general_core, "METHOD_REGISTRY", path):
                context = general_core.load_method_registry_context()

        self.assertEqual(len(context["methods"]), 3)
        self.assertEqual(len(context["historical_results"]), 2)

    def test_missing_registry_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            with patch.object(general_core, "METHOD_REGISTRY", missing):
                with self.assertRaisesRegex(RuntimeError, "Method registry missing"):
                    general_core.load_method_registry_context()

    def test_invalid_registry_schema_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write_registry(tmp, {"schema_version": 99})
            with patch.object(general_core, "METHOD_REGISTRY", path):
                with self.assertRaisesRegex(RuntimeError, "schema invalid"):
                    general_core.load_method_registry_context()


if __name__ == "__main__":
    unittest.main(verbosity=2)
