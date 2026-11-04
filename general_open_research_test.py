import http.client
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import general_open_research as research
from general_data_guard import DataGuardError
import general_sealed_evaluation as sealed
from general_live_consent import CONFIRMATION_PHRASE




def make_directional_report(direction):
    proposal = {
        "hypothesis_id": "HYP-001",
        "discovery_direction": direction,
        "proposed_family": "generic count baseline",
        "hypothesis": "prior counts rank future items",
        "exact_definition": "count all prior appearances",
        "inputs": "historical numbers before target",
        "output_rule": "rank by count descending",
        "parameter_policy": "use fixed constants only",
        "selection_policy": "select on discovery rows only",
        "falsification_criterion": "reject if no stable effect",
        "known_risks": ["multiple testing risk"],
        "evaluation_protocols": [
            "forward_walk_forward",
            "reverse_retrospective_stress_test",
            "time_ordered_cross_validation",
            "uniform_random_6_of_N",
        ],
    }
    spec = {
        "schema_version": 1,
        "score_expression": {
            "op": "aggregate_history_numbers",
            "aggregate": "count",
            "expression": {
                "op": "eq",
                "left": {"op": "var", "name": "value"},
                "right": {"op": "var", "name": "candidate"},
            },
        },
    }
    proposals = {"schema_version": 2, "proposals": [proposal]}
    specs = {"schema_version": 1, "specifications": [{"hypothesis_id": "HYP-001", "execution_spec": spec}]}
    return (
        "<HYPOTHESIS_PROPOSALS_JSON>\n"
        + json.dumps(proposals)
        + "\n</HYPOTHESIS_PROPOSALS_JSON>\n"
        + "<HYPOTHESIS_EXECUTION_SPECS_JSON>\n"
        + json.dumps(specs)
        + "\n</HYPOTHESIS_EXECUTION_SPECS_JSON>\n"
        + research.LESSONS_START
        + json.dumps({
            "schema_version": 1,
            "observations": ["The fixed definition is reproducible."],
            "failed_approaches": ["A moving threshold was not stable."],
            "next_tests": ["Compare against a uniform random baseline."],
        })
        + research.LESSONS_END
    )


def make_fake_connection(response=None):
    connection = MagicMock()
    connection.sock = MagicMock()
    if response is not None:
        connection.getresponse.return_value = response
    return connection


class OpenResearchIsolationTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"date": "2026-10-18", "numbers": [1, 7, 13, 22, 34, 48]},
            {"date": "2026-10-20", "numbers": [2, 8, 14, 23, 35, 49]},
            {"date": "2026-10-22", "numbers": [3, 9, 15, 24, 36, 50]},
        ]

    def test_target_and_future_are_not_in_model_payload(self):
        target, train = research.isolate_pre_target(self.rows)
        forward_messages = research.build_messages(target, train, direction="forward")
        reverse_messages = research.build_messages(target, train, direction="reverse")
        forward_payload = json.loads(forward_messages[1]["content"])
        reverse_payload = json.loads(reverse_messages[1]["content"])
        self.assertEqual(target, "2026-10-22")
        self.assertEqual(forward_payload["discovery_direction"], "forward")
        self.assertEqual(forward_payload["history_order"], "oldest_to_newest")
        self.assertEqual(list(forward_payload["discovery_history_by_regime"]), ["R60"])
        self.assertEqual(
            [row["date"] for row in forward_payload["discovery_history_by_regime"]["R60"]],
            ["2026-10-18", "2026-10-20"],
        )
        self.assertEqual(forward_payload["discovery_counts_by_regime"], {"R60": 2})
        self.assertNotIn("reverse_discovery_history_by_regime", forward_payload)
        self.assertEqual(reverse_payload["discovery_direction"], "reverse")
        self.assertEqual(reverse_payload["history_order"], "newest_to_oldest")
        self.assertEqual(list(reverse_payload["discovery_history_by_regime"]), ["R60"])
        self.assertEqual(
            [row["date"] for row in reverse_payload["discovery_history_by_regime"]["R60"]],
            ["2026-10-20", "2026-10-18"],
        )
        self.assertNotIn("reverse_discovery_history_by_regime", reverse_payload)
        self.assertNotIn("forward_discovery_history_by_regime", reverse_payload)
        for messages in (forward_messages, reverse_messages):
            self.assertNotIn("[3,9,15,24,36,50]", messages[1]["content"])
            self.assertFalse(json.loads(messages[1]["content"])["target_numbers_included"])
            self.assertFalse(json.loads(messages[1]["content"])["post_target_records_included"])
        self.assertNotIn("3,9,15,24,36,50", forward_messages[1]["content"])
        self.assertNotIn("3,9,15,24,36,50", reverse_messages[1]["content"])
    def test_prompt_preserves_neutral_notation_without_teaching_old_formulas(self):
        target, train = research.isolate_pre_target(self.rows)
        messages = research.build_messages(target, train)
        system = messages[0]["content"].lower()
        for label in ("a->b->c", "a->t", "mod5->t"):
            self.assertIn(label, system)
        for legacy_marker in ("v8.6", "v19", "v0.3.1", "t[i] = b[i] - a[i]", "historical formula semantics"):
            self.assertNotIn(legacy_marker, system)
        self.assertIn("pre-general manual/pydroid formulas", system)
        self.assertIn("same-direction general-internal memory", system)
        self.assertNotIn("previous project-memory reports", system)

    def test_research_payload_uses_only_discovery_rows_for_long_history(self):
        rows = []
        r60_start = date(2020, 8, 2)
        for index in range(80):
            draw_day = (r60_start + timedelta(days=2 * index)).isoformat()
            base = (index * 7) % 60 + 1
            numbers = sorted({((base + offset * 11 - 1) % 60) + 1 for offset in range(6)})
            rows.append({"date": draw_day, "numbers": numbers})
        target_numbers = sorted({((5 + offset * 11 - 1) % 60) + 1 for offset in range(6)})
        rows.append({"date": "2021-01-10", "numbers": target_numbers})
        rows.sort(key=lambda row: row["date"])

        target, discovery, plan = research.isolate_discovery_only(rows)
        forward_messages = research.build_messages(target, discovery, direction="forward")
        reverse_messages = research.build_messages(target, discovery, direction="reverse")
        forward_payload = json.loads(forward_messages[1]["content"])
        reverse_payload = json.loads(reverse_messages[1]["content"])
        forward_dates = [row["date"] for row in forward_payload["discovery_history_by_regime"]["R60"]]
        reverse_dates = [row["date"] for row in reverse_payload["discovery_history_by_regime"]["R60"]]
        sealed_start = plan["regimes"]["R60"]["validation_start_date"]
        reverse_end = plan["regimes"]["R60"]["reverse_validation_end_date"]
        self.assertEqual(target, "2021-01-10")
        self.assertEqual(len(forward_dates), 40)
        self.assertEqual(forward_dates, sorted(forward_dates))
        self.assertLess(max(forward_dates), sealed_start)
        self.assertGreater(min(forward_dates), reverse_end)
        self.assertEqual(len(reverse_dates), 40)
        self.assertEqual(reverse_dates, list(reversed(forward_dates)))
        self.assertEqual(forward_payload["discovery_counts_by_regime"]["R60"], 40)
        self.assertNotIn("reverse_discovery_history_by_regime", forward_payload)
        self.assertNotIn("forward_discovery_history_by_regime", reverse_payload)
        self.assertNotIn(sealed_start, forward_messages[1]["content"])
        self.assertNotIn(sealed_start, reverse_messages[1]["content"])
        self.assertNotIn(target, forward_dates)
        self.assertNotIn(target, reverse_dates)
        self.assertFalse(forward_payload["target_numbers_included"])
        self.assertFalse(reverse_payload["target_numbers_included"])
        self.assertFalse(forward_payload["post_target_records_included"])
        self.assertFalse(reverse_payload["post_target_records_included"])

    def test_previous_holdout_dates_are_excluded_from_later_discovery_payload(self):
        rows = []
        r60_start = date(2020, 8, 2)
        for index in range(80):
            draw_day = (r60_start + timedelta(days=2 * index)).isoformat()
            base = (index * 7) % 60 + 1
            numbers = sorted({((base + offset * 11 - 1) % 60) + 1 for offset in range(6)})
            rows.append({"date": draw_day, "numbers": numbers})
        rows.append({
            "date": "2021-01-10",
            "numbers": sorted({((5 + offset * 11 - 1) % 60) + 1 for offset in range(6)}),
        })
        rows.sort(key=lambda row: row["date"])
        first_split = sealed.split_archive(rows)
        first_r60 = first_split["regimes"]["R60"]
        consumed_forward = [row["date"] for row in first_r60["validation_rows"]]
        consumed_reverse = [row["date"] for row in first_r60["reverse_validation_rows"]]
        used_dates = set(consumed_forward + consumed_reverse)

        ledger = {
            "schema_version": 1,
            "purpose": "test append-only validation ledger",
            "entries": [{
                "claim_id": "a" * 64,
                "state": "EVALUATED",
                "validation_dates": consumed_forward,
                "reverse_validation_dates": consumed_reverse,
            }],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            ledger_path = Path(temp_dir) / "ledger.json"
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            target, discovery, plan = research.isolate_discovery_only(
                rows,
                ledger_path=ledger_path,
            )

        r60_discovery_dates = {
            row["date"] for row in discovery
            if date.fromisoformat(row["date"]) >= date(2020, 8, 2)
        }
        original_middle_dates = {
            row["date"] for row in first_r60["discovery_rows"]
        }
        self.assertEqual(target, "2021-01-10")
        self.assertEqual(r60_discovery_dates, original_middle_dates)
        self.assertFalse(used_dates.intersection(r60_discovery_dates))
        self.assertEqual(plan["regimes"]["R60"]["previously_consumed_draw_count"], len(used_dates))

    def test_prompt_requires_bounded_structured_proposal_block(self):
        target, train = research.isolate_pre_target(self.rows)
        messages = research.build_messages(target, train, direction="forward")
        system = messages[0]["content"]
        lowered = system.lower()
        self.assertIn("<HYPOTHESIS_PROPOSALS_JSON>", system)
        self.assertIn("</HYPOTHESIS_PROPOSALS_JSON>", system)
        self.assertIn('"schema_version":2', system)
        self.assertIn("schema_version 1", lowered)
        self.assertIn('"falsification_criterion"', system)
        self.assertIn('"forward_walk_forward"', system)
        self.assertIn("An empty proposals list is an acceptable result.", system)
        self.assertIn("one isolated forward discovery pass", lowered)
        self.assertIn('"discovery_direction":"forward"', system)
        self.assertNotIn('"discovery_direction":"forward|reverse|both"', system)
        self.assertIn("the other direction's data and candidate list are not present", lowered)
        self.assertIn("coordinator will independently parse", lowered)
        for label in ("a->b->c", "a->t", "mod5->t"):
            self.assertIn(label, lowered)
        self.assertNotIn("v8.6", lowered)
        self.assertNotIn("t[i] = b[i] - a[i]", lowered)
        self.assertNotIn("reverse_discovery_history_by_regime", system)
    def test_prompt_describes_restricted_dsl_without_legacy_formula_context(self):
        forward = research.build_messages("2026-10-22", self.rows[:2], direction="forward")[0]["content"].lower()
        reverse = research.build_messages("2026-10-22", self.rows[:2], direction="reverse")[0]["content"].lower()
        for system in (forward, reverse):
            self.assertIn("<hypothesis_execution_specs_json>", system)
            self.assertIn("</hypothesis_execution_specs_json>", system)
            self.assertIn("aggregate_history_numbers", system)
            self.assertIn("const has only op,value", system)
            self.assertIn("binary operators have only op,left,right", system)
            self.assertIn("nested aggregates", system)
            self.assertIn("if a hypothesis cannot be represented", system)
            self.assertIn("zero or one item only", system)
            self.assertIn("complete claim requires one valid spec from each independent direction", system)
            self.assertIn("not approved or canonical", system)
            self.assertIn("fixed synthetic 6-of-60 active-scope fixtures", system)
            self.assertIn("not claim that it was real-archive backtested", system)
            self.assertNotIn("eval(", system)
            self.assertNotIn("exec(", system)
            self.assertNotIn("v19", system)
            self.assertNotIn("t[i] = b[i] - a[i]", system)
        self.assertIn("discovery_direction=forward", forward)
        self.assertIn("discovery_direction=reverse", reverse)
        self.assertNotIn("discovery_direction=reverse", forward)
        self.assertNotIn("discovery_direction=forward", reverse)
    def test_prompt_requires_user_approval_for_rule_changes(self):
        system = research.build_messages("2026-10-22", self.rows[:2])[0]["content"].lower()
        self.assertIn("explicit telegram user approval", system)
        self.assertIn('"change_type":"add|revise|remove"', system)
        self.assertIn("<rule_change_request_json>", system)
        self.assertIn("separate reviewed implementation pull request is required", system)
        self.assertIn("telegram", system)

    def test_pre_scope_rows_are_rejected_before_agent_payload(self):
        history = [
            {"date": "2020-07-30", "numbers": [1, 2, 3, 4, 5, 6]},
            {"date": "2020-08-02", "numbers": [2, 8, 14, 23, 35, 49]},
        ]
        with self.assertRaisesRegex(DataGuardError, "before the active research scope"):
            research.isolate_pre_target(history)

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


    def test_directional_results_are_parsed_independently_and_ids_are_namespaced(self):
        forward = research.parse_directional_result(make_directional_report("forward"), "forward")
        reverse = research.parse_directional_result(make_directional_report("reverse"), "reverse")
        proposals, specs = research.combine_directional_results(forward, reverse)
        self.assertEqual(proposals["status"], "VALID_PROPOSALS")
        self.assertEqual([item["hypothesis_id"] for item in proposals["proposals"]], ["HYP-001", "HYP-501"])
        self.assertEqual([item["discovery_direction"] for item in proposals["proposals"]], ["forward", "reverse"])
        self.assertTrue(proposals["directional_independence"]["separate_model_requests"])
        self.assertFalse(proposals["directional_independence"]["forward_data_sent_to_reverse_request"])
        self.assertFalse(proposals["directional_independence"]["reverse_data_sent_to_forward_request"])
        self.assertEqual(specs["status"], "VALIDATED_SPECS")
        self.assertTrue(specs["dsl_validated"])
        self.assertTrue(specs["synthetic_tests_passed"])
        self.assertEqual([item["hypothesis_id"] for item in specs["specifications"]], ["HYP-001", "HYP-501"])
        self.assertEqual(specs["specifications"][0]["source_hypothesis_id"], "HYP-001")
        self.assertEqual(specs["specifications"][1]["source_hypothesis_id"], "HYP-001")

    def test_direction_mismatch_is_rejected_fail_closed(self):
        result = research.parse_directional_result(make_directional_report("reverse"), "forward")
        self.assertEqual(result["proposal_document"]["status"], "REJECTED_DIRECTION_MISMATCH")
        self.assertEqual(result["proposal_document"]["proposals"], [])
        proposals, specs = research.combine_directional_results(
            result,
            research.parse_directional_result(make_directional_report("reverse"), "reverse"),
        )
        self.assertEqual(proposals["status"], "VALID_PROPOSALS")
        self.assertEqual(specs["status"], "INCOMPLETE_DIRECTIONAL_SPECS")
        self.assertFalse(specs["dsl_validated"])

    def test_directional_memory_context_is_injected_only_into_its_own_request(self):
        target, train = research.isolate_pre_target(self.rows)
        forward_memory = [{
            "direction": "forward",
            "status": "HYPOTHESES_CAPTURED",
            "hypotheses": [{"hypothesis": "FORWARD_ONLY_PRIOR_HYPOTHESIS"}],
        }]
        reverse_memory = [{
            "direction": "reverse",
            "status": "HYPOTHESES_CAPTURED",
            "hypotheses": [{"hypothesis": "REVERSE_ONLY_PRIOR_HYPOTHESIS"}],
        }]
        forward_system = research.build_messages(
            target, train, direction="forward", prior_memory=forward_memory
        )[0]["content"]
        reverse_system = research.build_messages(
            target, train, direction="reverse", prior_memory=reverse_memory
        )[0]["content"]
        self.assertIn("FORWARD_ONLY_PRIOR_HYPOTHESIS", forward_system)
        self.assertNotIn("REVERSE_ONLY_PRIOR_HYPOTHESIS", forward_system)
        self.assertIn("REVERSE_ONLY_PRIOR_HYPOTHESIS", reverse_system)
        self.assertNotIn("FORWARD_ONLY_PRIOR_HYPOTHESIS", reverse_system)
        self.assertIn("<RESEARCH_LEARNING_NOTES_JSON>", forward_system)
        self.assertIn("consumed feedback as fresh independent validation", forward_system)

    def test_cross_direction_memory_is_rejected_before_prompt_creation(self):
        target, train = research.isolate_pre_target(self.rows)
        with self.assertRaisesRegex(ValueError, "another direction"):
            research.build_messages(
                target,
                train,
                direction="forward",
                prior_memory=[{"direction": "reverse", "status": "UNVERIFIED"}],
            )

    def test_invalid_direction_is_rejected_before_payload_creation(self):
        target, train = research.isolate_pre_target(self.rows)
        with self.assertRaisesRegex(ValueError, "must be forward or reverse"):
            research.build_messages(target, train, direction="both")


class NvidiaUsageObservationTests(unittest.TestCase):
    def setUp(self):
        research._NVIDIA_REQUESTS_ATTEMPTED = 0
        research._NVIDIA_REQUEST_LABELS_ATTEMPTED.clear()

    @staticmethod
    def successful_response():
        response = MagicMock()
        response.status = 200
        response.headers = {"x-request-id": "synthetic-request"}
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.readline.side_effect = [
            b'data: {"choices":[{"delta":{"reasoning_content":"hidden reasoning"}}]}\n',
            b'\n',
            b'data: {"choices":[{"delta":{"content":"synthetic "}}]}\n',
            b'\n',
            b'data: {"choices":[{"delta":{"content":"response"},"finish_reason":"stop"}]}\n',
            b'\n',
            b'data: {"choices":[],"usage":{"total_tokens":1}}\n',
            b'\n',
            b'data: [DONE]\n',
            b'\n',
            b"",
        ]
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        return response

    def test_stream_parser_joins_content_and_ignores_reasoning(self):
        response = MagicMock()
        response.readline.side_effect = [
            b": keep-alive\n",
            b'data: {"choices":[{"delta":{"reasoning_content":"not final text"}}]}\n',
            b"\n",
            b'data: {"choices":[{"delta":{"content":"GENERAL "}}]}\n',
            b"\n",
            b'data: {"choices":[{"delta":{"content":"stream"},"finish_reason":"stop"}]}\n',
            b"\n",
            b'data: {"choices":[],"usage":{"total_tokens":12}}\n',
            b"\n",
            b"data: [DONE]\n",
            b"\n",
            b"",
        ]
        payload, metadata = research._parse_streamed_chat_completion(response)
        self.assertEqual(payload["choices"][0]["message"]["content"], "GENERAL stream")
        self.assertEqual(payload["choices"][0]["finish_reason"], "stop")
        self.assertEqual(payload["usage"]["total_tokens"], 12)
        self.assertEqual(metadata["stream_event_count"], 4)
        self.assertTrue(metadata["stream_completed"])

    def test_stream_parser_rejects_missing_done_marker(self):
        response = MagicMock()
        response.readline.side_effect = [
            b'data: {"choices":[{"delta":{"content":"partial"}}]}\n',
            b"\n",
            b"",
        ]
        with self.assertRaisesRegex(RuntimeError, "before the \\[DONE\\] marker"):
            research._parse_streamed_chat_completion(response)

    def test_stream_parser_rejects_token_limit_truncation(self):
        response = MagicMock()
        response.readline.side_effect = [
            b'data: {"choices":[{"delta":{"content":"partial answer"},"finish_reason":"length"}]}\n',
            b"\n",
            b'data: {"choices":[],"usage":{"prompt_tokens":120,"completion_tokens":1800,"total_tokens":1920}}\n',
            b"\n",
            b"data: [DONE]\n",
            b"\n",
            b"",
        ]
        with self.assertRaisesRegex(
            research.IncompleteModelResponseError, "reached max_tokens"
        ) as raised:
            research._parse_streamed_chat_completion(response)
        self.assertEqual(raised.exception.stream_event_count, 2)
        self.assertTrue(raised.exception.stream_completed)
        self.assertEqual(raised.exception.stream_finish_reason, "length")
        self.assertEqual(raised.exception.usage["total_tokens"], 1920)
        self.assertFalse(hasattr(raised.exception, "partial_content"))
        self.assertNotIn("partial answer", repr(raised.exception))

    def test_stream_parser_rejects_reasoning_only_output(self):
        response = MagicMock()
        response.readline.side_effect = [
            b'data: {"choices":[{"delta":{"reasoning_content":"private"}}]}\n',
            b"\n",
            b"data: [DONE]\n",
            b"\n",
            b"",
        ]
        with self.assertRaisesRegex(RuntimeError, "reasoning-only output is not accepted"):
            research._parse_streamed_chat_completion(response)


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
            "general_open_research._create_nvidia_connection"
        ) as mocked_connection_factory:
            with self.assertRaisesRegex(RuntimeError, "not explicitly approved"):
                research.call_model([])
            mocked_connection_factory.assert_not_called()

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
        ), patch("general_open_research._create_nvidia_connection") as mocked_connection_factory:
            with self.assertRaisesRegex(RuntimeError, "Global NVIDIA safety-lock preflight was not confirmed"):
                research.call_model([{"role": "user", "content": "test"}])
            mocked_connection_factory.assert_not_called()

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
        ), patch("general_open_research._create_nvidia_connection") as mocked_connection_factory:
            with self.assertRaisesRegex(RuntimeError, "Free-use status was not independently verified"):
                research.call_model([{"role": "user", "content": "test"}])
            mocked_connection_factory.assert_not_called()

    def test_two_directional_usage_observations_are_separate_and_summarized(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            usage_path = Path(temp_dir) / "usage.json"
            forward_observation = research.build_usage_observation(
                200,
                {"x-ratelimit-remaining-requests": "8"},
                {"usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130}},
            )
            reverse_observation = research.build_usage_observation(
                200,
                {"x-ratelimit-remaining-requests": "7"},
                {"usage": {"prompt_tokens": 110, "completion_tokens": 35, "total_tokens": 145}},
            )
            with patch.object(research, "USAGE_REPORT", usage_path):
                research.save_usage_observation(forward_observation, request_label="forward")
                research.save_usage_observation(reverse_observation, request_label="reverse")
                summary = research.write_combined_usage_summary()
            self.assertTrue((Path(temp_dir) / "usage_forward.json").is_file())
            self.assertTrue((Path(temp_dir) / "usage_reverse.json").is_file())
            self.assertEqual(summary["inference_requests_made_by_this_run"], 2)
            self.assertEqual(summary["automatic_retries"], 0)
            self.assertEqual([item["direction"] for item in summary["requests"]], ["forward", "reverse"])
            self.assertEqual(
                [item["observation"]["token_usage_returned_by_provider"]["total_tokens"]
                 for item in summary["requests"]],
                [130, 145],
            )
            self.assertEqual(summary["account_level_quota_status"], "UNKNOWN")
            self.assertEqual(summary["billing_or_paid_entitlement_status"], "NOT_VERIFIED_BY_THIS_RUN")

    def test_runner_enforces_two_total_requests_and_direction_order(self):
        env = {
            "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
            "GENERAL_NVIDIA_GUARD_PASSED": "1",
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }
        connections = [
            make_fake_connection(self.successful_response()),
            make_fake_connection(self.successful_response()),
        ]
        try:
            with patch.dict(os.environ, env, clear=True), patch.object(
                research, "save_usage_observation"
            ), patch.object(research, "reserve_nvidia_request") as mocked_reserve, patch(
                "general_open_research._create_nvidia_connection",
                side_effect=connections,
            ) as mocked_connection_factory:
                forward = research.call_model(
                    [{"role": "user", "content": "forward"}],
                    request_label="forward",
                )
                reverse = research.call_model(
                    [{"role": "user", "content": "reverse"}],
                    request_label="reverse",
                )
                self.assertEqual(forward, "synthetic response")
                self.assertEqual(reverse, "synthetic response")
                sent_body = connections[0].endheaders.call_args.args[0]
                sent_payload = json.loads(sent_body.decode("utf-8"))
                self.assertTrue(sent_payload["stream"])
                self.assertEqual(sent_payload["reasoning_effort"], "low")
                self.assertEqual(
                    sent_payload["chat_template_kwargs"],
                    {"clear_thinking": True},
                )
                sent_headers = connections[0].putheader.call_args_list
                self.assertIn(("Accept", "text/event-stream"), [call.args for call in sent_headers])
                with self.assertRaisesRegex(RuntimeError, "request cap reached"):
                    research.call_model([{"role": "user", "content": "third"}])
                self.assertEqual(mocked_connection_factory.call_count, 2)
                self.assertEqual(
                    [call.args[0] for call in mocked_connection_factory.call_args_list],
                    [research.DEFAULT_CONNECT_TIMEOUT_SECONDS] * 2,
                )
                self.assertEqual(mocked_reserve.call_count, 2)
                self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 2)
        finally:
            Path("/tmp/general_nvidia_api_attempted").unlink(missing_ok=True)
            Path("/tmp/general_nvidia_api_succeeded").unlink(missing_ok=True)

    def test_runner_rejects_reverse_before_forward_and_duplicate_direction(self):
        env = {
            "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
            "GENERAL_NVIDIA_GUARD_PASSED": "1",
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "general_open_research._create_nvidia_connection"
        ) as mocked_connection_factory:
            with self.assertRaisesRegex(RuntimeError, "forward-then-reverse"):
                research.call_model(
                    [{"role": "user", "content": "reverse"}],
                    request_label="reverse",
                )
            mocked_connection_factory.assert_not_called()

    def test_https_connection_uses_standard_http_connect_proxy_without_logging_credentials(self):
        with patch.object(
            research.urllib.request,
            "getproxies",
            return_value={"https": "http://proxy-user:proxy-pass@proxy.example:8080"},
        ), patch.object(
            research.urllib.request, "proxy_bypass", return_value=False
        ), patch(
            "general_open_research.http.client.HTTPSConnection"
        ) as https_connection:
            connection = https_connection.return_value
            result = research._create_nvidia_connection(19)

        self.assertIs(result, connection)
        https_connection.assert_called_once_with(
            "proxy.example",
            8080,
            timeout=19,
            context=unittest.mock.ANY,
        )
        connection.set_tunnel.assert_called_once_with(
            "integrate.api.nvidia.com",
            443,
            headers={"Proxy-Authorization": "Basic cHJveHktdXNlcjpwcm94eS1wYXNz"},
        )

    def test_https_connection_honors_no_proxy_bypass(self):
        with patch.object(
            research.urllib.request,
            "getproxies",
            return_value={"https": "http://proxy.example:8080"},
        ), patch.object(
            research.urllib.request, "proxy_bypass", return_value=True
        ), patch(
            "general_open_research.http.client.HTTPSConnection"
        ) as https_connection:
            connection = https_connection.return_value
            result = research._create_nvidia_connection(11)

        self.assertIs(result, connection)
        https_connection.assert_called_once_with(
            "integrate.api.nvidia.com",
            443,
            timeout=11,
            context=unittest.mock.ANY,
        )
        connection.set_tunnel.assert_not_called()

    def test_unsupported_https_proxy_fails_closed_before_connection(self):
        with patch.object(
            research.urllib.request,
            "getproxies",
            return_value={"https": "https://proxy.example:8443"},
        ), patch.object(
            research.urllib.request, "proxy_bypass", return_value=False
        ), patch(
            "general_open_research.http.client.HTTPSConnection"
        ) as https_connection:
            with self.assertRaisesRegex(RuntimeError, "HTTP CONNECT endpoint"):
                research._create_nvidia_connection(11)
        https_connection.assert_not_called()


    def test_connection_timeout_is_distinguished_from_header_timeout(self):
        env = {
            "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
            "GENERAL_NVIDIA_GUARD_PASSED": "1",
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }
        saved = []
        connection = make_fake_connection()
        connection.connect.side_effect = TimeoutError("synthetic TCP/TLS timeout")
        try:
            with patch.dict(os.environ, env, clear=True), patch.object(
                research, "reserve_nvidia_request"
            ), patch.object(
                research, "save_usage_observation",
                side_effect=lambda observation, **kwargs: saved.append(observation),
            ), patch(
                "general_open_research._create_nvidia_connection",
                return_value=connection,
            ) as mocked_connection_factory:
                with self.assertRaisesRegex(RuntimeError, "during tcp_tls_establishment"):
                    research.call_model(
                        [{"role": "user", "content": "test"}],
                        request_label="forward",
                    )
                self.assertEqual(mocked_connection_factory.call_count, 1)
                connection.putrequest.assert_not_called()
                connection.endheaders.assert_not_called()
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["failure_stage"], "tcp_tls_establishment")
            self.assertEqual(saved[0]["request_delivery_state"], "not_sent")
            self.assertEqual(saved[0]["automatic_retries"], 0)
        finally:
            Path("/tmp/general_nvidia_api_attempted").unlink(missing_ok=True)
            Path("/tmp/general_nvidia_api_succeeded").unlink(missing_ok=True)

    def test_request_send_timeout_is_recorded_without_retry_or_false_delivery_claim(self):
        env = {
            "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
            "GENERAL_NVIDIA_GUARD_PASSED": "1",
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }
        saved = []
        connection = make_fake_connection()
        connection.endheaders.side_effect = TimeoutError("synthetic request-send timeout")
        system_text = "system instructions must not be persisted"
        user_text = "user prompt content must not be persisted"
        try:
            with patch.dict(os.environ, env, clear=True), patch.object(
                research, "reserve_nvidia_request"
            ), patch.object(
                research,
                "save_usage_observation",
                side_effect=lambda observation, **kwargs: saved.append(observation),
            ), patch(
                "general_open_research._create_nvidia_connection",
                return_value=connection,
            ) as mocked_connection_factory:
                with self.assertRaisesRegex(RuntimeError, "during send_request"):
                    research.call_model(
                        [
                            {"role": "system", "content": system_text},
                            {"role": "user", "content": user_text},
                        ],
                        request_label="forward",
                    )
                self.assertEqual(mocked_connection_factory.call_count, 1)
                connection.endheaders.assert_called_once()
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["failure_stage"], "send_request")
            self.assertEqual(saved[0]["request_delivery_state"], "unknown_send_in_progress")
            self.assertEqual(saved[0]["network_error_type"], "TimeoutError")
            self.assertEqual(saved[0]["automatic_retries"], 0)
            self.assertGreater(saved[0]["request_body_bytes"], 0)
            self.assertEqual(
                saved[0]["system_prompt_bytes"],
                len(system_text.encode("utf-8")),
            )
            self.assertEqual(
                saved[0]["user_prompt_bytes"],
                len(user_text.encode("utf-8")),
            )
            serialized = json.dumps(saved[0])
            self.assertNotIn(system_text, serialized)
            self.assertNotIn(user_text, serialized)
            self.assertNotIn("test-secret", serialized)
        finally:
            Path("/tmp/general_nvidia_api_attempted").unlink(missing_ok=True)
            Path("/tmp/general_nvidia_api_succeeded").unlink(missing_ok=True)

    def test_remote_disconnected_before_headers_is_recorded_and_never_retried(self):
        env = {
            "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
            "GENERAL_NVIDIA_GUARD_PASSED": "1",
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }
        saved = []
        connection = make_fake_connection()
        connection.getresponse.side_effect = http.client.RemoteDisconnected(
            "synthetic remote closed before HTTP response"
        )
        try:
            with patch.dict(os.environ, env, clear=True), patch.object(
                research, "reserve_nvidia_request"
            ) as reserve, patch.object(
                research,
                "save_usage_observation",
                side_effect=lambda observation, **kwargs: saved.append(observation),
            ), patch(
                "general_open_research._create_nvidia_connection",
                return_value=connection,
            ) as connection_factory:
                with self.assertRaisesRegex(RuntimeError, "RemoteDisconnected"):
                    research.call_model(
                        [{"role": "user", "content": "synthetic header disconnect"}],
                        request_label="forward",
                    )
                self.assertEqual(connection_factory.call_count, 1)
                connection.endheaders.assert_called_once()
                connection.getresponse.assert_called_once()
                reserve.assert_called_once_with(
                    "open-research-forward",
                    diagnostic_exception=False,
                )
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["failure_stage"], "wait_for_headers")
            self.assertEqual(
                saved[0]["network_error_type"], "RemoteDisconnected"
            )
            self.assertEqual(
                saved[0]["request_delivery_state"],
                "sent_to_socket_provider_receipt_unconfirmed",
            )
            self.assertIsNone(saved[0]["http_status"])
            self.assertEqual(saved[0]["automatic_retries"], 0)
            self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 1)
        finally:
            Path("/tmp/general_nvidia_api_attempted").unlink(missing_ok=True)
            Path("/tmp/general_nvidia_api_succeeded").unlink(missing_ok=True)


    def test_stream_timeout_is_diagnosed_and_never_retried(self):
        env = {
            "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
            "GENERAL_NVIDIA_GUARD_PASSED": "1",
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }
        saved = []
        try:
            with patch.dict(os.environ, env, clear=True), patch.object(
                research, "reserve_nvidia_request"
            ), patch.object(
                research,
                "save_usage_observation",
                side_effect=lambda observation, **kwargs: saved.append(observation),
            ), patch(
                "general_open_research._create_nvidia_connection",
                return_value=make_fake_connection(),
            ) as mocked_connection_factory:
                mocked_connection_factory.return_value.getresponse.side_effect = TimeoutError("synthetic header timeout")
                with self.assertRaisesRegex(RuntimeError, "during wait_for_headers"):
                    research.call_model(
                        [{"role": "user", "content": "test"}],
                        request_label="forward",
                    )
                self.assertEqual(mocked_connection_factory.call_count, 1)
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["response_mode"], "sse")
            self.assertEqual(saved[0]["failure_stage"], "wait_for_headers")
            self.assertEqual(saved[0]["request_delivery_state"], "sent_to_socket_provider_receipt_unconfirmed")
            self.assertEqual(saved[0]["automatic_retries"], 0)
            self.assertEqual(saved[0]["network_error_type"], "TimeoutError")
            self.assertEqual(saved[0]["automatic_retries"], 0)
            self.assertIsNone(saved[0]["http_status"])
            self.assertEqual(
                saved[0]["request_timeout_seconds"],
                research.DEFAULT_REQUEST_TIMEOUT_SECONDS,
            )
        finally:
            Path("/tmp/general_nvidia_api_attempted").unlink(missing_ok=True)
            Path("/tmp/general_nvidia_api_succeeded").unlink(missing_ok=True)

    def test_token_limit_failure_is_recorded_as_incomplete_model_response(self):
        env = {
            "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
            "GENERAL_NVIDIA_GUARD_PASSED": "1",
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }
        response = MagicMock()
        response.status = 200
        response.headers = {"x-request-id": "synthetic-request"}
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.readline.side_effect = [
            b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"length"}]}\n',
            b"\n",
            b'data: {"choices":[],"usage":{"prompt_tokens":333,"completion_tokens":1800,"total_tokens":2133}}\n',
            b"\n",
            b"data: [DONE]\n",
            b"\n",
            b"",
        ]
        saved = []
        try:
            with patch.dict(os.environ, env, clear=True), patch.object(
                research, "reserve_nvidia_request"
            ), patch.object(
                research,
                "save_usage_observation",
                side_effect=lambda observation, **kwargs: saved.append(observation),
            ), patch(
                "general_open_research._create_nvidia_connection",
                return_value=make_fake_connection(response),
            ) as mocked_connection_factory:
                with self.assertRaisesRegex(RuntimeError, "reached max_tokens"):
                    research.call_model(
                        [{"role": "user", "content": "test"}],
                        request_label="forward",
                    )
                self.assertEqual(mocked_connection_factory.call_count, 1)
                sent_body = json.loads(mocked_connection_factory.return_value.endheaders.call_args.args[0].decode("utf-8"))
                self.assertEqual(sent_body["max_tokens"], research.DEFAULT_MAX_COMPLETION_TOKENS)
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["failure_stage"], "model_finish")
            self.assertEqual(saved[0]["network_error_type"], "IncompleteModelResponse")
            self.assertEqual(saved[0]["automatic_retries"], 0)
            self.assertEqual(saved[0]["stream_event_count"], 2)
            self.assertTrue(saved[0]["stream_completed"])
            self.assertEqual(saved[0]["stream_finish_reason"], "length")
            self.assertFalse(saved[0]["response_complete"])
            self.assertEqual(saved[0]["max_tokens_requested"], 1800)
            self.assertEqual(
                saved[0]["token_usage_returned_by_provider"]["total_tokens"],
                2133,
            )
            self.assertNotIn("partial", json.dumps(saved[0]))
        finally:
            Path("/tmp/general_nvidia_api_attempted").unlink(missing_ok=True)
            Path("/tmp/general_nvidia_api_succeeded").unlink(missing_ok=True)

    def test_response_socket_timeout_survives_connection_close_handoff(self):
        class CloseAfterResponseHandler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                body_length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(body_length)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.send_header("X-RateLimit-Remaining-Requests", "39")
                self.send_header("X-Request-ID", "local-sse-close-test")
                self.send_header("Authorization", "synthetic-secret-header")
                self.send_header("Set-Cookie", "synthetic-secret-cookie")
                self.end_headers()
                self.wfile.write(
                    b'data: {"choices":[{"delta":{"content":"socket handoff works"},'
                    b'"finish_reason":"stop"}]}\n\n'
                )
                self.wfile.write(b'data: {"choices":[],"usage":{"total_tokens":7}}\n\n')
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                self.close_connection = True

            def log_message(self, format, *args):
                return

        server = HTTPServer(("127.0.0.1", 0), CloseAfterResponseHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        connections = []
        saved = []
        original_parser = research._parse_streamed_chat_completion

        def connection_factory(timeout_seconds):
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_port,
                timeout=timeout_seconds,
            )
            connections.append(connection)
            return connection

        def parse_after_connection_handoff(response, *, before_read=None):
            self.assertEqual(len(connections), 1)
            self.assertIsNone(connections[0].sock)
            self.assertFalse(response.isclosed())
            return original_parser(response, before_read=before_read)

        env = {
            "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
            "GENERAL_NVIDIA_GUARD_PASSED": "1",
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }
        try:
            with patch.dict(os.environ, env, clear=True), patch.object(
                research, "reserve_nvidia_request"
            ), patch.object(
                research,
                "save_usage_observation",
                side_effect=lambda observation, **kwargs: saved.append(observation),
            ), patch(
                "general_open_research._create_nvidia_connection",
                side_effect=connection_factory,
            ), patch(
                "general_open_research._parse_streamed_chat_completion",
                side_effect=parse_after_connection_handoff,
            ):
                answer = research.call_model(
                    [{"role": "user", "content": "local SSE socket handoff test"}],
                    request_label="forward",
                )

            self.assertEqual(answer, "socket handoff works")
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["http_status"], 200)
            self.assertEqual(
                saved[0]["rate_limit_headers_returned"]["X-RateLimit-Remaining-Requests"],
                "39",
            )
            self.assertEqual(
                saved[0]["rate_limit_headers_returned"]["X-Request-ID"],
                "local-sse-close-test",
            )
            serialized = json.dumps(saved[0])
            self.assertNotIn("synthetic-secret-header", serialized)
            self.assertNotIn("synthetic-secret-cookie", serialized)
            self.assertNotIn("test-secret", serialized)
        finally:
            for marker in (
                Path("/tmp/general_nvidia_api_attempted"),
                Path("/tmp/general_nvidia_api_succeeded"),
            ):
                marker.unlink(missing_ok=True)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=2)

    def test_stream_network_failure_preserves_only_safe_response_metadata(self):
        env = {
            "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
            "GENERAL_NVIDIA_GUARD_PASSED": "1",
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }
        response = MagicMock()
        response.status = 200
        response.headers = {
            "x-ratelimit-remaining-requests": "38",
            "x-request-id": "synthetic-stream-failure",
            "Authorization": "synthetic-secret-header",
            "Set-Cookie": "synthetic-secret-cookie",
        }
        response.readline.side_effect = ConnectionResetError("synthetic SSE connection reset")
        saved = []
        connection = make_fake_connection(response)
        try:
            with patch.dict(os.environ, env, clear=True), patch.object(
                research, "reserve_nvidia_request"
            ), patch.object(
                research,
                "save_usage_observation",
                side_effect=lambda observation, **kwargs: saved.append(observation),
            ), patch(
                "general_open_research._create_nvidia_connection",
                return_value=connection,
            ) as connection_factory:
                with self.assertRaisesRegex(RuntimeError, "during read_stream"):
                    research.call_model(
                        [{"role": "user", "content": "metadata after HTTP response"}],
                        request_label="forward",
                    )

            self.assertEqual(connection_factory.call_count, 1)
            self.assertEqual(connection.getresponse.call_count, 1)
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["http_status"], 200)
            self.assertEqual(saved[0]["failure_stage"], "read_stream")
            self.assertEqual(
                saved[0]["network_error_type"], "ConnectionResetError"
            )
            self.assertEqual(saved[0]["request_delivery_state"], "response_received")
            self.assertEqual(
                saved[0]["rate_limit_headers_returned"]["x-ratelimit-remaining-requests"],
                "38",
            )
            self.assertEqual(
                saved[0]["rate_limit_headers_returned"]["x-request-id"],
                "synthetic-stream-failure",
            )
            serialized = json.dumps(saved[0])
            self.assertNotIn("synthetic-secret-header", serialized)
            self.assertNotIn("synthetic-secret-cookie", serialized)
            self.assertNotIn("test-secret", serialized)
            self.assertEqual(saved[0]["automatic_retries"], 0)
        finally:
            Path("/tmp/general_nvidia_api_attempted").unlink(missing_ok=True)
            Path("/tmp/general_nvidia_api_succeeded").unlink(missing_ok=True)

    def test_stream_read_timeout_is_distinguished_from_connection_timeout(self):
        env = {
            "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
            "GENERAL_NVIDIA_GUARD_PASSED": "1",
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }
        response = self.successful_response()
        response.readline.side_effect = [
            b'data: {"choices":[{"delta":{"content":"partial"}}]}\n',
            b"\n",
            TimeoutError("synthetic idle timeout"),
        ]
        saved = []
        try:
            with patch.dict(os.environ, env, clear=True), patch.object(
                research, "reserve_nvidia_request"
            ), patch.object(
                research,
                "save_usage_observation",
                side_effect=lambda observation, **kwargs: saved.append(observation),
            ), patch(
                "general_open_research._create_nvidia_connection",
                return_value=make_fake_connection(response),
            ) as mocked_connection_factory:
                with self.assertRaisesRegex(RuntimeError, "during read_stream"):
                    research.call_model(
                        [{"role": "user", "content": "test"}],
                        request_label="forward",
                    )
                self.assertEqual(mocked_connection_factory.call_count, 1)
            self.assertEqual(len(saved), 1)
            self.assertEqual(saved[0]["failure_stage"], "read_stream")
            self.assertEqual(saved[0]["network_error_type"], "TimeoutError")
            self.assertEqual(saved[0]["automatic_retries"], 0)
        finally:
            Path("/tmp/general_nvidia_api_attempted").unlink(missing_ok=True)
            Path("/tmp/general_nvidia_api_succeeded").unlink(missing_ok=True)

    def test_rate_limit_error_is_not_retried_and_metadata_is_saved(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            usage_path = Path(temp_dir) / "usage.json"
            response = MagicMock()
            response.status = 429
            response.headers = {
                "x-ratelimit-remaining-requests": "0",
                "retry-after": "60",
                "Authorization": "not-loggable",
            }
            connection = make_fake_connection(response)
            with patch.object(research, "USAGE_REPORT", usage_path), patch.dict(
                os.environ,
                {
                    "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
                    "GENERAL_NVIDIA_GUARD_PASSED": "1",
                    "GENERAL_ALLOW_NVIDIA_CALL": "true",
                    "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
                    "NVIDIA_API_KEY": "test-secret",
                },
            ), patch.object(research, "reserve_nvidia_request"), patch(
                "general_open_research._create_nvidia_connection", return_value=connection
            ) as mocked_connection_factory:
                with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                    research.call_model([{"role": "user", "content": "test"}])
                self.assertEqual(mocked_connection_factory.call_count, 1)
            observed = json.loads(usage_path.read_text(encoding="utf-8"))
            self.assertEqual(observed["http_status"], 429)
            self.assertEqual(observed["automatic_retries"], 0)
            self.assertEqual(
                observed["rate_limit_headers_returned"]["x-ratelimit-remaining-requests"], "0"
            )
            self.assertNotIn("test-secret", usage_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
