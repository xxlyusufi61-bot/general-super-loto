import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKFLOWS = ROOT / ".github" / "workflows"
CONFIRMATION = "I_CONFIRM_FREE_ONLY_NO_BILLING_AND_APPROVE_UP_TO_3_NVIDIA_REQUESTS"

WORKFLOW_CASES = (
    ("general-agent.yml", "Check global NVIDIA safety lock", "Run selected GENERAL agent"),
    ("nvidia-test.yml", "Check global NVIDIA safety lock", "Test NVIDIA API connection"),
    ("nvidia-chat-test.yml", "Check global NVIDIA safety lock", "Ask NVIDIA AI model"),
)


def read_step(source: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    start = source.find(marker)
    if start < 0:
        raise AssertionError(f"Missing workflow step: {name}")
    end = source.find("\n      - name:", start + len(marker))
    if end < 0:
        end = len(source)
    return source[start:end]


class LiveModelConsentGateTests(unittest.TestCase):
    def assert_default_no_and_phrase_present(self, source: str) -> None:
        self.assertIn("      confirm_live_calls:", source)
        self.assertIn('        default: "NO"', source)
        self.assertIn(CONFIRMATION, source)

    def test_live_request_workflows_default_to_no_and_gate_lock_and_request(self):
        for filename, guard_name, request_name in WORKFLOW_CASES:
            with self.subTest(workflow=filename):
                source = (WORKFLOWS / filename).read_text(encoding="utf-8")
                self.assert_default_no_and_phrase_present(source)
                expected_if = "inputs.confirm_live_calls == '" + CONFIRMATION + "'"
                self.assertIn(expected_if, read_step(source, guard_name))
                self.assertIn(expected_if, read_step(source, request_name))

                no_consent_name = (
                    "Skip live agent call without explicit free-only confirmation"
                    if filename == "general-agent.yml"
                    else "Report no NVIDIA request when confirmation is absent"
                )
                no_consent = read_step(source, no_consent_name)
                self.assertIn("inputs.confirm_live_calls != '" + CONFIRMATION + "'", no_consent)
                self.assertIn("nvidia", no_consent.lower())
                self.assertIn("made", no_consent.lower())

    def test_blank_slate_research_requires_exact_phrase_free_check_and_global_lock(self):
        source = (WORKFLOWS / "open-research.yml").read_text(encoding="utf-8")
        self.assert_default_no_and_phrase_present(source)
        self.assertIn("inputs.confirm_live_calls == '" + CONFIRMATION + "'", source)
        self.assertIn("inputs.confirm_free_usage_verified == true", source)
        self.assertIn("GENERAL_CONFIRM_LIVE_CALLS:", source)
        self.assertIn("GENERAL_NVIDIA_GUARD_PASSED=1", source)
        guard_step = read_step(source, "Check global NVIDIA safety lock")
        request_step = read_step(source, "Run isolated blank-slate exploration")
        lock_step = read_step(source, "Activate global lock after an incomplete NVIDIA research request")
        self.assertIn("general_nvidia_guard.py check", guard_step)
        self.assertIn("python3 general_open_research.py", request_step)
        self.assertIn("steps.nvidia-research.outcome == 'failure'", lock_step)
        self.assertIn("general_nvidia_guard.py lock", lock_step)

    def test_coordinator_execute_keeps_its_existing_explicit_gate(self):
        source = (WORKFLOWS / "general-coordinator-execute.yml").read_text(encoding="utf-8")
        self.assertIn(CONFIRMATION, source)
        consent_step = read_step(source, "Evaluate explicit free-only consent")
        self.assertIn('CONFIRM_LIVE_CALLS" = "' + CONFIRMATION, consent_step)
        run_step = read_step(source, "Run coordinator agents sequentially")
        self.assertIn("steps.consent.outputs.enabled == 'true'", run_step)
        self.assertIn("GENERAL_CONFIRM_LIVE_CALLS:", run_step)


if __name__ == "__main__":
    unittest.main(verbosity=2)
