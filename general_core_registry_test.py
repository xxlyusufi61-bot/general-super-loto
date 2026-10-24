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



    def test_real_registry_exposes_why_undated_entries_are_withheld(self):
        with patch.object(general_core, "METHOD_REGISTRY", Path(__file__).with_name("general_method_registry.json")):
            context = general_core.load_method_registry_context("2026-10-24")

        audit = context["registry_audit"]
        self.assertEqual(audit["method_count"], 3)
        self.assertEqual(audit["eligible_method_count"], 0)
        self.assertEqual(audit["withheld_method_count"], 3)
        self.assertEqual(audit["historical_result_count"], 2)
        self.assertEqual(audit["eligible_historical_result_count"], 0)
        self.assertEqual(
            set(context["withheld_method_reasons"].values()),
            {"missing_available_from"}
        )
        self.assertEqual(
            set(context["withheld_result_reasons"].values()),
            {"missing_available_from"}
        )

    def test_real_archive_regime_partition_is_explicit_and_disjoint(self):
        import tempfile
        archive_rows = [
            {"date": "2020-07-30", "numbers": [1, 2, 3, 4, 5, 6]},
            {"date": "2020-08-02", "numbers": [7, 8, 9, 10, 11, 12]},
            {"date": "2020-08-05", "numbers": [13, 14, 15, 16, 17, 18]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "archive.json"
            archive_path.write_text(
                json.dumps({"draws": archive_rows}), encoding="utf-8"
            )
            with patch.object(general_core, "ARCHIVE", archive_path):
                data = general_core.prepare_data("2020-08-05")

        regimes = data["training_by_regime"]
        self.assertEqual(data["regime_boundary"], "2020-08-02")
        self.assertEqual(data["regime_counts"], {"R54": 1, "R60": 1})
        self.assertEqual([row["date"] for row in regimes["R54"]], ["2020-07-30"])
        self.assertEqual([row["date"] for row in regimes["R60"]], ["2020-08-02"])
        self.assertFalse(
            set(row["date"] for row in regimes["R54"])
            & set(row["date"] for row in regimes["R60"])
        )
        self.assertTrue(all(
            row["date"] < "2020-08-05"
            for rows in regimes.values() for row in rows
        ))


if __name__ == "__main__":
    unittest.main(verbosity=2)
