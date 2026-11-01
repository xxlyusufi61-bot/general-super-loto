import unittest

from telegram_report import format_message


class TelegramReportTests(unittest.TestCase):
    def test_candidate_report_is_turkish_and_includes_experimental_caveat(self):
        report = {
            "forward_walk_forward": {
                "baselines": {
                    "abc_transition_to_c": {
                        "evaluated_draws": 12,
                        "mean_hits_out_of_6": 0.7,
                    }
                }
            }
        }
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
            "regimes": {
                "R60": {
                    "status": "EVALUATED",
                    "pre_target_history_records": 99,
                    "number_pool_size": 60,
                    "random_uniform_expected_hits_out_of_6": 0.6,
                    "forward_walk_forward": {
                        "baselines": {"frequency_top6": {"evaluated_draws": 5, "mean_hits_out_of_6": 0.6}}
                    },
                    "time_ordered_cross_validation": {"baselines": {}},
                },
            },
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


if __name__ == "__main__":
    unittest.main()
