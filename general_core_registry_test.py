import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import general_core


class MethodRegistryContextTests(unittest.TestCase):
    def test_runtime_context_contains_only_open_families_and_no_history(self):
        context = general_core.load_method_registry_context("2026-10-27")
        self.assertEqual(
            context["registry_mode"],
            "fresh_start_no_inherited_formula_history",
        )
        self.assertEqual(set(context["methods"]), {"A->B->C", "A->T", "MOD5->T"})
        self.assertEqual(context["historical_results"], {})
        self.assertEqual(context["registry_audit"]["historical_result_count"], 0)
        self.assertEqual(context["registry_audit"]["inherited_candidate_count"], 0)
        serialized = json.dumps(context, ensure_ascii=False)
        for marker in ("legacy_candidate", "source_artifact", "legacy_sources/"):
            with self.subTest(marker=marker):
                self.assertNotIn(marker, serialized)

    def test_missing_registry_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            with patch.object(general_core, "METHOD_REGISTRY", missing):
                with self.assertRaisesRegex(RuntimeError, "Method registry missing"):
                    general_core.load_method_registry_context()

    def test_nonempty_historical_results_fail_closed(self):
        base = json.loads(
            Path(__file__).with_name("general_method_registry.json").read_text(encoding="utf-8")
        )
        base["historical_results"] = {"old_result": {"value": 0.99}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "general_method_registry.json"
            path.write_text(json.dumps(base), encoding="utf-8")
            with patch.object(general_core, "METHOD_REGISTRY", path):
                with self.assertRaisesRegex(RuntimeError, "contains inherited history"):
                    general_core.load_method_registry_context()

    def test_legacy_candidate_pointer_fails_closed(self):
        base = json.loads(
            Path(__file__).with_name("general_method_registry.json").read_text(encoding="utf-8")
        )
        base["methods"]["A->B->C"]["legacy_candidate"] = {"source_artifact": "old.py"}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "general_method_registry.json"
            path.write_text(json.dumps(base), encoding="utf-8")
            with patch.object(general_core, "METHOD_REGISTRY", path):
                with self.assertRaisesRegex(RuntimeError, "contains an inherited or invalid method"):
                    general_core.load_method_registry_context()

    def test_real_archive_regime_partition_is_explicit_and_disjoint(self):
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
        self.assertEqual(
            [row["date"] for row in regimes["R54"]], ["2020-07-30"]
        )
        self.assertEqual(
            [row["date"] for row in regimes["R60"]], ["2020-08-02"]
        )
        self.assertFalse(
            set(row["date"] for row in regimes["R54"])
            & set(row["date"] for row in regimes["R60"])
        )
        self.assertTrue(
            all(
                row["date"] < "2020-08-05"
                for rows in regimes.values()
                for row in rows
            )
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
