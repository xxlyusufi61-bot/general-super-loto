import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path

from general_coordinator_memory_publication import (
    publication_decision,
    summarize_log,
    summarize_payload,
    main as publication_main,
)


def payload(status="success", results=None):
    results = results if results is not None else [
        {"agent": "validation", "status": "success", "report_received": True},
        {"agent": "debug", "status": "success", "report_received": True},
    ]
    return {
        "planner": "GENERAL Coordinator",
        "planner_version": "0.2.0",
        "mode": "sequential_execution",
        "status": status,
        "task_type": "code",
        "agent_results": results,
        "successful_agents": sum(item["status"] == "success" for item in results),
        "agent_invocations_started": len(results),
        "report": "PRIVATE_REPORT_TEXT_MUST_NOT_LEAK",
        "note": "ANOTHER_PRIVATE_REPORT_MUST_NOT_LEAK",
    }


def valid_memory_summary():
    return {
        "valid": True,
        "has_new_entries": True,
        "new_entries_count": 2,
        "all_new_entries_unverified": True,
        "binding_rules_changed": False,
        "auto_merge": False,
    }


def valid_coordinator_summary():
    return summarize_payload(payload())


class CoordinatorOutputSanitizationTests(unittest.TestCase):
    def test_parses_wrapped_json_and_suppresses_report_text(self):
        log_text = (
            "debug line PRIVATE_REPORT_TEXT_MUST_NOT_LEAK\n"
            + json.dumps(payload(), ensure_ascii=False, indent=2)
            + "\nmore harmless runner output\n"
        )
        summary = summarize_log(log_text)
        serialized = json.dumps(summary, ensure_ascii=False)

        self.assertEqual(summary["status"], "success")
        self.assertEqual(summary["task_type"], "code")
        self.assertEqual(summary["successful_agents"], 2)
        self.assertEqual(summary["agent_invocations_started"], 2)
        self.assertTrue(summary["report_text_suppressed"])
        self.assertNotIn("PRIVATE_REPORT_TEXT_MUST_NOT_LEAK", serialized)
        self.assertNotIn("ANOTHER_PRIVATE_REPORT_MUST_NOT_LEAK", serialized)
        self.assertEqual(
            summary["agent_results"],
            [
                {"agent": "validation", "status": "success", "report_received": True},
                {"agent": "debug", "status": "success", "report_received": True},
            ],
        )

    def test_failure_summary_keeps_only_allowlisted_error_metadata(self):
        source = payload(
            status="failed",
            results=[
                {"agent": "validation", "status": "success", "report_received": True},
                {
                    "agent": "debug",
                    "status": "failed",
                    "error_type": "TimeoutError",
                    "secret": "PRIVATE_FAILURE_REPORT",
                },
                {
                    "agent": "research",
                    "status": "failed",
                    "error_type": "PRIVATE_REPORT_TEXT",
                },
            ],
        )
        summary = summarize_payload(source)
        serialized = json.dumps(summary)

        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["agent_results"][1]["error_type"], "TimeoutError")
        self.assertNotIn("PRIVATE_FAILURE_REPORT", serialized)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("PRIVATE_REPORT_TEXT", serialized)
        self.assertNotIn("PRIVATE_FAILURE_REPORT", serialized)

    def test_malformed_untrusted_fields_fail_closed_without_leaking_them(self):
        source = payload()
        source["status"] = {"unexpected": "PRIVATE_STATUS_TEXT"}
        source["task_type"] = ["PRIVATE_TASK_TEXT"]
        summary = summarize_payload(source)

        self.assertEqual(summary["status"], "unparsed")
        self.assertIsNone(summary["task_type"])
        self.assertNotIn("PRIVATE_STATUS_TEXT", json.dumps(summary))
        self.assertNotIn("PRIVATE_TASK_TEXT", json.dumps(summary))

    def test_invalid_agent_metadata_makes_summary_unparsed(self):
        source = payload(
            results=[
                {"agent": "PRIVATE_REPORT_TEXT", "status": "success", "report_received": True},
            ]
        )
        summary = summarize_payload(source)
        self.assertEqual(summary["status"], "unparsed")
        self.assertNotIn("PRIVATE_REPORT_TEXT", json.dumps(summary))

    def test_missing_or_broken_payload_is_unparsed(self):
        for log_text in (
            "no coordinator JSON here",
            '{\n  "planner": "GENERAL Coordinator", not valid JSON',
            json.dumps({"planner": "not GENERAL"}),
        ):
            with self.subTest(log_text=log_text):
                summary = summarize_log(log_text)
                self.assertEqual(summary["status"], "unparsed")
                self.assertTrue(summary["report_text_suppressed"])


class PublicationDecisionTests(unittest.TestCase):
    def decide(
        self,
        *,
        coordinator=None,
        memory=None,
        request="YES",
        baseline=b'{"entries":[]}\n',
        current=b'{"entries":[]}\n',
    ):
        return publication_decision(
            valid_coordinator_summary() if coordinator is None else coordinator,
            valid_memory_summary() if memory is None else memory,
            request,
            baseline,
            current,
        )

    def test_allows_valid_candidate_only_for_manual_review(self):
        result = self.decide()
        self.assertTrue(result["allowed"])
        self.assertEqual(result["reason"], "validated_for_manual_review")
        self.assertTrue(result["human_review_only"])
        self.assertEqual(result["new_entries_count"], 2)

    def test_publication_no_is_blocked(self):
        result = self.decide(request="NO")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "publication_not_requested")

    def test_no_new_entries_is_blocked(self):
        memory = valid_memory_summary()
        memory["has_new_entries"] = False
        memory["new_entries_count"] = 0
        result = self.decide(memory=memory)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "no_new_memory_entries")

    def test_invalid_memory_guard_is_blocked(self):
        memory = valid_memory_summary()
        memory["valid"] = False
        result = self.decide(memory=memory)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "memory_guard_failed")

    def test_new_entries_must_remain_unverified(self):
        memory = valid_memory_summary()
        memory["all_new_entries_unverified"] = False
        result = self.decide(memory=memory)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "new_entries_not_unverified")

    def test_binding_rules_and_auto_merge_must_remain_locked(self):
        for key, value, reason in (
            ("binding_rules_changed", True, "binding_rules_changed"),
            ("auto_merge", True, "automatic_merge_not_disabled"),
        ):
            with self.subTest(key=key):
                memory = valid_memory_summary()
                memory[key] = value
                result = self.decide(memory=memory)
                self.assertFalse(result["allowed"])
                self.assertEqual(result["reason"], reason)

    def test_stale_main_memory_is_blocked(self):
        result = self.decide(current=b'{"entries":[{"id":99}]}\n')
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "main_memory_changed_during_run")

    def test_missing_baseline_is_blocked(self):
        result = self.decide(baseline=None)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "main_baseline_unavailable")

    def test_unparsed_or_rejected_coordinator_result_is_blocked(self):
        for status in ("unparsed", "rejected"):
            with self.subTest(status=status):
                summary = valid_coordinator_summary()
                summary["status"] = status
                result = self.decide(coordinator=summary)
                self.assertFalse(result["allowed"])
                self.assertEqual(result["reason"], "coordinator_status_not_publishable")

    def test_no_successful_agent_report_is_blocked(self):
        summary = summarize_payload(payload(
            status="failed",
            results=[
                {"agent": "debug", "status": "failed", "error_type": "TimeoutError"},
            ],
        ))
        result = self.decide(coordinator=summary)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "no_successful_report_to_publish")

    def test_partial_failure_with_a_valid_prior_report_can_be_reviewed(self):
        summary = summarize_payload(payload(
            status="failed",
            results=[
                {"agent": "validation", "status": "success", "report_received": True},
                {"agent": "debug", "status": "failed", "error_type": "TimeoutError"},
            ],
        ))
        result = self.decide(coordinator=summary)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["coordinator_status"], "failed")

    def test_inconsistent_agent_counts_are_blocked(self):
        summary = valid_coordinator_summary()
        summary["successful_agents"] = 1
        result = self.decide(coordinator=summary)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "successful_agent_count_mismatch")

    def test_malformed_publication_input_fails_closed(self):
        summary = valid_coordinator_summary()
        summary["status"] = {"unexpected": "value"}
        result = self.decide(coordinator=summary)
        self.assertFalse(result["allowed"])


class PublicationCliTests(unittest.TestCase):
    def _write_json(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def test_publication_cli_allows_valid_candidate_without_exposing_report_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.json"
            current = root / "current-main.json"
            coordinator = root / "coordinator-summary.json"
            memory = root / "memory-summary.json"
            baseline.write_text('{"entries":[]}\n', encoding="utf-8")
            current.write_bytes(baseline.read_bytes())
            self._write_json(coordinator, valid_coordinator_summary())
            self._write_json(memory, valid_memory_summary())
            argv = [
                "general_coordinator_memory_publication.py", "publication-check",
                "--coordinator-summary", str(coordinator),
                "--memory-summary", str(memory),
                "--baseline", str(baseline),
                "--current-main", str(current),
                "--publish-request", "YES",
            ]
            output = io.StringIO()
            with patch("sys.argv", argv), redirect_stdout(output):
                code = publication_main()

        decision = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(decision["allowed"])
        self.assertNotIn("PRIVATE_REPORT", output.getvalue())

    def test_publication_cli_blocks_stale_main_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline = root / "baseline.json"
            current = root / "current-main.json"
            coordinator = root / "coordinator-summary.json"
            memory = root / "memory-summary.json"
            baseline.write_text('{"entries":[]}\n', encoding="utf-8")
            current.write_text('{"entries":[{"id":99}]}\n', encoding="utf-8")
            self._write_json(coordinator, valid_coordinator_summary())
            self._write_json(memory, valid_memory_summary())
            argv = [
                "general_coordinator_memory_publication.py", "publication-check",
                "--coordinator-summary", str(coordinator),
                "--memory-summary", str(memory),
                "--baseline", str(baseline),
                "--current-main", str(current),
                "--publish-request", "YES",
            ]
            output = io.StringIO()
            with patch("sys.argv", argv), redirect_stdout(output):
                code = publication_main()

        decision = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "main_memory_changed_during_run")


class WorkflowWiringTests(unittest.TestCase):
    def test_execution_workflow_uses_helpers_and_excludes_raw_log_from_artifact(self):
        workflow_path = Path(".github/workflows/general-coordinator-execute.yml")
        workflow = workflow_path.read_text(encoding="utf-8")

        self.assertIn("general_coordinator_memory_publication.py summarize-log", workflow)
        self.assertIn("general_coordinator_memory_publication.py publication-check", workflow)
        self.assertIn("rm -f coordinator-execution.log", workflow)
        self.assertIn("default: \"NO\"", workflow)
        self.assertIn("path: general-coordinator-output/", workflow)
        self.assertIn("steps.memory-proposal.outputs.has_new_entries == 'true'", workflow)
        self.assertIn("inputs.publish_memory_pr == 'YES'", workflow)
        self.assertNotIn("path: coordinator-execution.log", workflow)
        self.assertNotIn("path: general_memory.json", workflow)


if __name__ == "__main__":
    unittest.main(verbosity=2)
