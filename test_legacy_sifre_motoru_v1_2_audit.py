import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

LEGACY = load_module("legacy_sifre_motoru_v1_2", ROOT / "legacy_sources" / "Super_Loto_Sifre_Kesif_Motoru_v1_2.py")
MOTOR = load_module("sifre_motoru_v1_2_audit", ROOT / "legacy_sources" / "Super_Loto_Sifre_Kesif_Motoru_v1_2_audit.py")

def synthetic_draws(count=45):
    """Deterministic synthetic rows; no real draw results."""
    return tuple(tuple(sorted(((i * 7 + j * 11) % 60) + 1 for j in range(6))) for i in range(count))

class LegacySifreMotoruV12AuditTests(unittest.TestCase):
    def test_original_source_and_embedded_archive_are_preserved(self):
        self.assertEqual(MOTOR.ARCHIVE, LEGACY.ARCHIVE)
        self.assertEqual(MOTOR.DRAWS, LEGACY.DRAWS)
        self.assertEqual(MOTOR.NUMS, LEGACY.NUMS)
        self.assertEqual(len(LEGACY.DRAWS), 263)
        ids = [row[0] for row in LEGACY.DRAWS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(sum(str(x).startswith("2025") for x in ids), 156)
        self.assertEqual(sum(str(x).startswith("2026") for x in ids), 107)
        for draw_id, numbers in LEGACY.DRAWS:
            self.assertIsInstance(draw_id, int)
            self.assertIsInstance(numbers, tuple)
            self.assertEqual(len(numbers), 6)
            self.assertEqual(len(set(numbers)), 6)
            self.assertTrue(all(type(x) is int and 1 <= x <= 60 for x in numbers))

    def test_mixed_tuple_and_integer_candidate_filter(self):
        name = "__audit_mixed_type_fixture__"
        old = MOTOR.GENERATORS.get(name)
        MOTOR.GENERATORS[name] = lambda source, train: [
            (42, 100.0), ((1, 2, 3, 4, 5, 6), 100.0), (41, 90.0),
            (40, 80.0), (39, 70.0), (38, 60.0), (37, 50.0)]
        try:
            rows = synthetic_draws()
            pred = MOTOR.predict([(name, 1.0)], rows[10], rows[:11])
        finally:
            if old is None: MOTOR.GENERATORS.pop(name, None)
            else: MOTOR.GENERATORS[name] = old
        self.assertEqual(pred, (37, 38, 39, 40, 41, 42))
        self.assertTrue(all(type(x) is int and 1 <= x <= 60 for x in pred))

    def test_evaluator_uses_only_strict_history_prefix(self):
        rows = synthetic_draws(35)
        seen = []
        def spy(recipe, source, train):
            seen.append((source, tuple(train)))
            return (1, 2, 3, 4, 5, 6)
        with mock.patch.object(MOTOR, "predict", side_effect=spy):
            MOTOR.evaluate_recipe([], rows, 12, 12)
        self.assertEqual(seen, [(rows[11], rows[:12])])
        self.assertNotIn(rows[12], seen[0][1])

    def test_detail_uses_actual_draw_id_not_row_number(self):
        rows = synthetic_draws(25)
        ids = [20250001 + i * 13 for i in range(len(rows))]
        result = MOTOR.evaluate_recipe([("same", 1.0)], rows, 10, 12, draw_ids=ids)
        self.assertEqual([x[0] for x in result["details"]], ids[10:13])
        self.assertNotEqual(result["details"][0][0], 11)

    def test_finalists_selected_only_by_development_score(self):
        a, b, c = [("same", 1.0)], [("shift", 1.0)], [("interval", 1.0)]
        results = [(3.0, a, {"avg": 1.0}), (5.0, b, {"avg": 1.7})]
        best_dev = (6.0, c, {"avg": 1.9})
        selected = MOTOR.select_finalists(results, best_dev, limit=3)
        self.assertEqual([r for r, _ in selected], [c, b, a])

    def test_holdout_scores_cannot_reorder_frozen_candidates(self):
        a, b = [("dev_champion", 1.0)], [("lower_dev", 1.0)]
        finalists = [(a, {"avg": 2.0}), (b, {"avg": 1.8})]
        fake_test_results = [{"avg": 0.1, "n": 2}, {"avg": 5.9, "n": 2}]
        with mock.patch.object(MOTOR, "evaluate_recipe", side_effect=fake_test_results):
            evaluated = MOTOR.evaluate_frozen_candidates(finalists, synthetic_draws(20), 10, 11)
        self.assertEqual([x[0] for x in evaluated], [1, 2])
        self.assertEqual([x[1] for x in evaluated], [a, b])
        self.assertLess(evaluated[0][3]["avg"], evaluated[1][3]["avg"])
        self.assertEqual(evaluated[1][0], 2)

    def test_invalid_evaluation_ranges_fail_loudly(self):
        rows = synthetic_draws(20)
        for start, end in [(0, 3), (5, 4), (3, 20)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                MOTOR.evaluate_recipe([], rows, start, end)

    def test_evaluator_runs_on_synthetic_rows(self):
        result = MOTOR.evaluate_recipe([("same", 1.0)], synthetic_draws(40), 20, 25)
        self.assertEqual(result["n"], 6)
        self.assertEqual(len(result["details"]), 6)
        self.assertGreaterEqual(result["avg"], 0.0)
        self.assertLessEqual(result["avg"], 6.0)

if __name__ == "__main__":
    unittest.main(verbosity=2)
