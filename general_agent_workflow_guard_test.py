import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WORKFLOWS = ROOT / ".github" / "workflows"
CONFIRMATION = "I_CONFIRM_FREE_ONLY_NO_BILLING_AND_APPROVE_UP_TO_3_NVIDIA_REQUESTS"

WORKFLOW_CASES = (
    (
        "general-agent.yml",
        "Check global NVIDIA safety lock",
        "Run selected GENERAL agent",
    ),
    (
        "nvidia-test.yml",
        "Check global NVIDIA safety lock",
        "Test NVIDIA API connection",
    ),
    (
        "nvidia-chat-test.yml",
        "Check global NVIDIA safety lock",
        "Ask NVIDIA AI model",
    ),
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
    def test_live_request_workflows_default_to_no_and_gate_lock_and_request(self):
        for filename, guard_name, request_name in WORKFLOW_CASES:
            with self.subTest(workflow=filename):
                source = (WORKFLOWS / filename).read_text(encoding="utf-8")
                self.assertIn("      confirm_live_calls:", source)
                self.assertRegex(
                    source,
                    r'(?m)^        default: "NO"$',
                    "Consent input must default to NO.",
                )
                self.assertIn(CONFIRMATION, source)

                guard_step = read_step(source, guard_name)
                request_step = read_step(source, request_name)
                expected_if = f"inputs.confirm_live_calls == '{CONFIRMATION}'"
                self.assertIn(expected_if, guard_step)
                self.assertIn(expected_if, request_step)

                no_consent_step = read_step(
                    source,
                    "Skip live agent call without explicit free-only confirmation"
                    if filename == "general-agent.yml"
                    else "Report no NVIDIA request when confirmation is absent",
                )
                self.assertIn(
                    f"inputs.confirm_live_calls != '{CONFIRMATION}'",
                    no_consent_step,
                )
                self.assertIn("no NVIDIA request was made", no_consent_step.lower())

    def test_coordinator_execute_keeps_its_existing_explicit_gate(self):
        source = (
            WORKFLOWS / "general-coordinator-execute.yml"
        ).read_text(encoding="utf-8")
        self.assertIn(CONFIRMATION, source)
        consent_step = read_step(source, "Evaluate explicit free-only consent")
        self.assertIn(f'CONFIRM_LIVE_CALLS" = "{CONFIRMATION}"', consent_step)
        run_step = read_step(source, "Run coordinator agents sequentially")
        self.assertIn("steps.consent.outputs.enabled == 'true'", run_step)

    def test_workflow_run_gating_is_static_and_does_not_use_api(self):
        # The guard test itself is offline: it only reads local workflow text.
        self.assertNotIn("integrate.api.nvidia.com", __file__.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
