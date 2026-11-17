import json
import tempfile
import unittest
from pathlib import Path

from general_sealed_evaluation import (
    SealedEvaluationError,
    _select_one_frozen_candidate,
    _evaluate_full_reverse_history,
    _evaluate_forward_validation_with_details,
    _validate_dsl_v2_directional_context,
    split_archive,
)


def directional_spec(direction, slot):
    return {
        "schema_version": 2,
        "direction": direction,
        "score_expression": {
            "op": "bool_to_number",
            "arg": {
                "op": "contains_candidate",
                "state": {"op": "state_ref", "slot": slot},
            },
        },
    }


def write_json(path: Path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class SealedEvaluationDSLv2Tests(unittest.TestCase):
    def test_sealed_v2_helpers_reject_missing_canonical_history(self):
        from datetime import date, timedelta

        discovery = []
        start = date(2024, 1, 1)
        for index in range(42):
            day = (start + timedelta(days=index)).isoformat()
            base = index * 7 % 60 + 1
            numbers = sorted({((base + offset * 11 - 1) % 60) + 1 for offset in range(6)})
            discovery.append({"date": day, "numbers": numbers})
        validation = [{
            "date": (start + timedelta(days=42)).isoformat(),
            "numbers": sorted({((42 * 7 % 60 + offset * 11) % 60) + 1 for offset in range(6)}),
        }]

        with self.assertRaisesRegex(SealedEvaluationError, "requires canonical archive history"):
            _evaluate_forward_validation_with_details(
                discovery,
                validation,
                hypothesis_id="HYP-FORWARD",
                spec_sha256="synthetic-only",
                execution_spec=directional_spec("forward", "A"),
            )

        with self.assertRaisesRegex(SealedEvaluationError, "requires canonical archive history"):
            _evaluate_full_reverse_history(
                discovery + validation,
                cutoff_date=(start + timedelta(days=44)).isoformat(),
                number_pool_size=60,
                execution_spec=directional_spec("reverse", "C"),
            )

    def _write_documents(self, directory: Path, *, forward_direction="forward"):
        proposals_path = directory / "proposals.json"
        specs_path = directory / "specs.json"
        proposals = {
            "schema_version": 2,
            "status": "VALID_PROPOSALS",
            "proposals": [
                {"hypothesis_id": "HYP-001", "discovery_direction": "forward"},
                {"hypothesis_id": "HYP-002", "discovery_direction": "reverse"},
            ],
        }
        specs = {
            "status": "VALIDATED_SPECS",
            "dsl_validated": True,
            "synthetic_tests_passed": True,
            "specifications": [
                {
                    "hypothesis_id": "HYP-001",
                    "execution_spec": directional_spec(
                        forward_direction, "A" if forward_direction == "forward" else "C"
                    ),
                    "dsl_validated": True,
                    "synthetic_tests_passed": True,
                },
                {
                    "hypothesis_id": "HYP-002",
                    "execution_spec": directional_spec("reverse", "C"),
                    "dsl_validated": True,
                    "synthetic_tests_passed": True,
                },
            ],
        }
        write_json(proposals_path, proposals)
        write_json(specs_path, specs)
        return proposals_path, specs_path

    def test_claim_guard_rejects_consumed_adjacent_context_in_both_directions(self):
        from datetime import date, timedelta

        canonical = []
        start = "2020-08-02"
        for index in range(5):
            day = (date.fromisoformat(start) + timedelta(days=index)).isoformat()
            base = index * 7 % 60 + 1
            numbers = sorted({((base + offset * 11 - 1) % 60) + 1 for offset in range(6)})
            canonical.append({"date": day, "numbers": numbers})

        forward_candidate = {
            "execution_spec_dsl_version": 2,
            "reverse_execution_spec_dsl_version": 1,
        }
        forward_split = {"regimes": {"R60": {
            "discovery_rows": canonical[:3],
            "validation_rows": [canonical[4]],
            "reverse_validation_rows": [],
        }}}
        with self.assertRaisesRegex(SealedEvaluationError, "Forward DSL v2 target"):
            _validate_dsl_v2_directional_context(
                forward_candidate,
                forward_split,
                canonical,
                {canonical[3]["date"]},
            )

        reverse_candidate = {
            "execution_spec_dsl_version": 1,
            "reverse_execution_spec_dsl_version": 2,
        }
        reverse_split = {"regimes": {"R60": {
            "discovery_rows": canonical[2:],
            "validation_rows": [],
            "reverse_validation_rows": [canonical[0]],
        }}}
        with self.assertRaisesRegex(SealedEvaluationError, "Reverse DSL v2 target"):
            _validate_dsl_v2_directional_context(
                reverse_candidate,
                reverse_split,
                canonical,
                {canonical[1]["date"]},
            )

    def test_adjacency_aware_partition_handles_consumed_blocks_at_all_archive_positions(self):
        from datetime import date, timedelta
        start = date(2020, 8, 2)
        canonical = []
        for index in range(120):
            day = (start + timedelta(days=2 * index)).isoformat()
            base = (index * 7) % 60 + 1
            numbers = sorted({
                ((base + offset * 11 - 1) % 60) + 1
                for offset in range(6)
            })
            canonical.append({"date": day, "numbers": numbers})
        target = {
            "date": (start + timedelta(days=242)).isoformat(),
            "numbers": [1, 2, 3, 4, 5, 6],
        }
        rows = canonical + [target]
        consumed_blocks = [
            list(range(0, 5)) + list(range(60, 65)),
            list(range(25, 31)) + list(range(70, 77)),
            list(range(30, 40)) + list(range(115, 120)),
            list(range(0, 5)) + list(range(25, 31)) + list(range(70, 77)) + list(range(115, 120)),
        ]
        with tempfile.TemporaryDirectory() as directory:
            memory_path = Path(directory) / "offline-memory.json"
            for block_indexes in consumed_blocks:
                with self.subTest(consumed=block_indexes):
                    consumed = {canonical[index]["date"] for index in block_indexes}
                    split = split_archive(
                        rows,
                        used_validation_dates=consumed,
                        offline_memory_path=memory_path,
                    )
                    r60 = split["regimes"]["R60"]
                    self.assertEqual(split["partition_version"], 2)
                    self.assertEqual(r60["status"], "READY")
                    self.assertGreaterEqual(r60["discovery_count"], 40)
                    self.assertGreaterEqual(r60["validation_count"], 2)
                    self.assertGreaterEqual(r60["reverse_validation_count"], 2)

                    discovery = r60["discovery_rows"]
                    forward = r60["validation_rows"]
                    reverse = r60["reverse_validation_rows"]
                    roles = [row["date"] for row in discovery + forward + reverse]
                    self.assertEqual(len(roles), len(set(roles)))
                    self.assertFalse(consumed.intersection(roles))
                    self.assertNotIn(target["date"], roles)
                    self.assertLess(max(row["date"] for row in reverse), min(row["date"] for row in discovery))
                    self.assertLess(max(row["date"] for row in discovery), min(row["date"] for row in forward))

                    role_map = {row["date"]: row for row in discovery + forward + reverse}
                    _validate_dsl_v2_directional_context(
                        {"execution_spec_dsl_version": 2, "reverse_execution_spec_dsl_version": 1},
                        split, canonical, consumed,
                    )
                    _validate_dsl_v2_directional_context(
                        {"execution_spec_dsl_version": 1, "reverse_execution_spec_dsl_version": 2},
                        split, canonical, consumed,
                    )
                    for target_row in reverse:
                        later = [row for row in canonical if row["date"] > target_row["date"]][:2]
                        self.assertEqual(len(later), 2)
                        self.assertTrue(all(role_map.get(row["date"]) == row for row in later))
                    for index, target_row in enumerate(forward):
                        earlier = [row for row in canonical if row["date"] < target_row["date"]][-2:]
                        training = discovery + forward[:index]
                        training_map = {row["date"]: row for row in training}
                        self.assertEqual(len(earlier), 2)
                        self.assertTrue(all(training_map.get(row["date"]) == row for row in earlier))

    def test_partition_v1_preserves_legacy_window_selection(self):
        from datetime import date, timedelta
        start = date(2020, 8, 2)
        canonical = []
        for index in range(80):
            day = (start + timedelta(days=2 * index)).isoformat()
            base = (index * 7) % 60 + 1
            numbers = sorted({
                ((base + offset * 11 - 1) % 60) + 1
                for offset in range(6)
            })
            canonical.append({"date": day, "numbers": numbers})
        target = {
            "date": (start + timedelta(days=162)).isoformat(),
            "numbers": [1, 2, 3, 4, 5, 6],
        }
        rows = canonical + [target]
        with tempfile.TemporaryDirectory() as directory:
            memory_path = Path(directory) / "offline-memory.json"
            legacy = split_archive(rows, partition_version=1, offline_memory_path=memory_path)
            current = split_archive(rows, offline_memory_path=memory_path)
        self.assertEqual(legacy["partition_version"], 1)
        self.assertEqual(current["partition_version"], 2)
        for field in ("discovery_rows", "validation_rows", "reverse_validation_rows"):
            self.assertEqual(
                [row["date"] for row in legacy["regimes"]["R60"][field]],
                [row["date"] for row in current["regimes"]["R60"][field]],
            )

    def test_full_reverse_diagnostic_skips_consumed_neighbours_instead_of_bridging(self):
        from datetime import date, timedelta

        canonical = []
        start = date(2020, 8, 2)
        for index in range(6):
            day = (start + timedelta(days=index)).isoformat()
            base = index * 7 % 60 + 1
            numbers = sorted({((base + offset * 11 - 1) % 60) + 1 for offset in range(6)})
            canonical.append({"date": day, "numbers": numbers})
        available = [canonical[0], canonical[2], canonical[3], canonical[4], canonical[5]]
        report = _evaluate_full_reverse_history(
            available,
            cutoff_date="2020-08-09",
            number_pool_size=60,
            execution_spec=directional_spec("reverse", "C"),
            min_train_draws=2,
            canonical_history=canonical,
            excluded_dates={canonical[1]["date"]},
        )
        self.assertEqual(report["status"], "EVALUATED_RETROSPECTIVE_ONLY")
        self.assertTrue(report["canonical_adjacency_enforced"])
        self.assertFalse(report["previously_consumed_rows_used_as_context"])
        self.assertEqual(report["skipped_consumed_context_draw_count"], 1)
        self.assertEqual(report["evaluated_draw_count"], 2)
        self.assertTrue(all(row["test_date"] != canonical[0]["date"] for row in report["per_draw"]))

    def test_sealed_freeze_requires_matching_directional_v2_specs(self):
        with tempfile.TemporaryDirectory() as temp:
            proposals, specs = self._write_documents(Path(temp))
            candidate = _select_one_frozen_candidate(proposals, specs)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["execution_spec_dsl_version"], 2)
        self.assertEqual(candidate["reverse_execution_spec_dsl_version"], 2)
        self.assertEqual(candidate["execution_spec"]["direction"], "forward")
        self.assertEqual(candidate["reverse_execution_spec"]["direction"], "reverse")
        self.assertEqual(candidate["execution_spec"]["score_expression"]["arg"]["state"]["slot"], "A")
        self.assertEqual(candidate["reverse_execution_spec"]["score_expression"]["arg"]["state"]["slot"], "C")
        self.assertTrue(candidate["spec_sha256"])
        self.assertTrue(candidate["reverse_spec_sha256"])

    def test_full_sealed_v2_walk_forward_and_reverse_evaluation_keep_target_hidden(self):
        from datetime import date, timedelta

        rows = []
        start = date(2020, 8, 2)
        for index in range(80):
            day = (start + timedelta(days=2 * index)).isoformat()
            base = (index * 7) % 60 + 1
            numbers = sorted({((base + offset * 11 - 1) % 60) + 1 for offset in range(6)})
            rows.append({"date": day, "numbers": numbers})
        rows.append({
            "date": "2021-01-10",
            "numbers": sorted({((5 + offset * 11 - 1) % 60) + 1 for offset in range(6)}),
        })
        rows.sort(key=lambda row: row["date"])

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            output = root / "report.json"
            write_json(archive, {"draws": rows})
            proposals, specs = self._write_documents(root)
            claim = __import__("general_sealed_evaluation").create_claim(
                archive,
                ledger,
                proposals,
                specs,
                snapshot_directory=root / "snapshots",
            )
            self.assertTrue(claim["claim_created"])
            self.assertEqual(claim["execution_spec_dsl_version"], 2)
            self.assertEqual(claim["reverse_execution_spec_dsl_version"], 2)

            import general_sealed_evaluation as sealed
            result = sealed.evaluate_claim(
                claim["claim_id"], ledger, proposals, specs, output_path=output
            )
            self.assertEqual(result["status"], "EVALUATED_EXPLORATORY_FIXED_SPEC")
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["execution_dsl_versions"], {"forward": 2, "reverse": 2})
            self.assertFalse(report["current_isolated_target_included"])
            self.assertFalse(report["current_target_numbers_exposed"])
            self.assertFalse(report["target_or_future_rows_used"])
            self.assertEqual(report["forward_walk_forward"]["execution_dsl_version"], 2)
            self.assertEqual(report["time_ordered_cross_validation"]["execution_dsl_version"], 2)
            reverse_holdout = report["reverse_discovered_candidate_holdout"]
            self.assertFalse(reverse_holdout["predictive_evidence"])
            self.assertEqual(reverse_holdout["test_draw_count"], 20)
            reverse_full = report["reverse_retrospective_full_history_diagnostic"]
            self.assertFalse(reverse_full["predictive_evidence"])
            self.assertEqual(reverse_full["hypothesis_id"], "HYP-002")
            self.assertEqual(reverse_full["diagnostic_spec_direction"], "reverse")
            self.assertEqual(reverse_full["evaluated_draw_count"], 40)
            self.assertTrue(all(
                row["spec_sha256"] == report["candidate_selection"]["reverse_spec_sha256"]
                for row in reverse_full["per_draw"]
            ))
            self.assertNotIn("reverse_retrospective_stress_test_of_forward_candidate", report)
            self.assertNotIn('"numbers":', json.dumps(report))

    def test_sealed_freeze_rejects_forward_proposal_with_reverse_spec(self):
        with tempfile.TemporaryDirectory() as temp:
            proposals, specs = self._write_documents(Path(temp), forward_direction="reverse")
            with self.assertRaisesRegex(SealedEvaluationError, "does not match"):
                _select_one_frozen_candidate(proposals, specs)

    def test_sealed_freeze_does_not_accept_target_slot_in_directional_spec(self):
        with tempfile.TemporaryDirectory() as temp:
            proposals, specs = self._write_documents(Path(temp))
            payload = json.loads(specs.read_text(encoding="utf-8"))
            payload["specifications"][0]["execution_spec"]["score_expression"]["arg"]["state"]["slot"] = "C"
            write_json(specs, payload)
            with self.assertRaisesRegex(ValueError, "hidden or direction-forbidden"):
                _select_one_frozen_candidate(proposals, specs)


if __name__ == "__main__":
    unittest.main(verbosity=2)
