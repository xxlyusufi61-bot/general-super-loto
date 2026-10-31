import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

import general_open_research as research
from general_live_consent import CONFIRMATION_PHRASE


class OpenResearchIsolationTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"date": "2026-10-18", "numbers": [1, 7, 13, 22, 34, 48]},
            {"date": "2026-10-20", "numbers": [2, 8, 14, 23, 35, 49]},
            {"date": "2026-10-22", "numbers": [3, 9, 15, 24, 36, 50]},
        ]

    def test_target_and_future_are_not_in_model_payload(self):
        target, train = research.isolate_pre_target(self.rows)
        messages = research.build_messages(target, train)
        payload = json.loads(messages[1]["content"])
        self.assertEqual(target, "2026-10-22")
        self.assertEqual(
            [r["date"] for r in payload["pre_target_history_by_regime"]["R60"]],
            ["2026-10-18", "2026-10-20"],
        )
        self.assertEqual(payload["pre_target_history_by_regime"]["R54"], [])
        self.assertEqual(payload["regime_counts"], {"R54": 0, "R60": 2})
        self.assertTrue(payload["regimes_must_be_evaluated_separately"])
        self.assertNotIn("[3,9,15,24,36,50]", messages[1]["content"])
        self.assertFalse(payload["target_numbers_included"])
        self.assertFalse(payload["post_target_records_included"])
        self.assertNotIn("3,9,15,24,36,50", messages[1]["content"])

    def test_prompt_preserves_neutral_notation_without_teaching_old_formulas(self):
        target, train = research.isolate_pre_target(self.rows)
        messages = research.build_messages(target, train)
        system = messages[0]["content"].lower()
        for label in ("a->b->c", "a->t", "mod5->t"):
            self.assertIn(label, system)
        for legacy_marker in ("v8.6", "v19", "v0.3.1", "t[i] = b[i] - a[i]", "historical formula semantics"):
            self.assertNotIn(legacy_marker, system)

    def test_prompt_requires_bounded_structured_proposal_block(self):
        target, train = research.isolate_pre_target(self.rows)
        system = research.build_messages(target, train)[0]["content"]
        self.assertIn("<HYPOTHESIS_PROPOSALS_JSON>", system)
        self.assertIn("</HYPOTHESIS_PROPOSALS_JSON>", system)
        self.assertIn('"schema_version":1', system)
        self.assertIn('"falsification_criterion"', system)
        self.assertIn('"forward_walk_forward"', system)
        self.assertIn("An empty proposals list is an acceptable result.", system)
        for label in ("a->b->c", "a->t", "mod5->t"):
            self.assertIn(label, system.lower())
        self.assertNotIn("v8.6", system.lower())
        self.assertNotIn("t[i] = b[i] - a[i]", system.lower())

    def test_prompt_describes_restricted_dsl_without_legacy_formula_context(self):
        system = research.build_messages("2026-10-22", self.rows[:2])[0]["content"].lower()
        self.assertIn("<hypothesis_execution_specs_json>", system)
        self.assertIn("</hypothesis_execution_specs_json>", system)
        self.assertIn("aggregate_history_numbers", system)
        self.assertIn("nested aggregates", system)
        self.assertIn("if a hypothesis cannot be represented", system)
        self.assertIn("not approved or canonical", system)
        self.assertIn("fixed synthetic r54/r60 contract fixtures", system)
        self.assertIn("not claim that it was real-archive backtested", system)
        self.assertNotIn("eval(", system)
        self.assertNotIn("exec(", system)
        self.assertNotIn("v19", system)
        self.assertNotIn("t[i] = b[i] - a[i]", system)

    def test_prompt_requires_user_approval_for_rule_changes(self):
        system = research.build_messages("2026-10-22", self.rows[:2])[0]["content"].lower()
        self.assertIn("explicit telegram user approval", system)
        self.assertIn('"change_type":"add|revise|remove"', system)
        self.assertIn("<rule_change_request_json>", system)
        self.assertIn("separate reviewed implementation pull request is required", system)
        self.assertIn("telegram", system)

    def test_r54_and_r60_are_sent_as_disjoint_regime_partitions(self):
        history = [
            {"date": "2020-07-30", "numbers": [1, 7, 13, 22, 34, 48]},
            {"date": "2020-08-02", "numbers": [2, 8, 14, 23, 35, 49]},
        ]
        messages = research.build_messages("2020-08-05", history)
        payload = json.loads(messages[1]["content"])
        self.assertEqual([row["date"] for row in payload["pre_target_history_by_regime"]["R54"]], ["2020-07-30"])
        self.assertEqual([row["date"] for row in payload["pre_target_history_by_regime"]["R60"]], ["2020-08-02"])
        self.assertEqual(
            set(row["date"] for row in payload["pre_target_history_by_regime"]["R54"])
            & set(row["date"] for row in payload["pre_target_history_by_regime"]["R60"]),
            set(),
        )

    def test_invalid_archive_stops_before_model_payload(self):
        bad = [
            {"date": "2026-10-20", "numbers": [1, 1, 14, 23, 35, 49]},
            {"date": "2026-10-22", "numbers": [3, 9, 15, 24, 36, 50]},
        ]
        with self.assertRaises(ValueError):
            research.isolate_pre_target(bad)

    def test_target_or_future_training_row_is_rejected(self):
        target, train = research.isolate_pre_target(self.rows)
        self.assertTrue(all(row["date"] < target for row in train))


class NvidiaUsageObservationTests(unittest.TestCase):
    def test_only_safe_rate_limit_headers_are_recorded(self):
        observed = research.build_usage_observation(
            200,
            {
                "x-ratelimit-limit-requests": "10",
                "x-ratelimit-remaining-requests": "8",
                "retry-after": "30",
                "x-request-id": "request-123",
                "Authorization": "Bearer FAKE_BEARER_TOKEN_DO_NOT_USE",
                "Set-Cookie": "must-not-be-recorded",
            },
            {"usage": {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160}},
        )
        self.assertEqual(observed["http_status"], 200)
        self.assertEqual(observed["automatic_retries"], 0)
        self.assertEqual(observed["remaining_quota_before_request"], None)
        self.assertEqual(observed["account_level_quota_status"], "UNKNOWN")
        self.assertEqual(
            observed["rate_limit_headers_returned"]["x-ratelimit-remaining-requests"], "8"
        )
        self.assertEqual(observed["token_usage_returned_by_provider"]["total_tokens"], 160)
        header_blob = json.dumps(observed["rate_limit_headers_returned"])
        self.assertNotIn("Authorization", header_blob)
        self.assertNotIn("Set-Cookie", header_blob)
        self.assertNotIn("FAKE_BEARER_TOKEN_DO_NOT_USE", json.dumps(observed))
        self.assertNotIn("must-not-be-recorded", json.dumps(observed))

    def test_inference_requires_explicit_approval_and_sends_no_request_otherwise(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "general_open_research.urllib.request.urlopen"
        ) as mocked_urlopen:
            with self.assertRaisesRegex(RuntimeError, "not explicitly approved"):
                research.call_model([])
            mocked_urlopen.assert_not_called()

    def test_live_inference_is_blocked_without_global_lock_preflight(self):
        with patch.dict(
            os.environ,
            {
                "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
                "GENERAL_ALLOW_NVIDIA_CALL": "true",
                "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
                "NVIDIA_API_KEY": "test-secret",
            },
            clear=True,
        ), patch("general_open_research.urllib.request.urlopen") as mocked_urlopen:
            with self.assertRaisesRegex(RuntimeError, "Global NVIDIA safety-lock preflight was not confirmed"):
                research.call_model([{"role": "user", "content": "test"}])
            mocked_urlopen.assert_not_called()

    def test_live_inference_is_blocked_when_free_use_is_not_verified(self):
        with patch.dict(
            os.environ,
            {
                "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
                "GENERAL_NVIDIA_GUARD_PASSED": "1",
                "GENERAL_ALLOW_NVIDIA_CALL": "true",
                "NVIDIA_API_KEY": "test-secret",
            },
            clear=True,
        ), patch("general_open_research.urllib.request.urlopen") as mocked_urlopen:
            with self.assertRaisesRegex(RuntimeError, "Free-use status was not independently verified"):
                research.call_model([{"role": "user", "content": "test"}])
            mocked_urlopen.assert_not_called()

    def test_rate_limit_error_is_not_retried_and_metadata_is_saved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            usage_path = Path(temp_dir) / "usage.json"
            error = urllib.error.HTTPError(
                research.API_URL,
                429,
                "Too Many Requests",
                {
                    "x-ratelimit-remaining-requests": "0",
                    "retry-after": "60",
                    "Authorization": "not-loggable",
                },
                None,
            )
            with patch.object(research, "USAGE_REPORT", usage_path), patch.dict(
                os.environ,
                {
                    "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
                    "GENERAL_NVIDIA_GUARD_PASSED": "1",
                    "GENERAL_ALLOW_NVIDIA_CALL": "true",
                    "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
                    "NVIDIA_API_KEY": "test-secret",
                },
            ), patch(
                "general_open_research.urllib.request.urlopen", side_effect=error
            ) as mocked_urlopen:
                with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                    research.call_model([{"role": "user", "content": "test"}])
                self.assertEqual(mocked_urlopen.call_count, 1)
            observed = json.loads(usage_path.read_text(encoding="utf-8"))
            self.assertEqual(observed["http_status"], 429)
            self.assertEqual(observed["automatic_retries"], 0)
            self.assertEqual(
                observed["rate_limit_headers_returned"]["x-ratelimit-remaining-requests"], "0"
            )
            self.assertNotIn("test-secret", usage_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
