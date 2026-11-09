import copy
import json
import random
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import general_offline_hypothesis_discovery as offline
import general_research_memory as combined_memory


def synthetic_archive(count=120):
    start = date(2020, 8, 2)
    generator = random.Random(20261109)
    rows = []
    for index in range(count):
        day = (start + timedelta(days=index * 3)).isoformat()
        numbers = sorted(generator.sample(range(1, 61), 6))
        rows.append({"date": day, "numbers": numbers})
    return rows


class OfflineHypothesisDiscoveryTests(unittest.TestCase):
    def test_bounded_catalogue_covers_all_open_research_families(self):
        training = [
            {**row, "sequence_index": index}
            for index, row in enumerate(synthetic_archive(50))
        ]
        candidates = offline.generate_hypotheses(training)
        self.assertGreaterEqual(len(candidates), 1)
        self.assertLessEqual(len(candidates), offline.MAX_CANDIDATES)
        self.assertEqual(
            {item["family"] for item in candidates},
            {"A→B→C", "A→T", "MOD5→T"},
        )
        self.assertTrue(all(item["synthetic_tests"]["status"] == "PASS" for item in candidates))
        self.assertTrue(all(item["proposal"]["execution_ready"] is False for item in candidates))
        self.assertTrue(all(
            item["training_signal"]["distinct_score_values"] >= 2
            and item["training_signal"]["score_range"] > 0
            and item["training_signal"]["validation_data_used"] is False
            and item["training_signal"]["target_data_used"] is False
            for item in candidates
        ))

    def test_candidates_are_formal_safe_dsl_and_registry_is_never_promoted(self):
        training = [
            {**row, "sequence_index": index}
            for index, row in enumerate(synthetic_archive(50))
        ]
        for candidate in offline.generate_hypotheses(training):
            checked = offline.engine.validate_execution_spec(candidate["execution_spec"])
            self.assertEqual(checked, candidate["execution_spec"])
            self.assertEqual(
                candidate["proposal"]["formal_execution_spec"],
                candidate["execution_spec"],
            )
        self.assertTrue(all(
            candidate["proposal"]["registry_write_allowed"] is False
            for candidate in offline.generate_hypotheses(training)
        ))

    def test_full_cycle_runs_walk_forward_cv_and_reverse_diagnostic_without_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "archive.json"
            memory_path = root / "offline-memory.json"
            report_path = root / "report.json"
            archive_path.write_text(
                json.dumps(synthetic_archive(), separators=(",", ":")),
                encoding="utf-8",
            )
            result = offline.run_cycle(
                archive_path=archive_path,
                sealed_ledger_path=root / "missing-sealed-ledger.json",
                memory_path=memory_path,
                report_path=report_path,
            )
            self.assertEqual(result["status"], "COMPLETED_EXPLORATORY_ONLY")
            self.assertGreaterEqual(result["candidate_count"], 1)
            self.assertLessEqual(result["candidate_count"], offline.MAX_CANDIDATES)
            self.assertEqual(result["candidate_selection"], "NONE")
            self.assertFalse(result["target_numbers_exposed"])
            self.assertFalse(result["sealed_holdout_used"])
            for candidate in result["candidates"]:
                self.assertTrue(candidate["training_signal"]["training_draw_count"] >= offline.MIN_TRAIN_DRAWS)
                self.assertEqual(candidate["forward_walk_forward"]["validation_draw_count"], 10)
                self.assertEqual(candidate["time_ordered_cross_validation"]["validation_draw_count"], 10)
                self.assertEqual(candidate["time_ordered_cross_validation"]["predictive_claim"], False)
                self.assertFalse(candidate["reverse_retrospective_only"]["predictive_evidence"])
            combined = combined_memory.load_memory(memory_path)
            self.assertEqual(combined["entries"], [])
            self.assertEqual(combined["schema_version"], 3)
            self.assertIn("offline_research", combined)
            memory = offline.load_memory(memory_path)
            self.assertEqual(len(memory["entries"]), 1)
            self.assertEqual(len(memory["consumed_development_windows"]), 1)
            self.assertEqual(len(memory["consumed_development_windows"][0]["dates"]), 10)
            self.assertEqual(
                offline.memory_context(
                    memory_path,
                    direction="forward",
                    target_date=result["target_date"],
                )[0]["candidate_selection"],
                "NONE",
            )
            self.assertEqual(
                offline.memory_context(
                    memory_path,
                    direction="reverse",
                    target_date=result["target_date"],
                ),
                [],
            )
            forward_prompt_memory = combined_memory.direction_context(
                "forward", result["target_date"], memory_path=memory_path
            )
            reverse_prompt_memory = combined_memory.direction_context(
                "reverse", result["target_date"], memory_path=memory_path
            )
            self.assertEqual(len(forward_prompt_memory), 1)
            self.assertEqual(
                forward_prompt_memory[0]["source"],
                "GENERAL/offline-symbolic-discovery",
            )
            self.assertEqual(forward_prompt_memory[0]["candidate_selection"], "NONE")
            self.assertTrue(
                forward_prompt_memory[0]["development_window"]["consumed_for_exploratory_feedback"]
            )
            self.assertEqual(reverse_prompt_memory, [])
            prompt_json = json.dumps(forward_prompt_memory, ensure_ascii=False)
            self.assertNotIn("validation_dates", prompt_json)
            self.assertNotIn("draw_numbers", prompt_json)
            self.assertNotIn("numbers", memory["entries"][0])
            self.assertNotIn("validation_dates", memory["entries"][0])

    def test_same_development_interval_cannot_be_consumed_twice(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive_path = root / "archive.json"
            memory_path = root / "offline-memory.json"
            report_path = root / "report.json"
            archive_path.write_text(json.dumps(synthetic_archive()), encoding="utf-8")
            first = offline.run_cycle(
                archive_path=archive_path,
                sealed_ledger_path=root / "missing-ledger.json",
                memory_path=memory_path,
                report_path=report_path,
            )
            self.assertEqual(first["status"], "COMPLETED_EXPLORATORY_ONLY")
            before = offline.load_memory(memory_path)
            second = offline.run_cycle(
                archive_path=archive_path,
                sealed_ledger_path=root / "missing-ledger.json",
                memory_path=memory_path,
                report_path=report_path,
            )
            self.assertEqual(second["status"], "WAITING_FOR_NEW_DEVELOPMENT_DRAWS")
            self.assertEqual(offline.load_memory(memory_path), before)

    def test_training_prefix_changes_the_training_only_discovery_signal(self):
        variable_rows = [
            {**row, "sequence_index": index}
            for index, row in enumerate(synthetic_archive(50))
        ]
        constant_rows = [
            {
                "date": row["date"],
                "numbers": [1, 2, 3, 4, 5, 6],
                "sequence_index": index,
            }
            for index, row in enumerate(synthetic_archive(50))
        ]
        variable = offline.generate_hypotheses(variable_rows)
        constant = offline.generate_hypotheses(constant_rows)
        variable_exact = next(
            item for item in variable
            if item["template_id"] == "at_exact_historical_frequency"
        )
        constant_exact = next(
            item for item in constant
            if item["template_id"] == "at_exact_historical_frequency"
        )
        self.assertNotEqual(
            variable_exact["training_signal"]["score_range"],
            constant_exact["training_signal"]["score_range"],
        )
        self.assertEqual(
            constant_exact["training_signal"]["validation_data_used"],
            False,
        )
        self.assertEqual(
            constant_exact["training_signal"]["target_data_used"],
            False,
        )

    def test_target_numbers_do_not_change_generated_hypothesis_definitions(self):
        rows = synthetic_archive()
        changed = copy.deepcopy(rows)
        changed[-1]["numbers"] = [2, 4, 6, 8, 10, 12]
        first_training = [
            {**row, "sequence_index": index}
            for index, row in enumerate(rows[30:80])
        ]
        second_training = [
            {**row, "sequence_index": index + 30}
            for index, row in enumerate(changed[30:80])
        ]
        first = offline.generate_hypotheses(first_training)
        second = offline.generate_hypotheses(second_training)
        self.assertEqual(
            [item["execution_spec"] for item in first],
            [item["execution_spec"] for item in second],
        )

    def test_duplicate_json_keys_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "memory.json"
            path.write_text(
                '{"schema_version":1,"schema_version":1,"purpose":"x","entries":[],"consumed_development_windows":[]}',
                encoding="utf-8",
            )
            with self.assertRaises(combined_memory.ResearchMemoryError):
                offline.load_memory(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
