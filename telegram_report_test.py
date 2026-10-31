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

    def test_walkforward_report_states_isolation_and_unimplemented_methods_in_turkish(self):
        report = {
            "current_target_excluded_from_scoring": True,
            "current_target_numbers_used_for_scoring": False,
            "current_target_numbers_exposed": False,
            "pre_target_history_records": 100,
            "regime_counts": {"R54": 30, "R60": 69},
            "regimes_evaluated_separately": True,
            "regimes": {
                "R54": {
                    "status": "INSUFFICIENT_PRETARGET_HISTORY",
                    "pre_target_history_records": 30,
                    "minimum_training_draws": 100,
                },
                "R60": {
                    "status": "EVALUATED",
                    "pre_target_history_records": 69,
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
        self.assertIn("R54 rejimi:", message)
        self.assertIn("Yetersiz hedef-öncesi geçmiş", message)
        self.assertIn("R60 rejimi:", message)
        self.assertIn("R54 ve R60 ayrı değerlendirildi", message)
        self.assertIn("tahmin kanıtı değildir", message)
        self.assertNotIn("Target isolation", message)

    def test_failure_message_is_turkish_and_links_logs(self):
        message = format_message("Güvenlik Testi", "failure", "https://github.com/example/run", "main")
        self.assertIn("Sonuç: BAŞARISIZ", message)
        self.assertIn("Hata ayrıntıları", message)
        self.assertIn("https://github.com/example/run", message)
        self.assertNotIn("Result:", message)


if __name__ == "__main__":
    unittest.main()
