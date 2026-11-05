import hashlib
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import general_nvidia_diagnostic as diagnostic
import general_open_research as research
from general_live_consent import DIAGNOSTIC_CONFIRMATION_PHRASE


class SingleRequestDiagnosticTests(unittest.TestCase):
    def setUp(self):
        research._NVIDIA_REQUESTS_ATTEMPTED = 0
        research._NVIDIA_REQUEST_LABELS_ATTEMPTED.clear()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

        first = date(2026, 10, 20)
        self.reverse_row = {
            "date": first.isoformat(),
            "numbers": [1, 7, 13, 22, 34, 48],
        }
        self.discovery_rows = [
            {
                "date": (first + timedelta(days=1)).isoformat(),
                "numbers": [2, 8, 14, 23, 35, 49],
            },
            {
                "date": (first + timedelta(days=2)).isoformat(),
                "numbers": [3, 9, 15, 24, 36, 50],
            },
        ]
        self.forward_row = {
            "date": (first + timedelta(days=3)).isoformat(),
            "numbers": [4, 10, 16, 25, 37, 51],
        }
        self.target_row = {
            "date": (first + timedelta(days=4)).isoformat(),
            "numbers": [5, 11, 17, 26, 38, 52],
        }
        self.rows = [
            self.reverse_row,
            *self.discovery_rows,
            self.forward_row,
            self.target_row,
        ]
        self.target = self.target_row["date"]
        self.archive_sha = "a" * 64
        self.partition = {
            "target_date": self.target,
            "target_numbers_exposed": False,
            "discovery_rows": self.discovery_rows,
            "regimes": {
                "R60": {
                    "status": "READY",
                    "validation_rows": [self.forward_row],
                    "reverse_validation_rows": [self.reverse_row],
                }
            },
        }

    def _environment(self):
        return {
            "GENERAL_DIAGNOSTIC_MODE": "true",
            "GENERAL_CONFIRM_LIVE_CALLS": DIAGNOSTIC_CONFIRMATION_PHRASE,
            "GENERAL_NVIDIA_GUARD_PASSED": "1",
        }

    def test_compact_diagnostic_prompt_rejects_nonchronological_history(self):
        reversed_rows = list(reversed(self.discovery_rows))
        with self.assertRaisesRegex(RuntimeError, "not strictly chronological"):
            diagnostic.build_diagnostic_messages(self.target, reversed_rows)

    def test_compact_diagnostic_prompt_rejects_out_of_scope_history(self):
        invalid_rows = [
            {
                "date": "2020-08-01",
                "numbers": [1, 7, 13, 22, 34, 48],
            }
        ]
        with self.assertRaisesRegex(RuntimeError, "pre-scope or target/future"):
            diagnostic.build_diagnostic_messages(self.target, invalid_rows)

    def test_compact_diagnostic_prompt_rejects_noncanonical_dates(self):
        noncanonical_rows = [
            {
                "date": "20261021",
                "numbers": [2, 8, 14, 23, 35, 49],
            }
        ]
        with self.assertRaisesRegex(RuntimeError, "canonical YYYY-MM-DD"):
            diagnostic.build_diagnostic_messages(self.target, noncanonical_rows)

    def test_requires_the_separate_diagnostic_approval_before_preflight(self):
        with patch.dict(
            os.environ,
            {
                "GENERAL_DIAGNOSTIC_MODE": "true",
                "GENERAL_NVIDIA_GUARD_PASSED": "1",
            },
            clear=True,
        ), patch.object(diagnostic.sealed, "preflight") as preflight, patch.object(
            diagnostic.research, "call_model"
        ) as call_model:
            with self.assertRaisesRegex(RuntimeError, "separate one-request diagnostic approval"):
                diagnostic.run_diagnostic()
        preflight.assert_not_called()
        call_model.assert_not_called()

    def test_requires_diagnostic_mode_and_global_lock_preflight(self):
        with patch.dict(
            os.environ,
            {"GENERAL_CONFIRM_LIVE_CALLS": DIAGNOSTIC_CONFIRMATION_PHRASE},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "Diagnostic mode is not explicitly enabled"):
                diagnostic._validate_environment()

        with patch.dict(
            os.environ,
            {
                "GENERAL_DIAGNOSTIC_MODE": "true",
                "GENERAL_CONFIRM_LIVE_CALLS": DIAGNOSTIC_CONFIRMATION_PHRASE,
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "safety-lock preflight"):
                diagnostic._validate_environment()

    def test_one_forward_call_receives_only_discovery_rows_and_saves_safe_metadata(self):
        calls = []

        def fake_call_model(messages, *, request_label=None):
            calls.append((messages, request_label))
            research._NVIDIA_REQUESTS_ATTEMPTED += 1
            research._NVIDIA_REQUEST_LABELS_ATTEMPTED.add(request_label)
            return "synthetic completed model response"

        with patch.dict(os.environ, self._environment(), clear=True), patch.object(
            diagnostic.research, "BASE", self.root
        ), patch.object(
            diagnostic.research, "ARCHIVE", self.root / "super_loto_archive.json"
        ), patch.object(
            diagnostic, "RESULT_PATH", self.root / "general_nvidia_diagnostic_result.json"
        ), patch.object(
            diagnostic.sealed, "preflight", return_value={"status": "READY"}
        ) as preflight, patch.object(
            diagnostic.research, "parse_archive", return_value=self.rows
        ), patch.object(
            diagnostic.research,
            "isolate_discovery_only",
            return_value=(self.target, self.discovery_rows, {}),
        ), patch.object(
            diagnostic.sealed,
            "load_archive",
            return_value={"rows": self.rows, "archive_sha256": self.archive_sha},
        ), patch.object(
            diagnostic.sealed, "load_ledger", return_value={"entries": []}
        ), patch.object(
            diagnostic.sealed, "get_used_validation_dates", return_value=set()
        ), patch.object(
            diagnostic.sealed, "split_archive", return_value=self.partition
        ), patch.object(
            diagnostic.research, "call_model", side_effect=fake_call_model
        ):
            result = diagnostic.run_diagnostic()

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], "forward")
        preflight.assert_called_once()
        self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 1)
        self.assertEqual(research._NVIDIA_REQUEST_LABELS_ATTEMPTED, {"forward"})

        messages = calls[0][0]
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["discovery_direction"], "forward")
        self.assertEqual(payload["history_order"], "oldest_to_newest")
        self.assertEqual(payload["discovery_scope"], "pre_target_discovery_only")
        self.assertEqual(payload["target_date_metadata_only"], self.target)
        self.assertFalse(payload["target_numbers_included"])
        self.assertFalse(payload["post_target_records_included"])
        self.assertFalse(payload["sealed_validation_rows_included"])
        self.assertFalse(payload["reverse_discovery_included"])
        self.assertEqual(payload["discovery_history_by_regime"]["R60"], self.discovery_rows)
        self.assertIn("under 300 words", messages[0]["content"])
        self.assertIn("OPEN_DEFINITION", messages[0]["content"])
        self.assertNotIn("HYPOTHESIS_EXECUTION_SPECS_JSON", messages[0]["content"])
        self.assertNotIn("RULE_CHANGE_REQUEST_JSON", messages[0]["content"])
        sent_dates = {
            row["date"]
            for row in payload["discovery_history_by_regime"]["R60"]
        }
        self.assertNotIn(self.reverse_row["date"], sent_dates)
        self.assertNotIn(self.forward_row["date"], sent_dates)
        self.assertNotIn(self.target, sent_dates)

        self.assertEqual(result["status"], "RESPONSE_RECEIVED")
        self.assertEqual(result["response_mode"], "json")
        self.assertFalse(result["response_streaming"])
        self.assertEqual(result["provider_requests_attempted"], 1)
        self.assertEqual(result["automatic_retries"], 0)
        self.assertEqual(result["max_tokens_requested"], 8192)
        self.assertEqual(result["prompt_version"], "compact-forward-diagnostic-v1")
        self.assertFalse(result["reverse_request_sent"])
        self.assertEqual(result["sealed_validation_rows_sent"], 0)
        self.assertFalse(result["target_values_sent"])
        self.assertFalse(result["raw_response_persisted"])
        self.assertFalse(result["evaluation_ledger_modified"])
        self.assertFalse(result["method_registry_modified"])
        self.assertFalse(result["project_memory_modified"])
        self.assertEqual(result["response_sha256"], hashlib.sha256(
            b"synthetic completed model response"
        ).hexdigest())

        saved = json.loads(
            (self.root / "general_nvidia_diagnostic_result.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved, result)
        self.assertNotIn(
            "synthetic completed model response",
            (self.root / "general_nvidia_diagnostic_result.json").read_text(encoding="utf-8"),
        )

    @staticmethod
    def successful_json_response():
        response = MagicMock()
        response.status = 200
        response.headers = {"x-request-id": "synthetic-diagnostic-request"}
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = json.dumps({
            "choices": [{
                "message": {
                    "content": "synthetic final response",
                    "reasoning_content": "this must never be treated as final content",
                },
                "finish_reason": "stop",
            }],
            "usage": {"total_tokens": 2},
        }).encode("utf-8")
        return response

    def test_json_parser_accepts_only_completed_message_content(self):
        payload = {
            "choices": [{
                "message": {
                    "content": "final answer",
                    "reasoning_content": "hidden reasoning",
                },
                "finish_reason": "stop",
            }],
            "usage": {"total_tokens": 3},
        }
        parsed, metadata = research._validate_json_chat_completion(payload)
        self.assertEqual(parsed["choices"][0]["message"]["content"], "final answer")
        self.assertEqual(metadata["finish_reason"], "stop")

        reasoning_only = {
            "choices": [{
                "message": {
                    "content": "",
                    "reasoning_content": "not a final answer",
                },
                "finish_reason": "stop",
            }],
        }
        with self.assertRaisesRegex(RuntimeError, "reasoning_content is not accepted"):
            research._validate_json_chat_completion(reasoning_only)

        truncated = {
            "choices": [{
                "message": {"content": "partial answer"},
                "finish_reason": "length",
            }],
            "usage": {"total_tokens": 99},
        }
        with self.assertRaises(research.IncompleteModelResponseError) as caught:
            research._validate_json_chat_completion(truncated)
        self.assertEqual(caught.exception.usage, {"total_tokens": 99})
        self.assertIsNone(caught.exception.stream_event_count)
        self.assertIsNone(caught.exception.stream_completed)

    def test_call_model_uses_openai_sdk_json_nonstreaming_and_hard_stops_after_one_request(self):
        env = self._environment() | {
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }
        completion = MagicMock()
        completion.model_dump.return_value = {
            "choices": [{
                "message": {
                    "content": "synthetic final response",
                    "reasoning_content": "this must never be treated as final content",
                },
                "finish_reason": "stop",
            }],
            "usage": {"total_tokens": 2},
        }
        completion._request_id = "diagnostic-synthetic-request"
        client = MagicMock()
        client.chat.completions.create.return_value = completion
        saved_observations = []
        with patch.dict(os.environ, env, clear=True), patch.object(
            research, "reserve_nvidia_request"
        ) as reserve, patch.object(
            research,
            "save_usage_observation",
            side_effect=lambda observation, **kwargs: saved_observations.append(observation),
        ), patch(
            "general_open_research._create_nvidia_client", return_value=client
        ) as client_factory:
            answer = research.call_model(
                [{"role": "user", "content": "synthetic diagnostic"}],
                request_label="forward",
            )
            self.assertEqual(answer, "synthetic final response")
            client_factory.assert_called_once_with(
                "test-secret",
                research.DIAGNOSTIC_REQUEST_TIMEOUT_SECONDS,
            )
            client.chat.completions.create.assert_called_once()
            sent = client.chat.completions.create.call_args.kwargs
            self.assertEqual(sent["max_tokens"], research.DIAGNOSTIC_MAX_COMPLETION_TOKENS)
            self.assertIs(sent["stream"], False)
            self.assertEqual(sent["reasoning_effort"] if "reasoning_effort" in sent else sent["extra_body"]["reasoning_effort"], "low")
            client.close.assert_called_once()
            self.assertEqual(len(saved_observations), 1)
            observation = saved_observations[0]
            self.assertEqual(observation["response_mode"], "json")
            self.assertEqual(observation["transport"], "openai-python-sdk")
            self.assertFalse(observation["stream_requested"])
            self.assertEqual(observation["sdk_max_retries"], 0)
            self.assertTrue(observation["response_complete"])
            self.assertIsNone(observation["stream_completed"])
            self.assertEqual(observation["model_finish_reason"], "stop")
            self.assertEqual(
                observation["token_usage_returned_by_provider"]["total_tokens"], 2
            )
            self.assertEqual(
                observation["rate_limit_headers_returned"]["x-request-id"],
                "diagnostic-synthetic-request",
            )

            with self.assertRaisesRegex(RuntimeError, "Single-request diagnostic cap reached"):
                research.call_model(
                    [{"role": "user", "content": "second request must be blocked"}],
                    request_label="forward",
                )
            self.assertEqual(client.chat.completions.create.call_count, 1)
            reserve.assert_called_once_with(
                "open-research-forward",
                diagnostic_exception=True,
            )
            self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 1)

    def test_diagnostic_sdk_timeout_is_recorded_without_retry(self):
        env = self._environment() | {
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }
        client = MagicMock()
        client.chat.completions.create.side_effect = TimeoutError(
            "synthetic SDK diagnostic timeout"
        )
        saved = []
        with patch.dict(os.environ, env, clear=True), patch.object(
            research, "reserve_nvidia_request"
        ), patch.object(
            research,
            "save_usage_observation",
            side_effect=lambda observation, **kwargs: saved.append(observation),
        ), patch(
            "general_open_research._create_nvidia_client", return_value=client
        ) as client_factory:
            with self.assertRaisesRegex(RuntimeError, "No automatic retry was attempted"):
                research.call_model(
                    [{"role": "user", "content": "diagnostic timeout test"}],
                    request_label="forward",
                )
            client_factory.assert_called_once_with(
                "test-secret",
                research.DIAGNOSTIC_REQUEST_TIMEOUT_SECONDS,
            )
            client.chat.completions.create.assert_called_once()
            client.close.assert_called_once()

        self.assertEqual(len(saved), 1)
        observation = saved[0]
        self.assertEqual(observation["response_mode"], "json")
        self.assertEqual(
            observation["request_timeout_seconds"],
            research.DIAGNOSTIC_REQUEST_TIMEOUT_SECONDS,
        )
        self.assertIsNone(observation["connect_timeout_seconds"])
        self.assertIsNone(observation["response_header_timeout_seconds"])
        self.assertEqual(
            observation["request_delivery_state"],
            "unknown_after_sdk_request_started",
        )
        self.assertEqual(observation["automatic_retries"], 0)
        self.assertEqual(observation["sdk_max_retries"], 0)
        self.assertEqual(observation["failure_stage"], "provider_call")
        self.assertEqual(observation["network_error_type"], "TimeoutError")

    def test_reverse_label_is_rejected_in_diagnostic_mode_before_network(self):
        env = self._environment() | {
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "general_open_research._create_nvidia_client"
        ) as client_factory:
            with self.assertRaisesRegex(RuntimeError, "only one forward request"):
                research.call_model(
                    [{"role": "user", "content": "reverse"}],
                    request_label="reverse",
                )
            client_factory.assert_not_called()



class DiagnosticWorkflowBudgetTests(unittest.TestCase):
    def test_live_diagnostic_has_bounded_step_and_job_timeouts(self):
        workflow_path = Path(__file__).resolve().parent / ".github" / "workflows" / "nvidia-diagnostic.yml"
        workflow = workflow_path.read_text(encoding="utf-8")
        newline = chr(10)
        job_marker = "  diagnostic:" + newline
        self.assertIn(job_marker, workflow)
        diagnostic_job = workflow.split(job_marker, 1)[1]
        self.assertIn("    timeout-minutes: 35" + newline, diagnostic_job)

        request_step_marker = "      - name: Run exactly one forward diagnostic request" + newline
        self.assertIn(request_step_marker, diagnostic_job)
        request_step = diagnostic_job.split(request_step_marker, 1)[1].split(
            "      - name: Activate global lock after", 1
        )[0]
        self.assertIn("        timeout-minutes: 32" + newline, request_step)
        self.assertGreater(35, 32)


if __name__ == "__main__":
    unittest.main()
