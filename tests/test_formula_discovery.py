import json
import random
import unittest
from datetime import date, timedelta
from pathlib import Path

import general_formula_discovery as discovery


ROOT = Path(__file__).resolve().parents[1]


def random_rows(count, start, universe_size=60, seed=11):
    rng = random.Random(seed)
    output = []
    for index in range(count):
        output.append({
            "date": (start + timedelta(days=index)).isoformat(),
            "numbers": sorted(rng.sample(range(1, universe_size + 1), 6)),
        })
    return output


class OfflineFormulaDiscoveryTests(unittest.TestCase):
    def test_search_space_contains_new_families_not_only_three_legacy_methods(self):
        families = {candidate["family"] for candidate in discovery.CANDIDATES}
        self.assertEqual(
            families,
            {
                "recent_frequency",
                "decay_frequency",
                "last_seen_gap",
                "profile_neighbors",
                "gap_neighbors",
                "position_neighbors",
            },
        )
        self.assertGreater(len(discovery.CANDIDATES), 3)
        self.assertFalse(any(candidate["candidate_id"] in {"A->B->C", "A->T", "MOD5->T"}
                             for candidate in discovery.CANDIDATES))

    def test_each_candidate_produces_a_stable_six_number_ranking(self):
        rows = random_rows(35, date(2022, 1, 1))
        history = [tuple(row["numbers"]) for row in rows[:-1]]
        for candidate in discovery.CANDIDATES:
            with self.subTest(candidate=candidate["candidate_id"]):
                scores = discovery._score(history, candidate, 60)
                ranked = discovery._ranked_top_six(scores)
                self.assertEqual(len(scores), 60)
                self.assertEqual(len(ranked), 6)
                self.assertEqual(len(set(ranked)), 6)
                self.assertTrue(all(1 <= value <= 60 for value in ranked))

    def test_changing_current_target_numbers_does_not_change_report(self):
        rows_a = random_rows(78, date(2022, 1, 1), seed=42)
        rows_b = [dict(row, numbers=list(row["numbers"])) for row in rows_a]
        rows_b[-1]["numbers"] = [2, 4, 6, 8, 10, 12]

        report_a = discovery.build_report(
            rows_a, random_repeats=2, bootstrap_replicates=0, seed=99
        )
        report_b = discovery.build_report(
            rows_b, random_repeats=2, bootstrap_replicates=0, seed=99
        )
        # The execution timestamp should differ; every scientific result and
        # isolation field must remain invariant when only target numbers change.
        report_a.pop("generated_at_utc")
        report_b.pop("generated_at_utc")
        self.assertEqual(report_a, report_b)
        self.assertIs(
            report_a["current_isolated_target"]["target_numbers_used_by_discovery_or_evaluation"],
            False,
        )
        serialized = json.dumps(report_a, sort_keys=True)
        self.assertNotIn('"actual_numbers"', serialized)
        self.assertNotIn('"predicted_numbers"', serialized)
        self.assertNotIn('"target_draw_numbers"', serialized)

    def test_r54_and_r60_are_reported_separately(self):
        old = random_rows(48, date(2020, 6, 15), universe_size=54, seed=7)
        new = random_rows(30, date(2020, 8, 2), universe_size=60, seed=8)
        rows = old + new
        report = discovery.build_report(
            rows, random_repeats=1, bootstrap_replicates=0, seed=5
        )
        self.assertEqual(set(report["regimes"]), {"R54", "R60"})
        self.assertEqual(report["regimes"]["R54"]["universe_size"], 54)
        self.assertEqual(report["regimes"]["R60"]["universe_size"], 60)
        self.assertEqual(report["regimes"]["R54"]["pre_target_draw_count"], 48)
        self.assertEqual(report["regimes"]["R60"]["pre_target_draw_count"], 29)
        self.assertIs(report["research_design"]["r54_and_r60_pooled"], False)

    def test_unsorted_draw_numbers_are_rejected_before_positional_scoring(self):
        rows = random_rows(35, date(2022, 1, 1))
        rows[10]["numbers"] = list(reversed(rows[10]["numbers"]))
        with self.assertRaisesRegex(ValueError, "sorted ascending"):
            discovery.build_report(
                rows, random_repeats=1, bootstrap_replicates=0, seed=7
            )

    def test_r54_record_with_number_over_54_is_rejected(self):
        rows = random_rows(4, date(2020, 7, 1), universe_size=54)
        rows[1]["numbers"] = [1, 8, 16, 29, 43, 55]
        with self.assertRaisesRegex(ValueError, "R54 record contains"):
            discovery.build_report(rows, random_repeats=1, bootstrap_replicates=0)

    def test_valid_target_isolation_metadata_is_reported(self):
        rows = random_rows(30, date(2022, 1, 1))
        # A valid archive always isolates exactly its latest record.
        report = discovery.build_report(rows, random_repeats=1, bootstrap_replicates=0)
        self.assertIs(report["current_isolated_target"]["isolation_pass"], True)
        self.assertIs(report["current_isolated_target"]["present_in_archive"], True)
        self.assertEqual(report["archive_integrity"]["pre_target_draw_count"], len(rows) - 1)

    def test_report_records_reproducible_evaluation_parameters(self):
        rows = random_rows(35, date(2022, 1, 1))
        report = discovery.build_report(
            rows, random_repeats=3, bootstrap_replicates=12, seed=1234
        )
        self.assertTrue(report["generated_at_utc"].endswith("+00:00"))
        self.assertEqual(
            report["evaluation_parameters"],
            {
                "random_predictions_per_target": 3,
                "bootstrap_replicates": 12,
                "bootstrap_block_length": discovery.DEFAULT_BLOCK_LENGTH,
                "seed": 1234,
                "minimum_history_draws": discovery.MIN_HISTORY,
            },
        )

    def test_report_declares_api_free_no_promotion_policy(self):
        rows = random_rows(35, date(2022, 1, 1))
        report = discovery.build_report(rows, random_repeats=1, bootstrap_replicates=0)
        design = report["research_design"]
        self.assertEqual(design["api_calls"], 0)
        self.assertIs(design["live_ai_calls"], False)
        self.assertIs(design["provider_or_paid_service_required"], False)
        self.assertIs(design["canonical_registry_modified"], False)
        self.assertIs(design["automatic_promotion"], False)

    def test_bootstrap_interval_is_unavailable_for_short_test_series(self):
        interval = discovery._block_bootstrap_ci(
            [0.2, -0.1, 0.4, 0.0, 0.1, -0.2, 0.3, 0.1, -0.1, 0.0, 0.2],
            replicates=250,
            block_length=20,
            seed=7,
        )
        self.assertIsNone(interval)

    def test_bootstrap_interval_has_uncertainty_with_sufficient_targets(self):
        values = [(-0.2 if index % 3 == 0 else 0.1) for index in range(100)]
        interval = discovery._block_bootstrap_ci(
            values, replicates=50, block_length=20, seed=7
        )
        self.assertIsNotNone(interval)
        self.assertLessEqual(interval["low"], interval["high"])
        self.assertEqual(interval["replicates"], 50)

    def test_workflow_has_no_provider_key_and_tests_this_engine(self):
        workflow_path = ROOT / ".github" / "workflows" / "formula-discovery.yml"
        workflow = workflow_path.read_text(encoding="utf-8")
        source = (ROOT / "general_formula_discovery.py").read_text(encoding="utf-8")
        self.assertNotIn("NVIDIA_API_KEY", workflow)
        self.assertNotIn("integrate.api.nvidia.com", workflow)
        self.assertNotIn("urllib.request.urlopen", source)
        self.assertIn("general_formula_discovery.py", workflow)
        self.assertIn('"api_calls": 0', source)
        self.assertIn('"live_ai_calls": False', source)


if __name__ == "__main__":
    unittest.main()
