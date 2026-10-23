import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "legacy_sources" / "Super_Loto_Sifre_Kesif_Motoru_v1_2.py"
SPEC = importlib.util.spec_from_file_location("legacy_sifre_motoru_v1_2", SOURCE)
MOTOR = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MOTOR
SPEC.loader.exec_module(MOTOR)


def synthetic_draws(count=45):
    """Deterministic, synthetic six-number rows; never real draw data."""
    return tuple(
        tuple(sorted(((i * 7 + j * 11) % 60) + 1 for j in range(6)))
        for i in range(count)
    )


class LegacySifreMotoruV12AuditTests(unittest.TestCase):
    def test_embedded_archive_is_well_formed_but_historical(self):
        # This is the archive embedded in this recovered source, not canonical data.
        self.assertEqual(len(MOTOR.DRAWS), 263)
        self.assertEqual(len(MOTOR.NUMS), 263)
        ids = [row[0] for row in MOTOR.DRAWS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(sum(str(draw_id).startswith("2025") for draw_id in ids), 156)
        self.assertEqual(sum(str(draw_id).startswith("2026") for draw_id in ids), 107)
        for draw_id, numbers in MOTOR.DRAWS:
            self.assertIsInstance(draw_id, int)
            self.assertIsInstance(numbers, tuple)
            self.assertEqual(len(numbers), 6)
            self.assertEqual(len(set(numbers)), 6)
            self.assertTrue(all(type(x) is int and 1 <= x <= 60 for x in numbers))
        self.assertEqual(MOTOR.NUMS, [numbers for _, numbers in MOTOR.DRAWS])

    def test_prediction_filters_mixed_tuple_and_integer_keys_before_sort(self):
        # Equal-score tuple and integer keys reproduce the historical sort hazard.
        name = "__audit_mixed_type_fixture__"
        old = MOTOR.GENERATORS.get(name)
        MOTOR.GENERATORS[name] = lambda source, train: [
            (42, 100.0),
            ((1, 2, 3, 4, 5, 6), 100.0),
            (41, 90.0),
            (40, 80.0),
            (39, 70.0),
            (38, 60.0),
            (37, 50.0),
        ]
        try:
            rows = synthetic_draws()
            pred = MOTOR.predict([(name, 1.0)], rows[10], rows[:11])
        finally:
            if old is None:
                MOTOR.GENERATORS.pop(name, None)
            else:
                MOTOR.GENERATORS[name] = old

        self.assertEqual(pred, (37, 38, 39, 40, 41, 42))
        self.assertEqual(len(pred), 6)
        self.assertTrue(all(type(x) is int and 1 <= x <= 60 for x in pred))

    def test_evaluator_passes_only_strict_history_prefix_to_predictor(self):
        rows = synthetic_draws(32)
        start, end = 12, 17
        observed = []

        def spy(recipe, source, train):
            observed.append((source, tuple(train)))
            return (1, 2, 3, 4, 5, 6)

        with mock.patch.object(MOTOR, "predict", side_effect=spy):
            result = MOTOR.evaluate_recipe([], rows, start, end)

        self.assertEqual(result["n"], end - start + 1)
        self.assertEqual(len(observed), end - start + 1)
        for offset, target_index in enumerate(range(start, end + 1)):
            source, train = observed[offset]
            self.assertEqual(train, rows[:target_index])
            self.assertEqual(source, rows[target_index - 1])
            self.assertEqual(len(train), target_index)

    def test_evaluator_runs_on_synthetic_six_number_rows(self):
        rows = synthetic_draws(40)
        result = MOTOR.evaluate_recipe([("same", 1.0)], rows, 20, 25)
        self.assertEqual(result["n"], 6)
        self.assertEqual(len(result["details"]), 6)
        self.assertGreaterEqual(result["avg"], 0.0)
        self.assertLessEqual(result["avg"], 6.0)
        for _, hits, prediction, actual in result["details"]:
            self.assertEqual(len(prediction), 6)
            self.assertEqual(len(actual), 6)
            self.assertTrue(all(type(x) is int and 1 <= x <= 60 for x in prediction))
            self.assertGreaterEqual(hits, 0)
            self.assertLessEqual(hits, 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
