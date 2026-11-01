import json
import tempfile
import unittest
from pathlib import Path

from telegram_report import format_message, format_research_messages, read_report


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
            "validation_end_date": "2021-02-01",
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
                "first_test_date": "2021-01-05", "last_test_date": "2021-01-03",
                "mean_hits_out_of_6": 0.5, "uniform_random_expected_hits_out_of_6": 0.6,
                "per_draw": [{
                    "test_date": "2021-01-05", "direction": "reverse_retrospective",
                    "hypothesis_id": "REV-001", "spec_sha256": spec_b,
                    "later_training_draw_count": 45, "hit_column_positions_1_to_6": [2],
                    "hit_count": 1, "target_numbers_reported": False,
                    "predicted_number_values_reported": False,
                }, {
                    "test_date": "2021-01-03", "direction": "reverse_retrospective",
                    "hypothesis_id": "REV-001", "spec_sha256": spec_b,
                    "later_training_draw_count": 46, "hit_column_positions_1_to_6": [3],
                    "hit_count": 1, "target_numbers_reported": False,
                    "predicted_number_values_reported": False,
                }],
            },
            "reverse_time_ordered_cross_validation": {"folds": 2, "mean_hits_out_of_6": 0.48},
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
            "candidate_selection": {},
            "forward_walk_forward": {},
            "reverse_discovered_candidate_holdout": {"predictive_evidence": True},
            "time_ordered_cross_validation": {},
            "reverse_time_ordered_cross_validation": {},
        }
        with self.assertRaisesRegex(ValueError, "retrospective-only"):
            format_research_messages(report)

    def test_pretty_printed_evaluation_json_is_read_as_one_document(self):
        payload = {"status": "EVALUATED_EXPLORATORY_FIXED_SPEC", "detail": {"folds": 5}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            self.assertEqual(read_report(path), payload)


if __name__ == "__main__":
    unittest.main()
