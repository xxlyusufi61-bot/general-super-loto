import unittest
from pathlib import Path

from general_live_consent import (
    CONFIRMATION_PHRASE,
    COORDINATOR_CONFIRMATION_PHRASE,
    SINGLE_REQUEST_CONFIRMATION_PHRASE,
    has_explicit_live_consent,
    has_explicit_coordinator_live_consent,
    has_explicit_single_request_consent,
)


EXPECTED_CONFIRMATION_PHRASE = (
    "I_CONFIRM_FREE_ONLY_NO_BILLING_AND_APPROVE_UP_TO_2_NVIDIA_REQUESTS"
)
EXPECTED_COORDINATOR_PHRASE = (
    "I_CONFIRM_FREE_ONLY_NO_BILLING_AND_APPROVE_UP_TO_3_NVIDIA_REQUESTS"
)
EXPECTED_SINGLE_REQUEST_PHRASE = "I_APPROVE_ONE_NORMAL_RESEARCH_REQUEST_8192_TOKENS"
LIVE_WORKFLOW_PATHS = (
    ".github/workflows/general-agent.yml",
    ".github/workflows/open-research.yml",
    ".github/workflows/general-autonomous-research.yml",
    ".github/workflows/nvidia-chat-test.yml",
    ".github/workflows/nvidia-test.yml",
)
COORDINATOR_WORKFLOW_PATH = ".github/workflows/general-coordinator-execute.yml"


class LiveNvidiaConsentTests(unittest.TestCase):
    def test_canonical_phrases_match_their_operation_caps(self):
        self.assertEqual(CONFIRMATION_PHRASE, EXPECTED_CONFIRMATION_PHRASE)
        self.assertEqual(COORDINATOR_CONFIRMATION_PHRASE, EXPECTED_COORDINATOR_PHRASE)
        self.assertNotEqual(CONFIRMATION_PHRASE, COORDINATOR_CONFIRMATION_PHRASE)

    def test_only_exact_operation_phrases_are_accepted(self):
        self.assertTrue(has_explicit_live_consent(CONFIRMATION_PHRASE))
        self.assertTrue(has_explicit_live_consent(COORDINATOR_CONFIRMATION_PHRASE))
        self.assertFalse(has_explicit_live_consent(CONFIRMATION_PHRASE + " extra"))
        self.assertFalse(has_explicit_live_consent(" " + CONFIRMATION_PHRASE))
        self.assertFalse(has_explicit_live_consent(CONFIRMATION_PHRASE + " "))
        self.assertFalse(has_explicit_coordinator_live_consent(CONFIRMATION_PHRASE))
        self.assertTrue(has_explicit_coordinator_live_consent(COORDINATOR_CONFIRMATION_PHRASE))
        self.assertFalse(has_explicit_coordinator_live_consent(COORDINATOR_CONFIRMATION_PHRASE + " extra"))

    def test_single_request_consent_is_distinct_and_exact(self):
        self.assertEqual(SINGLE_REQUEST_CONFIRMATION_PHRASE, EXPECTED_SINGLE_REQUEST_PHRASE)
        self.assertTrue(has_explicit_single_request_consent(SINGLE_REQUEST_CONFIRMATION_PHRASE))
        self.assertFalse(has_explicit_single_request_consent(SINGLE_REQUEST_CONFIRMATION_PHRASE + " extra"))
        self.assertFalse(has_explicit_single_request_consent(" " + SINGLE_REQUEST_CONFIRMATION_PHRASE))
        self.assertFalse(has_explicit_live_consent(SINGLE_REQUEST_CONFIRMATION_PHRASE))

    def test_standard_live_workflows_use_the_two_request_phrase(self):
        root = Path(__file__).resolve().parent
        for relative_path in LIVE_WORKFLOW_PATHS:
            with self.subTest(workflow=relative_path):
                workflow_path = root / relative_path
                self.assertTrue(workflow_path.is_file(), relative_path + " is missing")
                workflow_text = workflow_path.read_text(encoding="utf-8")
                self.assertIn(CONFIRMATION_PHRASE, workflow_text)
                self.assertNotIn(COORDINATOR_CONFIRMATION_PHRASE, workflow_text)

    def test_single_request_workflow_is_manual_one_shot_and_relocks(self):
        root = Path(__file__).resolve().parent
        workflow_path = root / ".github/workflows/general-single-request-normal-token-test.yml"
        self.assertTrue(workflow_path.is_file())
        workflow = workflow_path.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn(SINGLE_REQUEST_CONFIRMATION_PHRASE, workflow)
        self.assertIn("github.run_attempt == 1", workflow)
        self.assertIn("inputs.confirm_free_usage_verified == true", workflow)
        self.assertIn("group: general-nvidia-api", workflow)
        self.assertIn("single-request-check", workflow)
        self.assertIn("single-request-unlock", workflow)
        self.assertIn("steps.temporary-unlock.outputs.unlock_started == 'true'", workflow)
        self.assertIn("general_nvidia_guard.py lock", workflow)
        self.assertIn("general_single_request_token_test.py", workflow)
        self.assertNotIn("schedule:", workflow)

    def test_coordinator_uses_its_separate_three_agent_approval(self):
        root = Path(__file__).resolve().parent
        workflow = (root / COORDINATOR_WORKFLOW_PATH).read_text(encoding="utf-8")
        coordinator_code = (root / "general_coordinator.py").read_text(encoding="utf-8")
        self.assertIn(COORDINATOR_CONFIRMATION_PHRASE, workflow)
        self.assertNotIn(CONFIRMATION_PHRASE, workflow)
        self.assertIn("EXECUTION_CONFIRMATION = COORDINATOR_CONFIRMATION_PHRASE", coordinator_code)
        self.assertIn('plan["agent_count"] > 3', coordinator_code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
