import unittest
from pathlib import Path

from general_live_consent import CONFIRMATION_PHRASE, has_explicit_live_consent


EXPECTED_CONFIRMATION_PHRASE = (
    "I_CONFIRM_FREE_ONLY_NO_BILLING_AND_APPROVE_UP_TO_2_NVIDIA_REQUESTS"
)
LEGACY_THREE_REQUEST_PHRASE = (
    "I_CONFIRM_FREE_ONLY_NO_BILLING_AND_APPROVE_UP_TO_3_NVIDIA_REQUESTS"
)
LIVE_WORKFLOW_PATHS = (
    ".github/workflows/general-agent.yml",
    ".github/workflows/open-research.yml",
    ".github/workflows/general-autonomous-research.yml",
    ".github/workflows/nvidia-chat-test.yml",
    ".github/workflows/nvidia-test.yml",
)


class LiveNvidiaConsentTests(unittest.TestCase):
    def test_canonical_consent_phrase_states_the_two_request_cap(self):
        self.assertEqual(CONFIRMATION_PHRASE, EXPECTED_CONFIRMATION_PHRASE)

    def test_only_the_exact_two_request_phrase_is_accepted(self):
        self.assertTrue(has_explicit_live_consent(CONFIRMATION_PHRASE))
        self.assertFalse(has_explicit_live_consent(CONFIRMATION_PHRASE + " extra"))
        self.assertFalse(has_explicit_live_consent(" " + CONFIRMATION_PHRASE))
        self.assertFalse(has_explicit_live_consent(CONFIRMATION_PHRASE + " "))

    def test_legacy_three_request_phrase_is_rejected(self):
        self.assertFalse(has_explicit_live_consent(LEGACY_THREE_REQUEST_PHRASE))

    def test_all_live_workflows_use_the_same_two_request_phrase(self):
        root = Path(__file__).resolve().parent
        for relative_path in LIVE_WORKFLOW_PATHS:
            with self.subTest(workflow=relative_path):
                workflow_path = root / relative_path
                self.assertTrue(workflow_path.is_file(), relative_path + " is missing")
                workflow_text = workflow_path.read_text(encoding="utf-8")
                self.assertIn(CONFIRMATION_PHRASE, workflow_text)
                self.assertNotIn(LEGACY_THREE_REQUEST_PHRASE, workflow_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
