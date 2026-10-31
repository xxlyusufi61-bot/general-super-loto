import json
import os
import tempfile
import unittest
import urllib.error
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import general_open_research as research
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
    )


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
        self.assertEqual(
            [row["date"] for row in forward_payload["discovery_history_by_regime"]["R60"]],
            ["2026-10-18", "2026-10-20"],
        )
        self.assertEqual(forward_payload["discovery_history_by_regime"]["R54"], [])
        self.assertEqual(forward_payload["discovery_counts_by_regime"], {"R54": 0, "R60": 2})
        self.assertNotIn("reverse_discovery_history_by_regime", forward_payload)
        self.assertEqual(reverse_payload["discovery_direction"], "reverse")
        self.assertEqual(reverse_payload["history_order"], "newest_to_oldest")
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

    def test_research_payload_uses_only_discovery_rows_for_long_history(self):
        rows = []
        r54_start = date(2020, 1, 2)
        for index in range(31):
            draw_day = (r54_start + timedelta(days=6 * index)).isoformat()
            base = (index * 7) % 54 + 1
            numbers = sorted({((base + offset * 11 - 1) % 54) + 1 for offset in range(6)})
            rows.append({"date": draw_day, "numbers": numbers})
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
        self.assertEqual(forward_payload["discovery_counts_by_regime"]["R54"], 31)
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
            self.assertIn("fixed synthetic r54/r60 contract fixtures", system)
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

    def test_r54_and_r60_are_sent_as_disjoint_regime_partitions(self):
        history = [
            {"date": "2020-07-30", "numbers": [1, 7, 13, 22, 34, 48]},
            {"date": "2020-08-02", "numbers": [2, 8, 14, 23, 35, 49]},
        ]
        messages = research.build_messages("2020-08-05", history)
        payload = json.loads(messages[1]["content"])
        self.assertEqual([row["date"] for row in payload["discovery_history_by_regime"]["R54"]], ["2020-07-30"])
        self.assertEqual([row["date"] for row in payload["discovery_history_by_regime"]["R60"]], ["2020-08-02"])
        self.assertEqual(
            set(row["date"] for row in payload["discovery_history_by_regime"]["R54"])
            & set(row["date"] for row in payload["discovery_history_by_regime"]["R60"]),
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

    def test_invalid_direction_is_rejected_before_payload_creation(self):
        target, train = research.isolate_pre_target(self.rows)
        with self.assertRaisesRegex(ValueError, "must be forward or reverse"):
            research.build_messages(target, train, direction="both")


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
