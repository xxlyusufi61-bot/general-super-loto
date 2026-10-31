import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import general_sealed_evaluation as sealed


def make_rows():
    rows = []
    r54_start = date(2020, 1, 2)
    for index in range(31):
        day = (r54_start + timedelta(days=6 * index)).isoformat()
        base = (index * 7) % 54 + 1
        numbers = sorted({((base + offset * 11 - 1) % 54) + 1 for offset in range(6)})
        rows.append({"date": day, "numbers": numbers})
    r60_start = date(2020, 8, 2)
    for index in range(80):
        day = (r60_start + timedelta(days=2 * index)).isoformat()
        base = (index * 7) % 60 + 1
        numbers = sorted({((base + offset * 11 - 1) % 60) + 1 for offset in range(6)})
        rows.append({"date": day, "numbers": numbers})
    target_numbers = sorted({((5 + offset * 11 - 1) % 60) + 1 for offset in range(6)})
    rows.append({"date": "2021-01-10", "numbers": target_numbers})
    rows.sort(key=lambda row: row["date"])
    return rows


def count_candidate_matches_spec():
    return {
        "schema_version": 1,
        "score_expression": {
            "op": "aggregate_history_numbers",
            "aggregate": "count",
            "expression": {
                "op": "eq",
                "left": {"op": "var", "name": "value"},
                "right": {"op": "var", "name": "candidate"},
            },
        },
    }


def write_candidate_documents(directory, *, specification_count=2):
    proposal_path = directory / "proposals.json"
    specs_path = directory / "specs.json"
    proposal_path.write_text(json.dumps({
        "schema_version": 1,
        "status": "VALID_PROPOSALS",
        "proposals": [
            {"hypothesis_id": "HYP-001", "discovery_direction": "forward"},
            {"hypothesis_id": "HYP-002", "discovery_direction": "reverse"},
        ],
    }), encoding="utf-8")
    specifications = [
        {
            "hypothesis_id": f"HYP-{index + 1:03d}",
            "execution_spec": count_candidate_matches_spec(),
            "dsl_validated": True,
            "synthetic_tests_passed": True,
        }
        for index in range(specification_count)
    ]
    # HYP-001 is the forward candidate; HYP-002 is the separate reverse candidate.
    specs_path.write_text(json.dumps({
        "schema_version": 1,
        "status": "VALIDATED_SPECS",
        "dsl_validated": True,
        "synthetic_tests_passed": True,
        "specifications": specifications,
    }), encoding="utf-8")
    return proposal_path, specs_path


def assert_no_draw_number_sets(test_case, value):
    if isinstance(value, dict):
        test_case.assertNotIn("numbers", value)
        for nested in value.values():
            assert_no_draw_number_sets(test_case, nested)
    elif isinstance(value, list):
        for nested in value:
            assert_no_draw_number_sets(test_case, nested)


class SealedEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive_path = self.root / "archive.json"
        self.rows = make_rows()
        self.archive_path.write_text(json.dumps({"draws": self.rows}, separators=(",", ":")), encoding="utf-8")
        self.ledger_path = self.root / "ledger.json"
        self.snapshot_directory = self.root / "snapshots"

    def tearDown(self):
        self.temp.cleanup()

    def test_split_is_disjoint_and_keeps_latest_target_out(self):
        archive = sealed.load_archive(self.archive_path)
        split = sealed.split_archive(archive["rows"], archive_sha256=archive["archive_sha256"])
        r60 = split["regimes"]["R60"]
        self.assertEqual(split["target_date"], "2021-01-10")
        self.assertEqual(r60["discovery_count"], 40)
        self.assertEqual(r60["validation_count"], 20)
        self.assertEqual(r60["reverse_validation_count"], 20)
        discovery_dates = {row["date"] for row in r60["discovery_rows"]}
        validation_dates = {row["date"] for row in r60["validation_rows"]}
        reverse_dates = {row["date"] for row in r60["reverse_validation_rows"]}
        self.assertFalse(discovery_dates.intersection(validation_dates))
        self.assertFalse(discovery_dates.intersection(reverse_dates))
        self.assertFalse(validation_dates.intersection(reverse_dates))
        self.assertLess(max(reverse_dates), min(discovery_dates))
        self.assertLess(max(discovery_dates), min(validation_dates))
        self.assertNotIn(split["target_date"], discovery_dates)
        self.assertNotIn(split["target_date"], validation_dates)
        self.assertNotIn(split["target_date"], reverse_dates)
        self.assertNotIn("numbers", split)

    def test_split_rejects_unsorted_number_sets_even_in_isolated_target(self):
        rows = make_rows()
        rows[-1]["numbers"] = list(reversed(rows[-1]["numbers"]))
        with self.assertRaises(sealed.SealedEvaluationError):
            sealed.split_archive(rows)

    def test_r54_remains_unevaluated_instead_of_lowering_minimum(self):
        archive = sealed.load_archive(self.archive_path)
        split = sealed.split_archive(archive["rows"], archive_sha256=archive["archive_sha256"])
        r54 = split["regimes"]["R54"]
        self.assertEqual(r54["pre_target_draw_count"], 31)
        self.assertEqual(r54["discovery_count"], 31)
        self.assertEqual(r54["validation_count"], 0)
        self.assertEqual(r54["status"], "NOT_EVALUABLE_INSUFFICIENT_HISTORY")
        self.assertIn("minimum was not lowered", r54["reason"])

    def test_r54_stays_discovery_only_even_if_history_grows(self):
        rows = make_rows()
        additions = []
        for index in range(11):
            day = (date(2020, 7, 1) + timedelta(days=index)).isoformat()
            base = (index * 5) % 54 + 1
            numbers = sorted({((base + offset * 9 - 1) % 54) + 1 for offset in range(6)})
            additions.append({"date": day, "numbers": numbers})
        rows.extend(additions)
        rows.sort(key=lambda row: row["date"])
        split = sealed.split_archive(rows)
        r54 = split["regimes"]["R54"]
        self.assertEqual(r54["pre_target_draw_count"], 42)
        self.assertEqual(r54["discovery_count"], 42)
        self.assertEqual(r54["validation_count"], 0)
        self.assertEqual(r54["status"], "DISCOVERY_ONLY_NOT_EVALUATED")
        self.assertIn("not enabled in this version", r54["reason"])

    def test_public_plan_contains_metadata_not_draw_values(self):
        archive = sealed.load_archive(self.archive_path)
        split = sealed.split_archive(archive["rows"], archive_sha256=archive["archive_sha256"])
        plan = sealed.public_plan(split)
        assert_no_draw_number_sets(self, plan)
        self.assertFalse(plan["target_numbers_exposed"])
        self.assertFalse(plan["target_in_discovery"])
        self.assertFalse(plan["target_in_validation"])
        self.assertEqual(plan["regimes"]["R60"]["validation_count"], 20)
        self.assertEqual(plan["regimes"]["R60"]["reverse_validation_count"], 20)

    def test_preflight_is_offline_and_ready_before_model_request(self):
        result = sealed.preflight(self.archive_path, self.ledger_path)
        self.assertEqual(result["status"], "READY")
        self.assertFalse(result["hosted_model_request_performed"])
        self.assertFalse(result["real_archive_evaluation_performed"])
        self.assertEqual(result["regimes"]["R54"]["status"], "NOT_EVALUABLE_INSUFFICIENT_HISTORY")

    def test_claim_persists_snapshot_and_prevents_overlap_reuse(self):
        proposals, specs = write_candidate_documents(self.root)
        result = sealed.create_claim(
            self.archive_path,
            self.ledger_path,
            proposals,
            specs,
            snapshot_directory=self.snapshot_directory,
        )
        self.assertTrue(result["claim_created"])
        self.assertTrue((self.root / result["archive_snapshot_path"]).is_file())
        with self.ledger_path.open(encoding="utf-8") as stream:
            ledger = json.load(stream)
        self.assertEqual(len(ledger["entries"]), 1)
        self.assertEqual(ledger["entries"][0]["state"], "CLAIMED")
        self.assertEqual(len(ledger["entries"][0]["validation_dates"]), 20)
        self.assertEqual(len(ledger["entries"][0]["reverse_validation_dates"]), 20)
        self.assertFalse(
            set(ledger["entries"][0]["validation_dates"]).intersection(
                ledger["entries"][0]["reverse_validation_dates"]
            )
        )
        self.assertEqual(ledger["entries"][0]["reverse_hypothesis_id"], "HYP-002")
        assert_no_draw_number_sets(self, ledger)
        with self.assertRaises(sealed.SealedEvaluationError):
            sealed.preflight(self.archive_path, self.ledger_path)

    def test_more_than_two_or_unmatched_directional_specs_cannot_claim(self):
        proposals, specs = write_candidate_documents(self.root, specification_count=3)
        result = sealed.create_claim(
            self.archive_path,
            self.ledger_path,
            proposals,
            specs,
            snapshot_directory=self.snapshot_directory,
        )
        self.assertFalse(result["claim_created"])
        self.assertEqual(result["status"], "SKIPPED_NO_SINGLE_FROZEN_CANDIDATE")
        self.assertFalse(self.ledger_path.exists())
        self.assertFalse(self.snapshot_directory.exists())

    def test_evaluation_is_aggregate_only_and_claim_cannot_be_reused(self):
        proposals, specs = write_candidate_documents(self.root)
        claim = sealed.create_claim(
            self.archive_path,
            self.ledger_path,
            proposals,
            specs,
            snapshot_directory=self.snapshot_directory,
        )
        output_path = self.root / "evaluation.json"
        report = sealed.evaluate_claim(
            claim["claim_id"], self.ledger_path, proposals, specs,
            output_path=output_path,
        )
        self.assertEqual(report["status"], "EVALUATED_EXPLORATORY_FIXED_SPEC")
        self.assertFalse(report["draw_numbers_reported"])
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        assert_no_draw_number_sets(self, payload)
        self.assertFalse(payload["current_isolated_target_included"])
        self.assertFalse(payload["current_target_numbers_exposed"])
        self.assertEqual(payload["forward_walk_forward"]["validation_draw_count"], 20)
        self.assertEqual(payload["reverse_discovered_candidate_holdout"]["test_draw_count"], 20)
        self.assertFalse(payload["reverse_discovered_candidate_holdout"]["predictive_evidence"])
        self.assertEqual(payload["reverse_discovered_candidate_holdout"]["evaluation_order"], "newest_to_oldest")
        self.assertEqual(len(payload["reverse_discovered_candidate_holdout"]["per_draw"]), 20)
        for draw in payload["reverse_discovered_candidate_holdout"]["per_draw"]:
            self.assertTrue(draw["test_date"] <= payload["reverse_discovered_candidate_holdout"]["first_test_date"])
            self.assertEqual(draw["hit_count"], len(draw["hit_column_positions_1_to_6"]))
            self.assertTrue(all(1 <= position <= 6 for position in draw["hit_column_positions_1_to_6"]))
            self.assertFalse(draw["target_numbers_reported"])
            self.assertFalse(draw["predicted_number_values_reported"])
        self.assertEqual(payload["forward_walk_forward"]["uniform_random_expected_hits_out_of_6"], 0.6)
        self.assertEqual(payload["candidate_selection"]["evaluated_directional_specification_count"], 2)
        self.assertEqual(payload["candidate_selection"]["forward_hypothesis_id"], "HYP-001")
        self.assertEqual(payload["candidate_selection"]["reverse_hypothesis_id"], "HYP-002")
        self.assertFalse(payload["time_ordered_cross_validation"]["automatic_candidate_selection"])
        self.assertFalse(payload["reverse_retrospective_stress_test"]["predictive_evidence"])
        self.assertEqual(payload["reverse_retrospective_stress_test"]["evaluated_draw_count"], 40)
        self.assertEqual(payload["reverse_retrospective_stress_test"]["last_test_date"], "2020-08-02")
        self.assertEqual(sum(payload["reverse_retrospective_stress_test"]["hit_distribution"].values()), 40)
        self.assertIn("correlated summaries", " ".join(payload["interpretation_limits"]))
        with self.ledger_path.open(encoding="utf-8") as stream:
            ledger = json.load(stream)
        self.assertEqual(ledger["entries"][0]["state"], "EVALUATED")
        self.assertIn("evaluation_report_sha256", ledger["entries"][0])
        with self.assertRaises(sealed.SealedEvaluationError):
            sealed.evaluate_claim(
                claim["claim_id"], self.ledger_path, proposals, specs,
                output_path=output_path,
            )

    def test_candidate_cannot_change_after_claim(self):
        proposals, specs = write_candidate_documents(self.root)
        claim = sealed.create_claim(
            self.archive_path,
            self.ledger_path,
            proposals,
            specs,
            snapshot_directory=self.snapshot_directory,
        )
        changed = json.loads(specs.read_text(encoding="utf-8"))
        changed["specifications"][0]["execution_spec"] = {
            "schema_version": 1,
            "score_expression": {
                "op": "aggregate_history_numbers",
                "aggregate": "sum",
                "expression": {"op": "var", "name": "value"},
            },
        }
        specs.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaises(sealed.SealedEvaluationError):
            sealed.evaluate_claim(
                claim["claim_id"], self.ledger_path, proposals, specs,
                output_path=self.root / "evaluation.json",
            )


if __name__ == "__main__":
    unittest.main()
