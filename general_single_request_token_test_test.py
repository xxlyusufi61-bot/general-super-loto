import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import general_single_request_token_test as token_test
import general_open_research as research
from general_live_consent import SINGLE_REQUEST_CONFIRMATION_PHRASE


class SingleRequestTokenTestTests(unittest.TestCase):
    def environment(self):
        return {
            "GENERAL_SINGLE_REQUEST_MODE": "true",
            "GENERAL_DIAGNOSTIC_MODE": "false",
            "GENERAL_CONFIRM_LIVE_CALLS": SINGLE_REQUEST_CONFIRMATION_PHRASE,
            "GENERAL_NVIDIA_GUARD_PASSED": "1",
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "synthetic-test-key",
        }

    def test_environment_requires_exact_normal_single_request_authorization(self):
        with patch.dict(os.environ, self.environment(), clear=True):
            token_test._validate_environment()
        for key, value in [
            ("GENERAL_SINGLE_REQUEST_MODE", "false"),
            ("GENERAL_DIAGNOSTIC_MODE", "true"),
            ("GENERAL_CONFIRM_LIVE_CALLS", "I_CONFIRM_FREE_ONLY_NO_BILLING_AND_APPROVE_UP_TO_2_NVIDIA_REQUESTS"),
            ("GENERAL_NVIDIA_GUARD_PASSED", "0"),
            ("GENERAL_ALLOW_NVIDIA_CALL", "false"),
            ("GENERAL_NVIDIA_FREE_USAGE_VERIFIED", "false"),
            ("NVIDIA_API_KEY", ""),
        ]:
            with patch.dict(os.environ, self.environment() | {key: value}, clear=True):
                with self.subTest(key=key, value=value):
                    with self.assertRaises(RuntimeError):
                        token_test._validate_environment()

    def test_partition_requires_disjoint_pre_target_forward_and_reverse_holdouts(self):
        target = "2026-11-05"
        discovery = [{"date": "2026-11-01", "numbers": [1, 2, 3, 4, 5, 6]}]
        partition = {
            "target_date": target,
            "target_numbers_exposed": False,
            "discovery_rows": discovery,
            "regimes": {
                "R60": {
                    "status": "READY",
                    "validation_rows": [{"date": "2026-11-02"}],
                    "reverse_validation_rows": [{"date": "2026-11-03"}],
                }
            },
        }
        forward, reverse = token_test._validate_forward_partition(target, discovery, partition)
        self.assertEqual(forward, ["2026-11-02"])
        self.assertEqual(reverse, ["2026-11-03"])

        overlap = json.loads(json.dumps(partition))
        overlap["regimes"]["R60"]["reverse_validation_rows"] = [{"date": "2026-11-02"}]
        with self.assertRaisesRegex(RuntimeError, "overlap"):
            token_test._validate_forward_partition(target, discovery, overlap)

        target_leak = json.loads(json.dumps(partition))
        target_leak["regimes"]["R60"]["validation_rows"] = [{"date": target}]
        with self.assertRaisesRegex(RuntimeError, "target, or future"):
            token_test._validate_forward_partition(target, discovery, target_leak)

        exposed = json.loads(json.dumps(partition))
        exposed["target_numbers_exposed"] = True
        with self.assertRaisesRegex(RuntimeError, "target-isolation"):
            token_test._validate_forward_partition(target, discovery, exposed)

    def test_run_makes_one_forward_call_and_persists_only_safe_metadata(self):
        research._NVIDIA_REQUESTS_ATTEMPTED = 0
        research._NVIDIA_REQUEST_LABELS_ATTEMPTED.clear()
        rows = [{"date": "2026-11-01", "numbers": [1, 2, 3, 4, 5, 6]}]
        discovery = [{"date": "2026-11-01", "numbers": [1, 2, 3, 4, 5, 6]}]
        partition = {
            "target_date": "2026-11-05",
            "target_numbers_exposed": False,
            "discovery_rows": discovery,
            "regimes": {
                "R60": {
                    "status": "READY",
                    "validation_rows": [{"date": "2026-11-02"}],
                    "reverse_validation_rows": [{"date": "2026-11-03"}],
                }
            },
        }
        prompt_payload = {
            "discovery_direction": "forward",
            "history_order": "oldest_to_newest",
            "target_date_metadata_only": "2026-11-05",
            "post_target_records_included": False,
            "discovery_history_by_regime": {"R60": discovery},
        }
        response_text = '{"synthetic":"response must not be persisted"}'

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            memory = root / "memory.json"
            registry = root / "registry.json"
            result_path = root / "result.json"
            usage_path = root / "usage_forward.json"
            archive.write_text("archive input", encoding="utf-8")
            ledger.write_text('{"entries":[]}', encoding="utf-8")
            memory.write_text('{"entries":[]}', encoding="utf-8")
            registry.write_text('{"methods":{}}', encoding="utf-8")

            archive_sha256 = hashlib.sha256(b"archive input").hexdigest()

            def fake_call_model(_messages, *, request_label=None):
                self.assertEqual(request_label, "forward")
                research._NVIDIA_REQUESTS_ATTEMPTED += 1
                research._NVIDIA_REQUEST_LABELS_ATTEMPTED.add("forward")
                usage_path.write_text(json.dumps({
                    "max_tokens_requested": 8192,
                    "model_finish_reason": "stop",
                    "response_complete": True,
                    "automatic_retries": 0,
                    "sdk_max_retries": 0,
                    "stream_requested": False,
                }), encoding="utf-8")
                return response_text

            with patch.dict(os.environ, self.environment(), clear=True), patch.object(
                token_test.sealed, "preflight", return_value={"status": "READY"}
            ), patch.object(
                token_test.research, "parse_archive", return_value=rows
            ), patch.object(
                token_test.research, "isolate_discovery_only",
                return_value=("2026-11-05", discovery, {}),
            ) as isolate, patch.object(
                token_test.sealed, "load_archive",
                return_value={"archive_sha256": archive_sha256, "rows": rows},
            ), patch.object(
                token_test.sealed, "load_ledger", return_value={}
            ), patch.object(
                token_test.sealed, "get_used_validation_dates", return_value=set()
            ), patch.object(
                token_test.sealed, "split_archive", return_value=partition
            ), patch.object(
                token_test.research_memory, "direction_context", return_value=[]
            ), patch.object(
                token_test.research, "build_messages",
                return_value=[
                    {"role": "system", "content": "test system"},
                    {"role": "user", "content": json.dumps(prompt_payload)},
                ],
            ) as build_messages, patch.object(
                token_test.research, "call_model", side_effect=fake_call_model
            ) as call_model, patch.object(
                token_test.research, "USAGE_REPORT", usage_path
            ):
                result = token_test.run_token_test(
                    archive_path=archive,
                    ledger_path=ledger,
                    memory_path=memory,
                    registry_path=registry,
                    result_path=result_path,
                )

            isolate.assert_called_once()
            build_messages.assert_called_once()
            call_model.assert_called_once()
            self.assertEqual(call_model.call_args.kwargs["request_label"], "forward")
            self.assertEqual(result["provider_requests_attempted"], 1)
            self.assertEqual(result["max_tokens_requested"], 8192)
            self.assertFalse(result["raw_response_persisted"])
            self.assertFalse(result["evaluation_ledger_modified"])
            self.assertFalse(result["research_memory_modified"])
            self.assertFalse(result["method_registry_modified"])
            saved = result_path.read_text(encoding="utf-8")
            self.assertNotIn(response_text, saved)
            self.assertNotIn("synthetic response must not be persisted", saved)
            self.assertEqual(json.loads(saved), result)
            self.assertEqual(ledger.read_text(encoding="utf-8"), '{"entries":[]}')
            self.assertEqual(memory.read_text(encoding="utf-8"), '{"entries":[]}')
            self.assertEqual(registry.read_text(encoding="utf-8"), '{"methods":{}}')

    def test_failed_or_unready_sealed_preflight_stops_before_model_call(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            archive.write_text("archive", encoding="utf-8")
            ledger.write_text("{}", encoding="utf-8")
            with patch.dict(os.environ, self.environment(), clear=True), patch.object(
                token_test.sealed, "preflight", return_value={"status": "NOT_READY"}
            ), patch.object(token_test.research, "call_model") as call_model:
                with self.assertRaisesRegex(RuntimeError, "did not return READY"):
                    token_test.run_token_test(
                        archive_path=archive,
                        ledger_path=ledger,
                        memory_path=root / "memory.json",
                        registry_path=root / "registry.json",
                        result_path=root / "result.json",
                    )
                call_model.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
