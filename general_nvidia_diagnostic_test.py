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
    def successful_response():
        response = MagicMock()
        response.status = 200
        response.headers = {"x-request-id": "synthetic-diagnostic-request"}
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.readline.side_effect = [
            b'data: {"choices":[{"delta":{"content":"synthetic response"},"finish_reason":"stop"}]}\n',
            b"\n",
            b'data: {"choices":[],"usage":{"total_tokens":2}}\n',
            b"\n",
            b"data: [DONE]\n",
            b"\n",
            b"",
        ]
        return response

    def test_call_model_hard_stops_after_one_diagnostic_request(self):
        env = self._environment() | {
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }
        try:
            with patch.dict(os.environ, env, clear=True), patch.object(
                research, "reserve_nvidia_request"
            ) as reserve, patch.object(
                research, "save_usage_observation"
            ), patch(
                "general_open_research.urllib.request.urlopen",
                side_effect=[self.successful_response(), self.successful_response()],
            ) as urlopen:
                answer = research.call_model(
                    [{"role": "user", "content": "synthetic diagnostic"}],
                    request_label="forward",
                )
                self.assertEqual(answer, "synthetic response")
                sent_body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
                self.assertEqual(sent_body["max_tokens"], 8192)
                self.assertEqual(sent_body["reasoning_effort"], "low")
                self.assertTrue(sent_body["stream"])
                with self.assertRaisesRegex(RuntimeError, "Single-request diagnostic cap reached"):
                    research.call_model(
                        [{"role": "user", "content": "second request must be blocked"}],
                        request_label="forward",
                    )
                self.assertEqual(urlopen.call_count, 1)
                self.assertEqual(reserve.call_count, 1)
                self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 1)
        finally:
            Path("/tmp/general_nvidia_api_attempted").unlink(missing_ok=True)
            Path("/tmp/general_nvidia_api_succeeded").unlink(missing_ok=True)

    def test_reverse_label_is_rejected_in_diagnostic_mode_before_network(self):
        env = self._environment() | {
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "general_open_research.urllib.request.urlopen"
        ) as urlopen:
            with self.assertRaisesRegex(RuntimeError, "only one forward request"):
                research.call_model(
                    [{"role": "user", "content": "reverse"}],
                    request_label="reverse",
                )
            urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
