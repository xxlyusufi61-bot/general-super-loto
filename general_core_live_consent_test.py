import os
import unittest
from unittest.mock import patch

import general_core as core
from general_live_consent import CONFIRMATION_PHRASE


class CoreLiveConsentBoundaryTests(unittest.TestCase):
    def assert_no_network_request(self, environment, expected_message):
        with patch.dict(os.environ, environment, clear=True), patch(
            "general_core.urllib.request.urlopen"
        ) as mocked_urlopen:
            with self.assertRaisesRegex(RuntimeError, expected_message):
                core.ask_nvidia([{"role": "user", "content": "offline guard test"}])
            mocked_urlopen.assert_not_called()

    def test_missing_exact_confirmation_blocks_before_network(self):
        self.assert_no_network_request(
            {"NVIDIA_API_KEY": "test-secret"},
            "exact free-only confirmation phrase is missing",
        )

    def test_wrong_or_extended_confirmation_phrase_is_rejected(self):
        self.assert_no_network_request(
            {
                "NVIDIA_API_KEY": "test-secret",
                "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE + " extra",
                "GENERAL_NVIDIA_GUARD_PASSED": "1",
            },
            "exact free-only confirmation phrase is missing",
        )

    def test_missing_global_lock_preflight_blocks_before_network(self):
        self.assert_no_network_request(
            {
                "NVIDIA_API_KEY": "test-secret",
                "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
            },
            "global safety-lock preflight was not confirmed",
        )

    def test_missing_free_use_verification_blocks_before_network(self):
        self.assert_no_network_request(
            {
                "NVIDIA_API_KEY": "test-secret",
                "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
                "GENERAL_NVIDIA_GUARD_PASSED": "1",
            },
            "free-use and no-billing status was not independently verified",
        )

    def test_false_free_use_verification_blocks_before_network(self):
        self.assert_no_network_request(
            {
                "NVIDIA_API_KEY": "test-secret",
                "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
                "GENERAL_NVIDIA_GUARD_PASSED": "1",
                "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "false",
            },
            "free-use and no-billing status was not independently verified",
        )

    def test_missing_api_key_blocks_after_gates_without_network(self):
        self.assert_no_network_request(
            {
                "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
                "GENERAL_NVIDIA_GUARD_PASSED": "1",
                "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            },
            "NVIDIA_API_KEY missing",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
