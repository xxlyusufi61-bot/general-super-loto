import json
import unittest
from pathlib import Path

import general_core


ROOT = Path(__file__).resolve().parent


class GeneralCoreOutputSafetyTests(unittest.TestCase):
    def test_report_metadata_never_contains_report_body(self):
        secret_text = "PRIVATE_REPORT_BODY_DO_NOT_LOG_7f2c11"
        metadata = general_core.safe_report_metadata(secret_text)

        self.assertEqual(
            metadata,
            {"report_received": True, "character_count": len(secret_text)},
        )
        self.assertNotIn(secret_text, json.dumps(metadata))

    def test_empty_or_invalid_report_is_marked_not_received(self):
        self.assertEqual(
            general_core.safe_report_metadata("   "),
            {"report_received": False, "character_count": 3},
        )
        self.assertEqual(
            general_core.safe_report_metadata(None),
            {"report_received": False, "character_count": 0},
        )

    def test_core_does_not_print_raw_agent_report(self):
        source = (ROOT / "general_core.py").read_text(encoding="utf-8")
        self.assertNotIn("--- AGENT REPORT ---", source)
        self.assertIn(
            'print("AGENT_REPORT_METADATA: " + json.dumps(safe_report_metadata(answer), sort_keys=True))',
            source,
        )

    def test_public_agent_workflow_never_artifacts_shared_memory_or_raw_fetch_log(self):
        workflow = (ROOT / ".github/workflows/general-agent.yml").read_text(
            encoding="utf-8"
        )
        # Shared memory may be staged only in the explicitly requested,
        # guard-validated review-PR path; it must never be bundled as an artifact.
        self.assertIn("Snapshot shared-memory baseline for optional review PR", workflow)
        self.assertIn("Validate single-agent memory delta", workflow)
        self.assertIn("Publish single-agent memory for human review only", workflow)
        upload_start = workflow.find("      - name: Upload run outputs for review")
        self.assertGreaterEqual(upload_start, 0)
        artifact_steps = workflow[upload_start:]
        self.assertNotIn("general_memory.json", artifact_steps)
        self.assertNotIn("cp archive_fetch_output.log", workflow)
        self.assertIn("inputs.agent == 'archive'", workflow)
        self.assertIn(
            "cp super_loto_archive.json general-run-output/super_loto_archive.json",
            workflow,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
