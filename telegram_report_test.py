import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from telegram_report import format_message, format_offline_research_messages, format_research_messages, read_report
from general_hypothesis_proposals import _render_expression


class TelegramReportTests(unittest.TestCase):
    def test_candidate_report_is_turkish_and_includes_experimental_caveat(self):
        report = {"forward_walk_forward": {"baselines": {
            "abc_transition_to_c": {"evaluated_draws": 12, "mean_hits_out_of_6": 0.7}
        }}}
        message = format_message("Yöntem Adayı Değerlendirmesi", "success", "https://github.com/example/run", "main", "candidates", report)
        self.assertIn("0.700/6 ortalama isabet", message)
        self.assertIn("Yalnızca deneysel", message)
        self.assertIn("Ters kronolojik sonuçlar", message)
        self.assertNotIn("SUCCESS", message)
        self.assertNotIn("Branch:", message)

    def test_walkforward_report_states_only_active_scope(self):
        report = {
            "current_target_excluded_from_scoring": True,
            "current_target_numbers_used_for_scoring": False,
            "current_target_numbers_exposed": False,
            "pre_target_history_records": 100,
            "regime_counts": {"R60": 99},
            "only_active_scope_evaluated": True,
            "regimes": {"R60": {
                "status": "EVALUATED",
                "pre_target_history_records": 99,
                "number_pool_size": 60,
                "random_uniform_expected_hits_out_of_6": 0.6,
                "forward_walk_forward": {"baselines": {
                    "frequency_top6": {"evaluated_draws": 5, "mean_hits_out_of_6": 0.6}
                }},
                "time_ordered_cross_validation": {"baselines": {}},
            }},
        }
        message = format_message("Kronolojik Değerlendirme", "success", "https://github.com/example/run", "main", "walkforward", report)
        self.assertIn("Hedef çekiliş izolasyonu: BAŞARILI", message)
        self.assertIn("uygulanmış değil", message)
        self.assertIn("R60 rejimi:", message)
        self.assertIn("0.600/6 (1–60 sayı havuzu; 36/60)", message)
        self.assertIn("Yalnızca 2 Ağustos 2020 ve sonrası", message)
        self.assertIn("tahmin kanıtı değildir", message)
        self.assertNotIn("Target isolation", message)
        self.assertNotIn("54", message)

    def test_failure_message_is_turkish_and_links_logs(self):
        message = format_message("Güvenlik Testi", "failure", "https://github.com/example/run", "main")
        self.assertIn("Sonuç: BAŞARISIZ", message)
        self.assertIn("Hata ayrıntıları", message)
        self.assertIn("https://github.com/example/run", message)
        self.assertNotIn("Result:", message)

    def _offline_report_fixture(self):
        spec = {
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
        encoded = json.dumps(
            spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        proposal = {
            "hypothesis_id": "HYP-001",
            "discovery_direction": "forward",
            "proposed_family": "A→T",
            "formal_execution_spec": spec,
            "exact_definition": (
                "Formal DSL definition: score(candidate) = "
                + _render_expression(spec["score_expression"]) + "."
            ),
            "status": "PROPOSAL_ONLY",
            "execution_ready": False,
            "registry_write_allowed": False,
        }
        candidate = {
            "hypothesis_id": "HYP-001",
            "family": "A→T",
            "template_id": "at_exact_historical_frequency",
            "proposal": proposal,
            "execution_spec": spec,
            "specification_sha256": hashlib.sha256(encoded).hexdigest(),
            "training_signal": {
                "distinct_score_values": 3,
                "score_range": 8,
                "training_draw_count": 50,
                "selection_source": "training_prefix_only",
                "validation_data_used": False,
                "target_data_used": False,
            },
            "synthetic_tests": {
                "status": "PASS",
                "target_values_used": False,
                "real_archive_used": False,
            },
            "forward_walk_forward": {
                "mean_hits_out_of_6": 0.7,
                "uniform_random_expected_hits_out_of_6": 0.6,
                "validation_draw_count": 10,
                "predictive_claim": False,
            },
            "time_ordered_cross_validation": {
                "mean_hits_out_of_6": 0.65,
                "uniform_random_expected_hits_out_of_6": 0.6,
                "validation_draw_count": 10,
                "folds": 5,
                "predictive_claim": False,
            },
            "reverse_retrospective_only": {
                "status": "EVALUATED_RETROSPECTIVE_ONLY",
                "mean_hits_out_of_6": 0.55,
                "uniform_random_expected_hits_out_of_6": 0.6,
                "evaluated_draw_count": 10,
                "predictive_evidence": False,
            },
        }
        return {
            "schema_version": 1,
            "runner_version": "0.1.0",
            "status": "COMPLETED_EXPLORATORY_ONLY",
            "target_date": "2026-11-08",
            "training_draw_count": 50,
            "development_draw_count": 10,
            "candidate_count": 1,
            "candidate_selection": "NONE",
            "automatic_candidate_selection": False,
            "method_registry_modified": False,
            "target_numbers_exposed": False,
            "target_or_future_rows_used": False,
            "sealed_holdout_used": False,
            "development_interval_is_confirmatory": False,
            "candidates": [candidate],
        }

    def test_offline_research_report_shows_formulas_and_metrics_without_draw_values(self):
        messages = format_offline_research_messages(
            self._offline_report_fixture(),
            "https://github.com/example/run",
            "general-automation-state",
            "Çevrimdışı keşif",
        )
        self.assertEqual(len(messages), 2)
        self.assertIn("ÇEVRİMDIŞI MATEMATİKSEL KEŞİF", messages[0])
        self.assertIn("10 yeni çekiliş", messages[0])
        self.assertIn("A→T", messages[1])
        self.assertIn("HYP-001", messages[1])
        self.assertIn("AGGREGATE_HISTORY_NUMBERS", messages[1])
        self.assertIn("0.700/6", messages[1])
        self.assertIn("0.650/6", messages[1])
        self.assertIn("0.550/6", messages[1])
        self.assertIn("YOK", messages[0])
        self.assertNotIn("1, 2, 3, 4, 5, 6", "\n".join(messages))
        self.assertNotIn("predicted number", "\n".join(messages).lower())

    def test_offline_telegram_report_rejects_target_values_and_noncanonical_candidates(self):
        report = self._offline_report_fixture()
        report["target_numbers_exposed"] = True
        with self.assertRaisesRegex(ValueError, "target_numbers_exposed"):
            format_offline_research_messages(report)
        report = self._offline_report_fixture()
        report["candidates"][0]["proposal"]["execution_ready"] = True
        with self.assertRaisesRegex(ValueError, "non-executable DSL candidate"):
            format_offline_research_messages(report)

    def test_research_reports_are_separate_and_hide_all_draw_values(self):
        spec_a = "a" * 64
        spec_b = "b" * 64
        report = {
            "status": "EVALUATED_EXPLORATORY_FIXED_SPEC",
            "target_date": "2026-10-29",
            "current_isolated_target_included": False,
            "current_target_numbers_exposed": False,
            "target_or_future_rows_used": False,
            "per_draw_target_numbers_reported": False,
            "candidate_rankings_reported": False,
            "validation_start_date": "2021-01-01",
            "validation_end_date": "2021-01-03",
            "candidate_selection": {
                "forward_hypothesis_id": "FWD-001",
                "forward_spec_sha256": spec_a,
                "reverse_hypothesis_id": "REV-001",
                "reverse_spec_sha256": spec_b,
            },
            "forward_walk_forward": {
                "validation_draw_count": 2,
                "mean_hits_out_of_6": 0.7,
                "uniform_random_expected_hits_out_of_6": 0.6,
                "per_draw": [{
                    "test_date": "2021-01-01", "direction": "forward",
                    "hypothesis_id": "FWD-001", "spec_sha256": spec_a,
                    "training_draw_count": 40, "hit_column_positions_1_to_6": [1, 4],
                    "hit_count": 2, "target_numbers_reported": False,
                    "predicted_number_values_reported": False,
                }, {
                    "test_date": "2021-01-03", "direction": "forward",
                    "hypothesis_id": "FWD-001", "spec_sha256": spec_a,
                    "training_draw_count": 41, "hit_column_positions_1_to_6": [],
                    "hit_count": 0, "target_numbers_reported": False,
                    "predicted_number_values_reported": False,
                }],
            },
            "time_ordered_cross_validation": {"folds": 2, "mean_hits_out_of_6": 0.65},
            "reverse_discovered_candidate_holdout": {
                "predictive_evidence": False, "test_draw_count": 2,
                "first_test_date": "2021-01-05", "last_test_date": "2021-01-02",
                "mean_hits_out_of_6": 0.5, "uniform_random_expected_hits_out_of_6": 0.6,
                "per_draw": [{
                    "test_date": "2021-01-05", "direction": "reverse_retrospective",
                    "hypothesis_id": "REV-001", "spec_sha256": spec_b,
                    "later_training_draw_count": 45, "hit_column_positions_1_to_6": [2],
                    "hit_count": 1, "target_numbers_reported": False,
                    "predicted_number_values_reported": False,
                }, {
                    "test_date": "2021-01-02", "direction": "reverse_retrospective",
                    "hypothesis_id": "REV-001", "spec_sha256": spec_b,
                    "later_training_draw_count": 46, "hit_column_positions_1_to_6": [3],
                    "hit_count": 1, "target_numbers_reported": False,
                    "predicted_number_values_reported": False,
                }],
            },
            "reverse_time_ordered_cross_validation": {"folds": 2, "mean_hits_out_of_6": 0.48, "predictive_evidence": False},
        }
        forward, reverse = format_research_messages(
            report, "https://github.com/example/run", "main", "Otonom araştırma"
        )
        self.assertIn("İLERİ YÖN", forward)
        self.assertIn("2021-01-01: 2/6", forward)
        self.assertIn("sütunlar: 1, 4", forward)
        self.assertIn("referans geçmiş: 40", forward)
        self.assertIn("TERS YÖN / RETROSPEKTİF", reverse)
        self.assertIn("yalnızca retrospektif stres testidir", reverse)
        self.assertIn("referans geçmiş: 45", reverse)
        self.assertNotIn("numbers", forward.lower())
        self.assertNotIn("numbers", reverse.lower())
        self.assertNotIn("[1, 2, 3, 4, 5, 6]", forward + reverse)
        self.assertNotIn("Predicted", forward + reverse)

    def test_research_report_fails_closed_if_reverse_is_not_retrospective(self):
        report = {
            "status": "EVALUATED_EXPLORATORY_FIXED_SPEC",
            "target_date": "2026-10-29",
            "current_isolated_target_included": False,
            "current_target_numbers_exposed": False,
            "target_or_future_rows_used": False,
            "per_draw_target_numbers_reported": False,
            "candidate_rankings_reported": False,
            "candidate_selection": {
                "forward_hypothesis_id": "FWD-001",
                "forward_spec_sha256": "a" * 64,
                "reverse_hypothesis_id": "REV-001",
                "reverse_spec_sha256": "b" * 64,
            },
            "forward_walk_forward": {},
            "reverse_discovered_candidate_holdout": {"predictive_evidence": True},
            "time_ordered_cross_validation": {},
            "reverse_time_ordered_cross_validation": {},
        }
        with self.assertRaisesRegex(ValueError, "retrospective-only"):
            format_research_messages(report)

    def test_research_report_rejects_predictive_reverse_cv(self):
        report = self._valid_research_fixture()
        report["reverse_time_ordered_cross_validation"]["predictive_evidence"] = True
        with self.assertRaisesRegex(ValueError, "Reverse cross-validation"):
            format_research_messages(report)

    def test_research_report_rejects_non_finite_hit_metrics(self):
        report = self._valid_research_fixture()
        report["forward_walk_forward"]["mean_hits_out_of_6"] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite hit metric"):
            format_research_messages(report)

    def test_research_report_rejects_target_or_pre_scope_dates(self):
        report = self._valid_research_fixture()
        report["forward_walk_forward"]["per_draw"][0]["test_date"] = report["target_date"]
        with self.assertRaisesRegex(ValueError, "pre-target 6-of-60 scope"):
            format_research_messages(report)

    def test_research_report_rejects_overlapping_or_unordered_holdouts(self):
        report = self._valid_research_fixture()
        report["reverse_discovered_candidate_holdout"]["per_draw"][1]["test_date"] = "2021-01-03"
        with self.assertRaisesRegex(ValueError, "overlapping holdout dates"):
            format_research_messages(report)

    @staticmethod
    def _valid_research_fixture():
        spec_a = "a" * 64
        spec_b = "b" * 64
        return {
            "status": "EVALUATED_EXPLORATORY_FIXED_SPEC",
            "target_date": "2026-10-29",
            "current_isolated_target_included": False,
            "current_target_numbers_exposed": False,
            "target_or_future_rows_used": False,
            "per_draw_target_numbers_reported": False,
            "candidate_rankings_reported": False,
            "validation_start_date": "2021-01-01",
            "validation_end_date": "2021-01-03",
            "candidate_selection": {
                "forward_hypothesis_id": "FWD-001",
                "forward_spec_sha256": spec_a,
                "reverse_hypothesis_id": "REV-001",
                "reverse_spec_sha256": spec_b,
            },
            "forward_walk_forward": {
                "validation_draw_count": 2,
                "mean_hits_out_of_6": 0.7,
                "uniform_random_expected_hits_out_of_6": 0.6,
                "per_draw": [{
                    "test_date": "2021-01-01", "direction": "forward",
                    "hypothesis_id": "FWD-001", "spec_sha256": spec_a,
                    "training_draw_count": 40, "hit_column_positions_1_to_6": [1],
                    "hit_count": 1, "target_numbers_reported": False,
                    "predicted_number_values_reported": False,
                }, {
                    "test_date": "2021-01-03", "direction": "forward",
                    "hypothesis_id": "FWD-001", "spec_sha256": spec_a,
                    "training_draw_count": 41, "hit_column_positions_1_to_6": [],
                    "hit_count": 0, "target_numbers_reported": False,
                    "predicted_number_values_reported": False,
                }],
            },
            "time_ordered_cross_validation": {"folds": 2, "mean_hits_out_of_6": 0.65},
            "reverse_discovered_candidate_holdout": {
                "predictive_evidence": False, "test_draw_count": 2,
                "first_test_date": "2021-02-02", "last_test_date": "2021-02-01",
                "mean_hits_out_of_6": 0.5, "uniform_random_expected_hits_out_of_6": 0.6,
                "per_draw": [{
                    "test_date": "2021-02-02", "direction": "reverse_retrospective",
                    "hypothesis_id": "REV-001", "spec_sha256": spec_b,
                    "later_training_draw_count": 45, "hit_column_positions_1_to_6": [2],
                    "hit_count": 1, "target_numbers_reported": False,
                    "predicted_number_values_reported": False,
                }, {
                    "test_date": "2021-02-01", "direction": "reverse_retrospective",
                    "hypothesis_id": "REV-001", "spec_sha256": spec_b,
                    "later_training_draw_count": 46, "hit_column_positions_1_to_6": [3],
                    "hit_count": 1, "target_numbers_reported": False,
                    "predicted_number_values_reported": False,
                }],
            },
            "reverse_time_ordered_cross_validation": {
                "folds": 2, "mean_hits_out_of_6": 0.48, "predictive_evidence": False,
            },
        }

    def test_pretty_printed_evaluation_json_is_read_as_one_document(self):
        payload = {"status": "EVALUATED_EXPLORATORY_FIXED_SPEC", "detail": {"folds": 5}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self.assertEqual(read_report(path), payload)


if __name__ == "__main__":
    unittest.main()
