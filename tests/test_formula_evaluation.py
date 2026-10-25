import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from general_formula_evaluation import evaluate_method, run_evaluation
from general_formula_proposals import score_abc_transition, score_a_to_t, score_mod5_to_t


ROOT = Path(__file__).resolve().parents[1]
SCORERS = (score_abc_transition, score_a_to_t, score_mod5_to_t)


def synthetic_rows(count=32):
    states = (
        (1, 11, 21, 31, 41, 51),
        (2, 12, 22, 32, 42, 52),
        (3, 13, 23, 33, 43, 53),
        (4, 14, 24, 34, 44, 54),
    )
    start = date(2025, 1, 1)
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "numbers": list(states[index % len(states)]),
        }
        for index in range(count)
    ]


def write_archive(path, draws):
    path.write_text(json.dumps({"draws": draws}), encoding="utf-8")


class FormulaEvaluationTests(unittest.TestCase):
    def test_each_protocol_is_separate_and_has_a_random_baseline(self):
        rows = synthetic_rows()
        for method in ("A->B->C", "A->T", "MOD5->T"):
            with self.subTest(method=method):
                result = evaluate_method(
                    rows,
                    method,
                    random_repeats=20,
                    bootstrap_replicates=30,
                    seed=17,
                )
                self.assertEqual(
                    set(result),
                    {
                        "minimum_history_draws",
                        "forward_walk_forward",
                        "reverse_retrospective_stress_test",
                        "time_ordered_cross_validation",
                    },
                )
                for protocol in ("forward_walk_forward", "reverse_retrospective_stress_test"):
                    summary = result[protocol]
                    self.assertGreater(summary["eligible_target_draws"], 0)
                    self.assertEqual(
                        summary["random_baseline"]["theoretical_expected_hits_per_random_six_number_draw"],
                        0.6,
                    )
                    self.assertIn("block_bootstrap_95_ci", summary["paired_candidate_minus_random_mean_hits_per_target"])
                cv = result["time_ordered_cross_validation"]
                self.assertGreater(cv["summary"]["eligible_target_draws"], 0)
                self.assertEqual(len(cv["folds"]), 5)
                self.assertEqual(
                    sum(fold["eligible_target_draws"] for fold in cv["folds"]),
                    cv["summary"]["eligible_target_draws"],
                )

    def test_evaluation_does_not_depend_on_current_target_numbers(self):
        historical = synthetic_rows()
        current_target_date = (
            date.fromisoformat(historical[-1]["date"]) + timedelta(days=1)
        ).isoformat()
        target_a = {"date": current_target_date, "numbers": [55, 56, 57, 58, 59, 60]}
        target_b = {"date": current_target_date, "numbers": [2, 4, 6, 8, 10, 12]}

        with tempfile.TemporaryDirectory() as directory:
            path_a = Path(directory) / "archive_a.json"
            path_b = Path(directory) / "archive_b.json"
            write_archive(path_a, historical + [target_a])
            write_archive(path_b, historical + [target_b])

            report_a = run_evaluation(
                path_a,
                random_repeats=5,
                bootstrap_replicates=20,
                seed=42,
            )
            report_b = run_evaluation(
                path_b,
                random_repeats=5,
                bootstrap_replicates=20,
                seed=42,
            )

        self.assertEqual(report_a, report_b)
        self.assertIs(report_a["current_isolated_target"]["target_numbers_used_by_evaluation"], False)
        self.assertIs(report_a["current_isolated_target"]["post_target_records_used"], False)
        self.assertIs(report_a["archive_integrity"]["draw_numbers_in_report"], False)
        self.assertIs(report_a["evaluation_policy"]["canonical_registry_modified"], False)
        self.assertIs(report_a["evaluation_policy"]["live_ai_api_calls"], False)

    def test_report_contains_aggregate_metrics_not_per_draw_predictions(self):
        historical = synthetic_rows()
        current_target_date = (
            date.fromisoformat(historical[-1]["date"]) + timedelta(days=1)
        ).isoformat()
        target = {"date": current_target_date, "numbers": [55, 56, 57, 58, 59, 60]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "archive.json"
            write_archive(path, historical + [target])
            report = run_evaluation(path, random_repeats=3, bootstrap_replicates=10, seed=9)

        serialized = json.dumps(report, sort_keys=True)
        self.assertNotIn('"predicted_numbers"', serialized)
        self.assertNotIn('"actual_numbers"', serialized)
        self.assertNotIn('"target_draw_numbers"', serialized)
        self.assertEqual(report["archive_integrity"]["historical_pre_target_record_count"], len(historical))
        self.assertEqual(report["archive_integrity"]["historical_pool_last_date"], historical[-1]["date"])

    def test_empty_history_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_method([], "A->B->C", random_repeats=1, bootstrap_replicates=0)

    def test_evaluation_workflow_has_no_ai_provider_or_secret(self):
        workflow = (ROOT / ".github" / "workflows" / "formula-candidate-evaluation.yml").read_text(encoding="utf-8")
        self.assertNotIn("NVIDIA_API_KEY", workflow)
        self.assertNotIn("integrate.api.nvidia.com", workflow)
        self.assertNotIn("general_core.py", workflow)
        self.assertIn("general_formula_evaluation.py", workflow)
        self.assertIn("super_loto_archive.json", workflow)


if __name__ == "__main__":
    unittest.main()
