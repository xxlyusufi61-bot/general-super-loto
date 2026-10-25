import unittest

from telegram_report import format_message


class TelegramReportTests(unittest.TestCase):
    def test_candidate_report_includes_aggregate_and_experimental_limit(self):
        report = {
            'forward_walk_forward': {
                'baselines': {
                    'abc_transition_to_c': {
                        'evaluated_draws': 12,
                        'mean_hits_out_of_6': 0.7,
                    }
                }
            }
        }
        message = format_message('Candidate Evaluation', 'success', 'https://github.com/example/run', 'main', 'candidates', report)
        self.assertIn('0.700/6 mean hits', message)
        self.assertIn('Experimental only', message)
        self.assertIn('Reverse-time results are retrospective', message)

    def test_walkforward_report_does_not_claim_unimplemented_methods(self):
        report = {
            'current_target_excluded_from_scoring': True,
            'current_target_numbers_used_for_scoring': False,
            'current_target_numbers_exposed': False,
            'pre_target_history_records': 100,
            'forward_walk_forward': {
                'baselines': {'frequency_top6': {'evaluated_draws': 5, 'mean_hits_out_of_6': 0.6}}
            },
            'time_ordered_cross_validation': {'baselines': {}},
        }
        message = format_message('Walk-Forward', 'success', 'https://github.com/example/run', 'main', 'walkforward', report)
        self.assertIn('Target isolation: PASS', message)
        self.assertIn('A->B->C, A->T and MOD5->T are not implemented', message)
        self.assertIn('Reverse-time results are retrospective', message)

    def test_failure_message_is_minimal_and_links_logs(self):
        message = format_message('Security Test', 'failure', 'https://github.com/example/run', 'main')
        self.assertIn('Result: FAILURE', message)
        self.assertIn('Open the Actions run below', message)
        self.assertIn('https://github.com/example/run', message)


if __name__ == '__main__':
    unittest.main()
