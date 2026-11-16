import json
import os
import sys
import tempfile
import types
import unittest
from importlib.util import find_spec
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import general_open_research as research
from general_data_guard import DataGuardError
import general_sealed_evaluation as sealed
from general_live_consent import (
    CONFIRMATION_PHRASE,
    SINGLE_REQUEST_CONFIRMATION_PHRASE,
)


def make_directional_report(direction, *, alter_spec=False):
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
    proposal_formal_spec = json.loads(json.dumps(spec))
    if alter_spec:
        spec["score_expression"]["expression"]["right"]["name"] = "draw_age"
    proposal = {
        "hypothesis_id": "HYP-001",
        "discovery_direction": direction,
        "proposed_family": "generic count baseline",
        "research_rationale": "Historical counts may be a useful baseline to challenge against a null model.",
        "known_risks": ["multiple testing risk"],
        "formal_execution_spec": proposal_formal_spec,
    }
    proposals = {"schema_version": 3, "proposals": [proposal]}
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

def make_directional_v2_report(direction):
    slot = "A" if direction == "forward" else "C"
    spec = {
        "schema_version": 2,
        "direction": direction,
        "score_expression": {
            "op": "bool_to_number",
            "arg": {
                "op": "contains_candidate",
                "state": {"op": "state_ref", "slot": slot},
            },
        },
    }
    proposal = {
        "hypothesis_id": "HYP-001",
        "discovery_direction": direction,
        "proposed_family": "direction-sealed state membership",
        "research_rationale": "A direction-isolated state relation can be evaluated without exposing the target.",
        "known_risks": ["Multiple testing and regime drift."],
        "formal_execution_spec": spec,
    }
    return (
        "<HYPOTHESIS_PROPOSALS_JSON>\n"
        + json.dumps({"schema_version": 3, "proposals": [proposal]})
        + "\n</HYPOTHESIS_PROPOSALS_JSON>\n"
        + "<HYPOTHESIS_EXECUTION_SPECS_JSON>\n"
        + json.dumps({"schema_version": 1, "specifications": [
            {"hypothesis_id": "HYP-001", "execution_spec": spec}
        ]})
        + "\n</HYPOTHESIS_EXECUTION_SPECS_JSON>"
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
        self.assertIn('"schema_version":3', system)
        self.assertIn('"formal_execution_spec":null', system)
        self.assertIn("coordinator assigns the four mandatory evaluation protocols", system.lower())
        self.assertIn("all mathematical explanation fields", system.lower())
        self.assertIn('"research_rationale"', system)
        self.assertNotIn('"evaluation_protocols"', system)
        self.assertNotIn('"exact_definition"', system)
        self.assertIn("zero or one proposal only", system.lower())
        self.assertIn("whole-block fail-closed contract", system.lower())
        self.assertIn("schema_version 1", lowered)
        self.assertIn("falsification criterion", system.lower())
        self.assertNotIn('"forward_walk_forward"', system)
        self.assertIn("four mandatory evaluation protocols", system.lower())
        self.assertIn("or return an empty list", system)
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
            self.assertIn("execution_spec must use schema_version 2", system)
            self.assertIn("direction must equal this isolated request", system)
            self.assertIn("state_ref", system)
            self.assertIn("set_union", system)
            self.assertIn("set_intersection", system)
            self.assertIn("set_difference", system)
            self.assertIn("set_symmetric_difference", system)
            self.assertIn("no modulo operator or built-in mod5 projection", system)
            self.assertIn("no built-in residue convention", system)
            self.assertIn("64 nodes and depth 12", system)
            self.assertIn("t is not a dsl operator", system)
            self.assertIn("if an exact hypothesis cannot be represented faithfully", system)
            self.assertIn("zero or one item only", system)
            self.assertIn("complete claim requires one valid spec from each independent direction", system)
            self.assertIn("not approved or canonical", system)
            self.assertIn("fixed synthetic 6-of-60 active-scope fixtures", system)
            self.assertIn("do not claim it was real-archive backtested", system)
            self.assertNotIn("eval(", system)
            self.assertNotIn("exec(", system)
            self.assertNotIn("mod5_project", system)
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

    def test_directional_v2_specs_survive_proposal_parser_and_sealed_contract(self):
        forward = research.parse_directional_result(
            make_directional_v2_report("forward"), "forward"
        )
        reverse = research.parse_directional_result(
            make_directional_v2_report("reverse"), "reverse"
        )
        self.assertEqual(forward["proposal_document"]["status"], "VALID_PROPOSALS")
        self.assertEqual(reverse["proposal_document"]["status"], "VALID_PROPOSALS")
        self.assertEqual(
            forward["execution_spec_document"]["specifications"][0]["dsl_version"], 2
        )
        self.assertEqual(
            reverse["execution_spec_document"]["specifications"][0]["dsl_version"], 2
        )
        proposals, specs = research.combine_directional_results(forward, reverse)
        self.assertEqual(proposals["status"], "VALID_PROPOSALS")
        self.assertEqual(specs["status"], "VALIDATED_SPECS")
        self.assertEqual(
            [item["execution_spec"]["direction"] for item in specs["specifications"]],
            ["forward", "reverse"],
        )
        self.assertTrue(specs["synthetic_tests_passed"])
        self.assertFalse(specs["execution_ready"])
        self.assertFalse(specs["method_registry_modified"])

    def test_directional_execution_spec_must_match_authoritative_formal_definition(self):
        result = research.parse_directional_result(
            make_directional_report("forward", alter_spec=True), "forward"
        )
        self.assertEqual(result["proposal_document"]["status"], "VALID_PROPOSALS")
        self.assertEqual(result["execution_spec_document"]["status"], "REJECTED")
        self.assertEqual(result["execution_spec_document"]["specifications"], [])
        self.assertIn(
            "exactly match the proposal's formal mathematical definition",
            result["execution_spec_document"]["rejection_reasons"][0],
        )
        proposals, specs = research.combine_directional_results(
            result,
            research.parse_directional_result(make_directional_report("reverse"), "reverse"),
        )
        self.assertEqual(proposals["status"], "VALID_PROPOSALS")
        self.assertEqual(specs["status"], "INCOMPLETE_DIRECTIONAL_SPECS")
        self.assertFalse(specs["dsl_validated"])

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

    def test_directional_memory_context_is_user_data_not_system_instructions(self):
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
        forward_messages = research.build_messages(
            target, train, direction="forward", prior_memory=forward_memory
        )
        reverse_messages = research.build_messages(
            target, train, direction="reverse", prior_memory=reverse_memory
        )
        forward_system = forward_messages[0]["content"]
        reverse_system = reverse_messages[0]["content"]
        forward_payload = json.loads(forward_messages[1]["content"])
        reverse_payload = json.loads(reverse_messages[1]["content"])

        self.assertNotIn("FORWARD_ONLY_PRIOR_HYPOTHESIS", forward_system)
        self.assertNotIn("REVERSE_ONLY_PRIOR_HYPOTHESIS", forward_system)
        self.assertNotIn("FORWARD_ONLY_PRIOR_HYPOTHESIS", reverse_system)
        self.assertNotIn("REVERSE_ONLY_PRIOR_HYPOTHESIS", reverse_system)
        self.assertEqual(
            forward_payload["prior_general_research_memory_untrusted"], forward_memory
        )
        self.assertEqual(
            reverse_payload["prior_general_research_memory_untrusted"], reverse_memory
        )
        self.assertNotIn("REVERSE_ONLY_PRIOR_HYPOTHESIS", json.dumps(forward_payload))
        self.assertNotIn("FORWARD_ONLY_PRIOR_HYPOTHESIS", json.dumps(reverse_payload))
        self.assertIn("untrusted quoted data", forward_system)
        self.assertIn("<RESEARCH_LEARNING_NOTES_JSON>", forward_system)
        self.assertIn("consumed feedback as fresh independent validation", forward_system)
        self.assertIn("each candidate is a complete validated record", forward_system)
        compact_memory = [{
            "direction": "forward",
            "source": "GENERAL/offline-symbolic-discovery",
            "candidate_transfer_mode": "FULL_VALIDATED_RECORDS",
            "candidates_in_memory": 1,
            "candidates_in_context": 1,
            "candidate_context_truncated": False,
            "candidates": [{
                "hypothesis_id": "HYP-001",
                "family": "synthetic family",
                "template_id": "synthetic-template",
                "proposal": {"hypothesis_id": "HYP-001", "exact_definition": "synthetic definition"},
                "execution_spec": {"schema_version": 1},
                "specification_sha256": "a" * 64,
                "training_signal": {},
                "synthetic_tests": {},
                "forward_walk_forward": {},
                "time_ordered_cross_validation": {},
                "reverse_retrospective_only": {},
            }],
        }]
        compact_messages = research.build_messages(
            target, train, direction="forward", prior_memory=compact_memory
        )
        compact_payload = json.loads(compact_messages[1]["content"])
        self.assertEqual(
            compact_payload["prior_general_research_memory_untrusted"], compact_memory
        )

    def test_prompt_payload_preserves_all_64_complete_candidate_records(self):
        target, train = research.isolate_pre_target(self.rows)
        candidates = [
            {
                "hypothesis_id": f"HYP-{index + 1:03d}",
                "family": "synthetic family",
                "template_id": f"synthetic-template-{index + 1}",
                "proposal": {"hypothesis_id": f"HYP-{index + 1:03d}", "exact_definition": f"Exact definition for candidate {index + 1}"},
                "execution_spec": {"schema_version": 1, "candidate_index": index + 1},
                "specification_sha256": "a" * 64,
                "training_signal": {"training_draw_count": 100},
                "synthetic_tests": {"status": "PASS"},
                "forward_walk_forward": {"predictive_claim": False},
                "time_ordered_cross_validation": {"predictive_claim": False},
                "reverse_retrospective_only": {"predictive_evidence": False},
            }
            for index in range(64)
        ]
        memory_context = [{
            "direction": "forward",
            "source": "GENERAL/offline-symbolic-discovery",
            "candidate_transfer_mode": "FULL_VALIDATED_RECORDS",
            "candidates": candidates,
            "candidates_in_memory": 64,
            "candidates_in_context": 64,
            "candidate_context_truncated": False,
            "candidate_selection": "NONE",
            "automatic_candidate_selection": False,
        }]
        messages = research.build_messages(
            target, train, direction="forward", prior_memory=memory_context
        )
        payload = json.loads(messages[1]["content"])
        carried = payload["prior_general_research_memory_untrusted"][0]
        self.assertEqual(carried["candidates"], candidates)
        self.assertEqual(len(carried["candidates"]), 64)
        self.assertEqual(carried["candidate_transfer_mode"], "FULL_VALIDATED_RECORDS")
        self.assertFalse(carried["candidate_context_truncated"])

    def test_offline_candidate_record_with_missing_execution_spec_is_rejected(self):
        target, train = research.isolate_pre_target(self.rows)
        incomplete = {
            "hypothesis_id": "HYP-001",
            "family": "synthetic family",
            "template_id": "synthetic-template",
            "proposal": {"hypothesis_id": "HYP-001"},
            "specification_sha256": "a" * 64,
            "training_signal": {},
            "synthetic_tests": {},
            "forward_walk_forward": {},
            "time_ordered_cross_validation": {},
            "reverse_retrospective_only": {},
        }
        context = [{
            "direction": "forward",
            "source": "GENERAL/offline-symbolic-discovery",
            "candidate_transfer_mode": "FULL_VALIDATED_RECORDS",
            "candidates_in_memory": 1,
            "candidates_in_context": 1,
            "candidate_context_truncated": False,
            "candidates": [incomplete],
        }]
        with self.assertRaisesRegex(ValueError, "incomplete or contains a partial/duplicate record"):
            research.build_messages(target, train, direction="forward", prior_memory=context)

    def test_partial_offline_candidate_context_is_rejected_before_prompt_creation(self):
        target, train = research.isolate_pre_target(self.rows)
        with self.assertRaisesRegex(ValueError, "Offline candidate context is incomplete"):
            research.build_messages(
                target,
                train,
                direction="forward",
                prior_memory=[{
                    "direction": "forward",
                    "source": "GENERAL/offline-symbolic-discovery",
                    "candidates": [["HYP-001", "partial row"]],
                    "candidate_context_truncated": False,
                }],
            )

    def test_complete_prompt_byte_budget_fails_before_any_provider_reservation(self):
        target, train = research.isolate_pre_target(self.rows)
        oversized_memory = [{
            "direction": "forward",
            "oversized_test_payload": "x" * research.MAX_PROMPT_MESSAGE_BYTES,
        }]
        with self.assertRaisesRegex(ValueError, "no provider reservation or request was made"):
            research.build_messages(
                target, train, direction="forward", prior_memory=oversized_memory
            )

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
        research._NVIDIA_REQUEST_LABELS_SUCCEEDED.clear()
        research._NVIDIA_DIRECTIONAL_COMPLETED.clear()
        research._NVIDIA_REQUEST_ATTEMPT_COUNTS.clear()
        research._RETRY_AUDIT_EVENTS.clear()
        research.RETRY_AUDIT.unlink(missing_ok=True)
        for label in ("forward", "reverse", "forward_critique", "between_draws", "unlabelled"):
            Path("/tmp/general_nvidia_api_no_retry_" + label).unlink(missing_ok=True)
            Path("/tmp/general_nvidia_api_known_http_5xx_" + label).unlink(missing_ok=True)
        research.RETRY_AUDIT.unlink(missing_ok=True)
        research.RETRY_AUDIT.with_name(research.RETRY_AUDIT.name + ".tmp").unlink(missing_ok=True)
            Path("/tmp/general_nvidia_api_known_http_5xx_" + label).unlink(missing_ok=True)

    @staticmethod
    def sdk_response(
        content="synthetic response",
        *,
        finish_reason="stop",
        usage=None,
        reasoning_content=None,
    ):
        message = {"role": "assistant", "content": content}
        if reasoning_content is not None:
            message["reasoning_content"] = reasoning_content
        payload = {
            "id": "chatcmpl-synthetic",
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }],
            "usage": usage or {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
        }
        first_delta = SimpleNamespace(
            content=content,
            reasoning_content=reasoning_content,
        )
        terminal_delta = SimpleNamespace(content=None, reasoning_content=None)
        usage_chunk = payload["usage"]

        class SyntheticSDKStream:
            def __init__(self):
                self.chunks = [
                    SimpleNamespace(
                        choices=[SimpleNamespace(delta=first_delta, finish_reason=None)],
                        usage=None,
                    ),
                    SimpleNamespace(
                        choices=[SimpleNamespace(delta=terminal_delta, finish_reason=finish_reason)],
                        usage=usage_chunk,
                    ),
                ]
                self._request_id = "synthetic-request-id"
                self.closed = False

            def __iter__(self):
                return iter(self.chunks)

            def close(self):
                self.closed = True

        return SyntheticSDKStream()

    @staticmethod
    def client_with_response(completion=None):
        client = MagicMock()
        client.chat.completions.create.return_value = (
            completion if completion is not None
            else NvidiaUsageObservationTests.sdk_response()
        )
        return client

    @staticmethod
    def live_env():
        return {
            "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
            "GENERAL_NVIDIA_GUARD_PASSED": "1",
            "GENERAL_ALLOW_NVIDIA_CALL": "true",
            "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
            "NVIDIA_API_KEY": "test-secret",
        }

    def tearDown(self):
        Path("/tmp/general_nvidia_api_attempted").unlink(missing_ok=True)
        Path("/tmp/general_nvidia_api_succeeded").unlink(missing_ok=True)
        for label in ("forward", "reverse", "forward_critique", "between_draws", "unlabelled"):
            Path("/tmp/general_nvidia_api_no_retry_" + label).unlink(missing_ok=True)

    def test_openai_sdk_client_uses_nvidia_base_url_timeout_and_zero_retries(self):
        fake_client = MagicMock()
        fake_http_client = MagicMock()
        fake_openai_module = types.ModuleType("openai")
        fake_openai_module.OpenAI = MagicMock(return_value=fake_client)
        fake_httpx2_module = types.ModuleType("httpx2")
        fake_httpx2_module.Client = MagicMock(return_value=fake_http_client)

        with patch.dict(
            sys.modules,
            {"openai": fake_openai_module, "httpx2": fake_httpx2_module},
        ):
            result = research._create_nvidia_client("test-secret", 123)

        network_trace = result._general_network_phase_trace
        self.assertIsInstance(network_trace, research.NvidiaNetworkPhaseTrace)
        self.assertIs(result, fake_client)
        httpx2_kwargs = fake_httpx2_module.Client.call_args.kwargs
        self.assertEqual(httpx2_kwargs["timeout"], 123)
        self.assertTrue(httpx2_kwargs["follow_redirects"])
        self.assertIs(httpx2_kwargs["event_hooks"]["request"][0].__self__, network_trace)
        self.assertIs(httpx2_kwargs["event_hooks"]["response"][0].__self__, network_trace)
        fake_openai_module.OpenAI.assert_called_once_with(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key="test-secret",
            timeout=123,
            http_client=fake_http_client,
            max_retries=0,
        )

    @unittest.skipUnless(
        find_spec("openai") is not None and find_spec("httpx2") is not None,
        "requires the pinned OpenAI SDK and HTTPX2 installed by provider test jobs",
    )
    def test_pinned_openai_sdk_constructs_with_real_httpx2_without_network(self):
        from importlib.metadata import version
        import httpx2

        self.assertEqual(version("openai"), "3.45.0")
        client = research._create_nvidia_client("test-secret", 123)
        try:
            self.assertIsInstance(client._client, httpx2.Client)
            self.assertIsInstance(
                client._general_network_phase_trace,
                research.NvidiaNetworkPhaseTrace,
            )
            self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 0)
        finally:
            client.close()

    def test_network_phase_trace_records_httpcore_timings_only(self):
        trace = research.NvidiaNetworkPhaseTrace()
        request = SimpleNamespace(extensions={})
        ticks = iter([
            0.0, 0.1, 0.3, 0.3, 0.6, 0.7, 0.8,
            0.8, 1.1, 1.1, 3.1, 3.2, 3.2, 8.2,
        ])

        with patch("general_open_research.time.monotonic", side_effect=lambda: next(ticks)):
            trace.on_request(request)
            record = request.extensions["trace"]
            record("connection.connect_tcp.started", {"host": "sensitive.example"})
            record("connection.connect_tcp.complete", {"return_value": "sensitive-socket"})
            record("connection.start_tls.started", {"server_hostname": "sensitive.example"})
            record("connection.start_tls.complete", {"return_value": "sensitive-stream"})
            record("http11.send_request_headers.started", {})
            record("http11.send_request_headers.complete", {})
            record("http11.send_request_body.started", {})
            record("http11.send_request_body.complete", {})
            record("http11.receive_response_headers.started", {})
            record("http11.receive_response_headers.complete", {})
            trace.on_response(SimpleNamespace())
            record("http11.receive_response_body.started", {})
            record("http11.receive_response_body.complete", {})

        self.assertEqual(trace.snapshot(), {
            "tcp_tls_establishment_seconds": 0.5,
            "request_send_seconds": 0.4,
            "response_header_wait_seconds": 2.0,
            "transport_open_seconds": 3.2,
            "stream_read_seconds": 5.0,
        })
        self.assertNotIn("sensitive.example", json.dumps(trace.snapshot()))
        self.assertNotIn("sensitive-socket", json.dumps(trace.snapshot()))

    def test_first_sse_event_latency_is_measured_from_request_start(self):
        stream = self.sdk_response()
        state = {
            "stream_event_count": 0,
            "stream_completed": False,
            "stream_finish_reason": None,
            "usage": None,
            "request_started_at": 100.0,
            "first_sse_event_seconds": None,
        }
        with patch("general_open_research.time.monotonic", return_value=101.375):
            payload, metadata = research._consume_streaming_chat_completion(stream, state)

        self.assertEqual(metadata["stream_event_count"], 2)
        self.assertEqual(state["first_sse_event_seconds"], 1.375)
        observed = research.build_usage_observation(
            200,
            {"x-request-id": "first-event-test"},
            payload,
            response_mode="sse",
            request_duration_seconds=3.0,
            first_sse_event_seconds=state["first_sse_event_seconds"],
            stream_requested=True,
        )
        self.assertEqual(observed["first_sse_event_seconds"], 1.375)

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
        self.assertEqual(observed["sdk_max_retries"], 0)
        self.assertFalse(observed["stream_requested"])
        self.assertEqual(observed["transport"], "openai-python-sdk")
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
            "general_open_research._create_nvidia_client"
        ) as client_factory:
            with self.assertRaisesRegex(RuntimeError, "not explicitly approved"):
                research.call_model([])
            client_factory.assert_not_called()
        self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 0)

    def test_live_inference_is_blocked_without_global_lock_preflight(self):
        with patch.dict(os.environ, self.live_env(), clear=True):
            os.environ.pop("GENERAL_NVIDIA_GUARD_PASSED")
            with patch("general_open_research._create_nvidia_client") as client_factory:
                with self.assertRaisesRegex(
                    RuntimeError, "Global NVIDIA safety-lock preflight was not confirmed"
                ):
                    research.call_model([{"role": "user", "content": "test"}])
                client_factory.assert_not_called()

    def test_live_inference_is_blocked_when_free_use_is_not_verified(self):
        env = self.live_env()
        env.pop("GENERAL_NVIDIA_FREE_USAGE_VERIFIED")
        with patch.dict(os.environ, env, clear=True), patch(
            "general_open_research._create_nvidia_client"
        ) as client_factory:
            with self.assertRaisesRegex(
                RuntimeError, "Free-use status was not independently verified"
            ):
                research.call_model([{"role": "user", "content": "test"}])
            client_factory.assert_not_called()

    def test_normal_mode_single_request_uses_default_8192_budget_and_blocks_a_second_call(self):
        env = self.live_env() | {
            "GENERAL_SINGLE_REQUEST_MODE": "true",
            "GENERAL_CONFIRM_LIVE_CALLS": SINGLE_REQUEST_CONFIRMATION_PHRASE,
        }
        client = self.client_with_response(self.sdk_response("single request response"))
        with tempfile.TemporaryDirectory() as temp_dir:
            usage_path = Path(temp_dir) / "usage.json"
            with patch.dict(os.environ, env, clear=True), patch.object(
                research, "USAGE_REPORT", usage_path
            ), patch.object(
                research, "save_usage_observation"
            ), patch.object(
                research, "reserve_nvidia_request"
            ) as reserve, patch(
                "general_open_research._create_nvidia_client", return_value=client
            ) as client_factory:
                with self.assertRaisesRegex(RuntimeError, "only one forward request"):
                    research.call_model(
                        [{"role": "user", "content": "reverse must be blocked"}],
                        request_label="reverse",
                    )
                client_factory.assert_not_called()

                answer = research.call_model(
                    [{"role": "user", "content": "normal-mode token test"}],
                    request_label="forward",
                )
                self.assertEqual(answer, "single request response")
                with self.assertRaisesRegex(RuntimeError, "one-request cap reached"):
                    research.call_model(
                        [{"role": "user", "content": "second request must be blocked"}],
                        request_label="forward",
                    )

                self.assertEqual(client_factory.call_count, 1)
                client.chat.completions.create.assert_called_once()
                sent = client.chat.completions.create.call_args.kwargs
                self.assertEqual(sent["max_tokens"], research.DEFAULT_MAX_COMPLETION_TOKENS)
                self.assertEqual(sent["max_tokens"], 8192)
                self.assertIs(sent["stream"], True)
                self.assertEqual(
                    client_factory.call_args.args[1],
                    research.DEFAULT_REQUEST_TIMEOUT_SECONDS,
                )
                client.close.assert_called_once()
                reserve.assert_called_once_with(
                    "open-research-single-forward",
                    single_request_exception=True,
                )
                self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 1)
                self.assertEqual(research._NVIDIA_REQUEST_LABELS_ATTEMPTED, {"forward"})

    def test_single_request_mode_rejects_diagnostic_mode_combination_before_network(self):
        env = self.live_env() | {
            "GENERAL_SINGLE_REQUEST_MODE": "true",
            "GENERAL_DIAGNOSTIC_MODE": "true",
            "GENERAL_CONFIRM_LIVE_CALLS": SINGLE_REQUEST_CONFIRMATION_PHRASE,
        }
        with patch.dict(os.environ, env, clear=True), patch(
            "general_open_research._create_nvidia_client"
        ) as client_factory:
            with self.assertRaisesRegex(RuntimeError, "cannot be combined"):
                research.call_model(
                    [{"role": "user", "content": "must not be sent"}],
                    request_label="forward",
                )
            client_factory.assert_not_called()
        self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 0)

    def test_four_request_package_allows_two_directions_critique_and_between_draws(self):
        env = self.live_env()
        clients = [
            self.client_with_response(self.sdk_response("forward final content")),
            self.client_with_response(self.sdk_response("reverse final content")),
            self.client_with_response(self.sdk_response("forward critique content")),
            self.client_with_response(self.sdk_response("between draws content")),
        ]
        saved = []
        with patch.dict(os.environ, env, clear=True), patch.object(
            research, "save_usage_observation",
            side_effect=lambda observation, **kwargs: saved.append((observation, kwargs)),
        ), patch.object(research, "reserve_nvidia_request") as reserve, patch(
            "general_open_research._create_nvidia_client",
            side_effect=clients,
        ) as client_factory:
            forward = research.call_model(
                [{"role": "user", "content": "forward"}],
                request_label="forward",
            )
            reverse = research.call_model(
                [{"role": "user", "content": "reverse"}],
                request_label="reverse",
            )
            critique = research.call_model(
                [{"role": "user", "content": "critique the validated forward proposal"}],
                request_label="forward_critique",
            )
            between = research.call_model(
                [{"role": "user", "content": "independent between-draw research"}],
                request_label="between_draws",
            )
            self.assertEqual(forward, "forward final content")
            self.assertEqual(reverse, "reverse final content")
            self.assertEqual(critique, "forward critique content")
            self.assertEqual(between, "between draws content")
            with self.assertRaisesRegex(RuntimeError, "request cap reached"):
                research.call_model(
                    [{"role": "user", "content": "fifth request must be blocked"}],
                )

        self.assertEqual(client_factory.call_count, 4)
        self.assertEqual([call.args[1] for call in client_factory.call_args_list], [600, 600, 600, 600])
        self.assertEqual(reserve.call_count, 4)
        self.assertEqual(research.MAX_NVIDIA_REQUESTS_PER_RUN, 4)
        for client in clients:
            client.chat.completions.create.assert_called_once()
            call = client.chat.completions.create.call_args.kwargs
            self.assertIs(call["stream"], True)
            self.assertEqual(call["model"], research.MODEL)
            self.assertEqual(call["max_tokens"], 8192)
            self.assertEqual(call["temperature"], 0.2)
            self.assertEqual(
                call["extra_body"],
                {
                    "reasoning_effort": "low",
                    "chat_template_kwargs": {"clear_thinking": True},
                },
            )
            client.close.assert_called_once()
        self.assertEqual(len(saved), 4)
        self.assertEqual(
            [item[1]["request_label"] for item in saved],
            ["forward", "reverse", "forward_critique", "between_draws"],
        )
        for observation, _kwargs in saved:
            self.assertEqual(observation["response_mode"], "sse")
            self.assertEqual(observation["transport"], "openai-python-sdk")
            self.assertEqual(observation["sdk_max_retries"], 0)
            self.assertEqual(observation["automatic_retries"], 0)
            self.assertTrue(observation["stream_requested"])
            self.assertTrue(observation["stream_completed"])
            self.assertEqual(observation["stream_finish_reason"], "stop")
            self.assertEqual(observation["request_delivery_state"], "response_received")
            self.assertEqual(observation["model_finish_reason"], "stop")
            self.assertEqual(
                observation["rate_limit_headers_returned"]["x-request-id"],
                "synthetic-request-id",
            )

    def test_runner_rejects_reverse_before_forward_and_duplicate_direction(self):
        with patch.dict(os.environ, self.live_env(), clear=True), patch(
            "general_open_research._create_nvidia_client"
        ) as client_factory:
            with self.assertRaisesRegex(RuntimeError, "forward-then-reverse"):
                research.call_model(
                    [{"role": "user", "content": "reverse"}],
                    request_label="reverse",
                )
            client_factory.assert_not_called()

    def test_sdk_timeout_is_recorded_once_and_never_retried(self):
        client = self.client_with_response()
        client.chat.completions.create.side_effect = TimeoutError("synthetic SDK timeout")
        saved = []
        with patch.dict(os.environ, self.live_env(), clear=True), patch.object(
            research, "reserve_nvidia_request"
        ), patch.object(
            research, "save_usage_observation",
            side_effect=lambda observation, **kwargs: saved.append(observation),
        ), patch(
            "general_open_research._create_nvidia_client", return_value=client
        ):
            with self.assertRaisesRegex(RuntimeError, "No automatic retry was attempted"):
                research.call_model(
                    [{"role": "user", "content": "timeout test"}],
                    request_label="forward",
                )

        client.chat.completions.create.assert_called_once()
        client.close.assert_called_once()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["failure_stage"], "provider_call")
        self.assertEqual(saved[0]["response_mode"], "sse")
        self.assertEqual(saved[0]["network_error_type"], "TimeoutError")
        self.assertEqual(
            saved[0]["request_delivery_state"],
            "unknown_after_sdk_request_started",
        )
        self.assertEqual(saved[0]["automatic_retries"], 0)
        self.assertEqual(saved[0]["sdk_max_retries"], 0)
        self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 1)

    def test_http_504_is_retried_once_after_explicit_response_and_then_validated(self):
        class SyntheticGatewayTimeout(Exception):
            status_code = 504
            response = SimpleNamespace(headers={
                "x-request-id": "gateway-request-504",
                "Authorization": "never-log-this",
            })

        first = self.client_with_response()
        first.chat.completions.create.side_effect = SyntheticGatewayTimeout("gateway timed out")
        second = self.client_with_response(self.sdk_response("recovered after one 5xx"))
        clients = [first, second]
        saved = []
        with patch.dict(os.environ, self.live_env(), clear=True), patch.object(
            research, "reserve_nvidia_request"
        ) as reserve, patch.object(
            research, "save_usage_observation",
            side_effect=lambda observation, **kwargs: saved.append(observation),
        ), patch(
            "general_open_research._create_nvidia_client", side_effect=clients
        ):
            answer = research.call_model(
                [{"role": "user", "content": "gateway retry test"}],
                request_label="forward",
            )

        self.assertEqual(answer, "recovered after one 5xx")
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]["http_status"], 504)
        self.assertEqual(saved[0]["failure_stage"], "provider_http_error")
        self.assertEqual(saved[0]["request_delivery_state"], "response_received")
        self.assertTrue(saved[0]["provider_http_response_received"])
        self.assertFalse(saved[0]["model_stream_events_received"])
        self.assertFalse(saved[0]["model_response_validated"])
        self.assertEqual(saved[0]["provider_error_category"], "HTTP_5XX_SERVER_ERROR")
        self.assertTrue(saved[1]["model_response_validated"])
        self.assertFalse(Path("/tmp/general_nvidia_api_known_http_5xx_forward").exists())
        self.assertFalse(Path("/tmp/general_nvidia_api_no_retry_forward").exists())
        self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 2)
        self.assertEqual(research._NVIDIA_REQUEST_ATTEMPT_COUNTS["forward"], 2)
        self.assertEqual(reserve.call_count, 2)
        self.assertEqual(reserve.call_args_list[1].kwargs.get("budget_category"), "retry_reserve")
        first.close.assert_called_once()
        second.close.assert_called_once()
        audit = json.loads(research.RETRY_AUDIT.read_text(encoding="utf-8"))
        self.assertEqual(audit["automatic_retries_reserved"], 1)
        self.assertFalse(audit["provider_response_bodies_stored"])

    def test_forward_success_then_reverse_504_retries_once_then_stops(self):
        class SyntheticGatewayTimeout(Exception):
            status_code = 504
            response = SimpleNamespace(headers={"x-request-id": "reverse-gateway-504"})

        clients = [
            self.client_with_response(self.sdk_response("forward completed")),
            self.client_with_response(),
            self.client_with_response(),
        ]
        clients[1].chat.completions.create.side_effect = SyntheticGatewayTimeout(
            "synthetic reverse gateway timeout"
        )
        clients[2].chat.completions.create.side_effect = SyntheticGatewayTimeout(
            "synthetic reverse retry also failed"
        )
        saved = []

        def client_factory(_api_key, _timeout):
            network_trace = research.NvidiaNetworkPhaseTrace()
            request = SimpleNamespace(extensions={})
            network_trace.on_request(request)
            record = request.extensions["trace"]
            for phase in (
                "connection.connect_tcp", "connection.start_tls",
                "http11.send_request_headers", "http11.send_request_body",
                "http11.receive_response_headers",
            ):
                record(phase + ".started", {})
                record(phase + ".complete", {})
            network_trace.on_response(SimpleNamespace())
            client = clients.pop(0)
            client._general_network_phase_trace = network_trace
            return client

        with patch.dict(os.environ, self.live_env(), clear=True), patch.object(
            research, "reserve_nvidia_request"
        ) as reserve, patch.object(
            research, "save_usage_observation",
            side_effect=lambda observation, **kwargs: saved.append(
                (observation, kwargs["request_label"])
            ),
        ), patch(
            "general_open_research._create_nvidia_client", side_effect=client_factory
        ):
            self.assertEqual(
                research.call_model(
                    [
                        {"role": "system", "content": "synthetic system prompt"},
                        {"role": "user", "content": "synthetic forward request"},
                    ],
                    request_label="forward",
                ),
                "forward completed",
            )
            with self.assertRaisesRegex(RuntimeError, "explicit HTTP 504"):
                research.call_model(
                    [
                        {"role": "system", "content": "synthetic system prompt"},
                        {"role": "user", "content": "synthetic reverse request"},
                    ],
                    request_label="reverse",
                )
            with self.assertRaisesRegex(RuntimeError, "already attempted"):
                research.call_model(
                    [{"role": "user", "content": "duplicate reverse must be blocked"}],
                    request_label="reverse",
                )

        self.assertEqual(len(saved), 3)
        forward_observation, forward_label = saved[0]
        reverse_observation, reverse_label = saved[1]
        retry_observation, retry_label = saved[2]
        self.assertEqual((forward_label, reverse_label, retry_label), ("forward", "reverse", "reverse"))
        self.assertTrue(forward_observation["model_response_validated"])
        self.assertIsNotNone(forward_observation["first_sse_event_seconds"])
        self.assertEqual(forward_observation["http_status"], 200)
        for failed in (reverse_observation, retry_observation):
            self.assertEqual(failed["http_status"], 504)
            self.assertEqual(failed["failure_stage"], "provider_http_error")
            self.assertEqual(failed["provider_error_category"], "HTTP_5XX_SERVER_ERROR")
            self.assertEqual(failed["stream_event_count"], 0)
            self.assertFalse(failed["model_stream_events_received"])
            self.assertFalse(failed["model_response_validated"])
        self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 3)
        self.assertEqual(research._NVIDIA_REQUEST_ATTEMPT_COUNTS, {"forward": 1, "reverse": 2})
        self.assertEqual(research._NVIDIA_REQUEST_LABELS_SUCCEEDED, {"forward"})
        self.assertTrue(Path("/tmp/general_nvidia_api_known_http_5xx_reverse").exists())
        self.assertFalse(Path("/tmp/general_nvidia_api_no_retry_reverse").exists())
        self.assertEqual(reserve.call_count, 3)
        self.assertEqual(reserve.call_args_list[2].kwargs.get("budget_category"), "retry_reserve")
        self.assertEqual(clients, [])
        audit = json.loads(research.RETRY_AUDIT.read_text(encoding="utf-8"))
        self.assertEqual(audit["automatic_retries_reserved"], 1)

    def test_http_404_records_allowlisted_error_identifiers_without_body_or_retry_changes(self):
        class SyntheticNotFoundError(Exception):
            status_code = 404
            response = SimpleNamespace(
                status_code=404,
                headers={
                    "x-request-id": "nvidia-request-404",
                    "Authorization": "Bearer DO_NOT_STORE_THIS_TOKEN",
                    "Set-Cookie": "DO_NOT_STORE_THIS_COOKIE",
                },
            )
            body = {
                "error": {
                    "type": "invalid_request_error",
                    "code": "model_not_found",
                    "message": "PRIVATE_PROVIDER_MESSAGE_DO_NOT_STORE",
                    "param": "model",
                },
            }

        client = self.client_with_response()
        client.chat.completions.create.side_effect = SyntheticNotFoundError(
            "PRIVATE_EXCEPTION_MESSAGE_DO_NOT_STORE"
        )
        saved = []
        with patch.dict(os.environ, self.live_env(), clear=True), patch.object(
            research, "reserve_nvidia_request"
        ), patch.object(
            research, "save_usage_observation",
            side_effect=lambda observation, **kwargs: saved.append(observation),
        ), patch(
            "general_open_research._create_nvidia_client", return_value=client
        ):
            with self.assertRaisesRegex(RuntimeError, "SyntheticNotFoundError"):
                research.call_model(
                    [{"role": "user", "content": "synthetic 404 test"}],
                    request_label="forward",
                )

        self.assertEqual(len(saved), 1)
        observation = saved[0]
        self.assertEqual(observation["http_status"], 404)
        self.assertEqual(observation["provider_error_category"], "HTTP_404_NOT_FOUND")
        self.assertEqual(observation["provider_error_type"], "invalid_request_error")
        self.assertEqual(observation["provider_error_code"], "model_not_found")
        self.assertEqual(
            observation["rate_limit_headers_returned"]["x-request-id"],
            "nvidia-request-404",
        )
        self.assertEqual(observation["request_delivery_state"], "response_received")
        self.assertTrue(observation["provider_http_response_received"])
        self.assertFalse(observation["model_stream_events_received"])
        self.assertFalse(observation["model_response_validated"])
        self.assertEqual(observation["automatic_retries"], 0)
        self.assertEqual(observation["sdk_max_retries"], 0)
        serialized = json.dumps(observation)
        for secret in (
            "DO_NOT_STORE_THIS_TOKEN",
            "DO_NOT_STORE_THIS_COOKIE",
            "PRIVATE_PROVIDER_MESSAGE_DO_NOT_STORE",
            "PRIVATE_EXCEPTION_MESSAGE_DO_NOT_STORE",
        ):
            self.assertNotIn(secret, serialized)
        self.assertFalse(Path("/tmp/general_nvidia_api_no_retry_forward").exists())
        self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 1)
        client.close.assert_called_once()

    def test_api_connection_error_records_only_nested_exception_types(self):
        class SyntheticAPIConnectionError(Exception):
            pass

        cause = ConnectionResetError("sensitive endpoint detail must not be persisted")
        error = SyntheticAPIConnectionError("outer provider detail must not be persisted")
        error.__cause__ = cause
        client = self.client_with_response()
        client.chat.completions.create.side_effect = error
        saved = []
        with patch.dict(os.environ, self.live_env(), clear=True), patch.object(
            research, "reserve_nvidia_request"
        ), patch.object(
            research, "save_usage_observation",
            side_effect=lambda observation, **kwargs: saved.append(observation),
        ), patch(
            "general_open_research._create_nvidia_client", return_value=client
        ):
            with self.assertRaisesRegex(RuntimeError, "SyntheticAPIConnectionError"):
                research.call_model(
                    [{"role": "user", "content": "connection error test"}],
                    request_label="forward",
                )

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["network_error_type"], "SyntheticAPIConnectionError")
        self.assertEqual(saved[0]["network_error_chain"], ["ConnectionResetError"])
        self.assertEqual(
            saved[0]["request_delivery_state"],
            "unknown_after_sdk_request_started",
        )
        serialized = json.dumps(saved[0])
        self.assertNotIn("sensitive endpoint detail", serialized)
        self.assertNotIn("outer provider detail", serialized)
        self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 1)
        client.close.assert_called_once()

    def test_stream_ending_without_finish_reason_is_rejected_as_incomplete(self):
        stream = self.sdk_response("partial without terminal finish reason", finish_reason=None)
        client = self.client_with_response(stream)
        saved = []
        with patch.dict(os.environ, self.live_env(), clear=True), patch.object(
            research, "reserve_nvidia_request"
        ), patch.object(
            research, "save_usage_observation",
            side_effect=lambda observation, **kwargs: saved.append(observation),
        ), patch(
            "general_open_research._create_nvidia_client", return_value=client
        ):
            with self.assertRaisesRegex(RuntimeError, "without a terminal finish_reason"):
                research.call_model(
                    [{"role": "user", "content": "missing terminal chunk test"}],
                    request_label="forward",
                )

        self.assertEqual(len(saved), 1)
        self.assertFalse(saved[0]["response_complete"])
        self.assertTrue(saved[0]["stream_completed"])
        self.assertIsNone(saved[0]["stream_finish_reason"])
        self.assertNotIn("partial without terminal", json.dumps(saved[0]))
        self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 1)
        client.close.assert_called_once()

    def test_stream_disconnect_discards_partial_content_and_keeps_delivery_uncertain(self):
        class SyntheticStreamDisconnect(Exception):
            pass

        class InterruptedStream:
            def __iter__(self):
                yield SimpleNamespace(
                    choices=[SimpleNamespace(
                        delta=SimpleNamespace(content="partial answer must not be accepted"),
                        finish_reason=None,
                    )],
                    usage=None,
                )
                raise SyntheticStreamDisconnect("simulated stream interruption")

            def close(self):
                pass

        client = self.client_with_response(InterruptedStream())
        saved = []
        with patch.dict(os.environ, self.live_env(), clear=True), patch.object(
            research, "reserve_nvidia_request"
        ), patch.object(
            research, "save_usage_observation",
            side_effect=lambda observation, **kwargs: saved.append(observation),
        ), patch(
            "general_open_research._create_nvidia_client", return_value=client
        ):
            with self.assertRaisesRegex(RuntimeError, "No automatic retry was attempted"):
                research.call_model(
                    [{"role": "user", "content": "interrupted stream test"}],
                    request_label="forward",
                )

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["stream_event_count"], 1)
        self.assertFalse(saved[0]["stream_completed"])
        self.assertFalse(saved[0]["response_complete"])
        self.assertEqual(
            saved[0]["request_delivery_state"],
            "unknown_after_sdk_request_started",
        )
        self.assertNotIn("partial answer must not be accepted", json.dumps(saved[0]))
        self.assertEqual(research._NVIDIA_REQUESTS_ATTEMPTED, 1)
        client.close.assert_called_once()

    def test_sdk_rate_limit_error_records_safe_metadata_without_retry(self):
        class SyntheticStatusError(Exception):
            status_code = 429
            response = SimpleNamespace(headers={
                "x-ratelimit-remaining-requests": "0",
                "retry-after": "60",
                "Authorization": "never-log-this",
            })

        client = self.client_with_response()
        client.chat.completions.create.side_effect = SyntheticStatusError("rate limited")
        saved = []
        with patch.dict(os.environ, self.live_env(), clear=True), patch.object(
            research, "reserve_nvidia_request"
        ), patch.object(
            research, "save_usage_observation",
            side_effect=lambda observation, **kwargs: saved.append(observation),
        ), patch(
            "general_open_research._create_nvidia_client", return_value=client
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                research.call_model(
                    [{"role": "user", "content": "rate limit test"}],
                    request_label="forward",
                )

        client.chat.completions.create.assert_called_once()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["http_status"], 429)
        self.assertEqual(saved[0]["failure_stage"], "provider_http_error")
        self.assertEqual(
            saved[0]["rate_limit_headers_returned"]["retry-after"], "60"
        )
        self.assertNotIn("never-log-this", json.dumps(saved[0]))
        self.assertEqual(saved[0]["automatic_retries"], 0)
        self.assertTrue(Path("/tmp/general_nvidia_api_no_retry_forward").is_file())

    def test_rate_limit_no_retry_marker_survives_usage_report_write_failure(self):
        class SyntheticStatusError(Exception):
            status_code = 429
            response = SimpleNamespace(headers={})

        client = self.client_with_response()
        client.chat.completions.create.side_effect = SyntheticStatusError("rate limited")
        with patch.dict(os.environ, self.live_env(), clear=True), patch.object(
            research, "reserve_nvidia_request"
        ), patch.object(
            research, "save_usage_observation", side_effect=OSError("synthetic disk error")
        ), patch(
            "general_open_research._create_nvidia_client", return_value=client
        ):
            with self.assertRaisesRegex(OSError, "synthetic disk error"):
                research.call_model(
                    [{"role": "user", "content": "rate limit with report-write failure"}],
                    request_label="between_draws",
                )

        self.assertTrue(Path("/tmp/general_nvidia_api_no_retry_between_draws").is_file())

    def test_sdk_insufficient_credit_error_is_marked_non_retryable(self):
        class SyntheticStatusError(Exception):
            status_code = 402
            response = SimpleNamespace(headers={})

        client = self.client_with_response()
        client.chat.completions.create.side_effect = SyntheticStatusError("insufficient credits")
        saved = []
        with patch.dict(os.environ, self.live_env(), clear=True), patch.object(
            research, "reserve_nvidia_request"
        ), patch.object(
            research, "save_usage_observation",
            side_effect=lambda observation, **kwargs: saved.append(observation),
        ), patch(
            "general_open_research._create_nvidia_client", return_value=client
        ):
            with self.assertRaisesRegex(RuntimeError, "HTTP 402"):
                research.call_model(
                    [{"role": "user", "content": "insufficient credit test"}],
                    request_label="forward",
                )

        client.chat.completions.create.assert_called_once()
        self.assertEqual(saved[0]["http_status"], 402)
        self.assertEqual(saved[0]["failure_stage"], "provider_http_error")
        self.assertTrue(Path("/tmp/general_nvidia_api_no_retry_forward").is_file())

    def test_json_response_rejects_reasoning_only_content(self):
        client = self.client_with_response(
            self.sdk_response(
                content=None,
                reasoning_content="this is not final answer content",
            )
        )
        saved = []
        with patch.dict(os.environ, self.live_env(), clear=True), patch.object(
            research, "reserve_nvidia_request"
        ), patch.object(
            research, "save_usage_observation",
            side_effect=lambda observation, **kwargs: saved.append(observation),
        ), patch(
            "general_open_research._create_nvidia_client", return_value=client
        ):
            with self.assertRaisesRegex(RuntimeError, "reasoning_content is not accepted"):
                research.call_model(
                    [{"role": "user", "content": "reasoning-only test"}],
                    request_label="forward",
                )

        client.chat.completions.create.assert_called_once()
        self.assertEqual(saved[0]["failure_stage"], "response_validation")
        self.assertEqual(saved[0]["http_status"], 200)
        self.assertEqual(saved[0]["request_delivery_state"], "response_received")

    def test_json_response_rejects_token_limit_truncation_without_saving_partial_text(self):
        partial = "partial answer must not be retained"
        client = self.client_with_response(
            self.sdk_response(
                partial,
                finish_reason="length",
                usage={"prompt_tokens": 333, "completion_tokens": 8192, "total_tokens": 8525},
            )
        )
        saved = []
        with patch.dict(os.environ, self.live_env(), clear=True), patch.object(
            research, "reserve_nvidia_request"
        ), patch.object(
            research, "save_usage_observation",
            side_effect=lambda observation, **kwargs: saved.append(observation),
        ), patch(
            "general_open_research._create_nvidia_client", return_value=client
        ):
            with self.assertRaisesRegex(RuntimeError, "reached max_tokens"):
                research.call_model(
                    [{"role": "user", "content": "truncation test"}],
                    request_label="forward",
                )

        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["failure_stage"], "model_finish")
        self.assertEqual(saved[0]["network_error_type"], "IncompleteModelResponseError")
        self.assertEqual(saved[0]["token_usage_returned_by_provider"]["total_tokens"], 8525)
        self.assertEqual(saved[0]["max_tokens_requested"], 8192)
        self.assertFalse(saved[0]["response_complete"])
        self.assertEqual(saved[0]["automatic_retries"], 0)
        self.assertNotIn(partial, json.dumps(saved[0]))

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



class OpenResearchOrchestrationFailureTests(unittest.TestCase):
    def test_reverse_504_after_forward_success_stops_before_parse_or_promotion(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = "2026-11-12"
            rows = [
                {"date": "2026-11-10", "numbers": [1, 2, 3, 4, 5, 6]},
                {"date": target, "numbers": [7, 8, 9, 10, 11, 12]},
            ]
            train = [rows[0]]
            archive_proof = {"archive_sha256": "a" * 64, "rows": rows}
            partition = {"target_date": target, "discovery_rows": train}
            paths = {
                "BASE": root,
                "ARCHIVE": root / "super_loto_archive.json",
                "REPORT": root / "open_research_report.md",
                "FORWARD_REPORT": root / "open_research_forward_report.md",
                "REVERSE_REPORT": root / "open_research_reverse_report.md",
                "PROPOSALS": root / "open_research_proposals.json",
                "EXECUTION_SPECS": root / "open_research_execution_specs.json",
                "USAGE_REPORT": root / "open_research_usage.json",
                "RUN_STATUS": root / "open_research_run_status.json",
            }
            with (
                patch.multiple(research, **paths),
                patch.object(research.sealed, "preflight"),
                patch.object(research, "parse_archive", return_value=rows),
                patch.object(research, "isolate_discovery_only", return_value=(target, train, {})),
                patch.object(research.sealed, "load_archive", return_value=archive_proof),
                patch.object(research.sealed, "load_ledger", return_value={}),
                patch.object(research.sealed, "get_used_validation_dates", return_value=set()),
                patch.object(research.sealed, "split_archive", return_value=partition),
                patch.object(research.sealed, "public_plan", return_value={"target_date": target}),
                patch.object(research.research_memory, "direction_context", return_value=[]),
                patch.object(
                    research, "build_messages",
                    return_value=[{"role": "user", "content": "synthetic isolated request"}],
                ),
                patch.object(
                    research, "call_model",
                    side_effect=["forward report", RuntimeError("synthetic reverse HTTP 504")],
                ) as call_model,
                patch.object(research, "write_run_status") as write_run_status,
                patch.object(research, "parse_directional_result") as parse_result,
                patch.object(research, "combine_directional_results") as combine_results,
                patch.object(research, "write_combined_usage_summary") as usage_summary,
            ):
                result = research.main()

            self.assertEqual(result, 1)
            self.assertEqual(call_model.call_count, 2)
            parse_result.assert_not_called()
            combine_results.assert_not_called()
            usage_summary.assert_not_called()
            self.assertTrue(paths["FORWARD_REPORT"].is_file())
            self.assertFalse(paths["REVERSE_REPORT"].exists())
            self.assertFalse(paths["PROPOSALS"].exists())
            self.assertFalse(paths["EXECUTION_SPECS"].exists())
            failed_status = write_run_status.call_args_list[-1]
            self.assertEqual(failed_status.args[:2], ("FAILED", "reverse_inference"))
            self.assertEqual(failed_status.kwargs["failed_direction"], "reverse")




class OpenResearchWorkflowRegressionTests(unittest.TestCase):
    @staticmethod
    def _workflow_step(path, name):
        workflow_path = Path(__file__).resolve().parent / ".github" / "workflows" / path
        lines = workflow_path.read_text(encoding="utf-8").splitlines()
        marker = "      - name: " + name
        start = next((index for index, line in enumerate(lines) if line == marker), None)
        if start is None:
            raise AssertionError("Workflow step is missing: " + name)
        end = next(
            (
                index for index in range(start + 1, len(lines))
                if lines[index].startswith("      - name: ")
            ),
            len(lines),
        )
        return "\n".join(lines[start:end])

    def test_reverse_504_path_activates_lock_and_never_retries_or_claims_holdout(self):
        workflow = "open-research.yml"
        lock_step = self._workflow_step(
            workflow, "Activate global lock after an incomplete NVIDIA research request"
        )
        self.assertIn(
            "if: ${{ failure() && steps.nvidia-research.outcome == 'failure' }}",
            lock_step,
        )
        self.assertIn('/tmp/general_nvidia_api_attempted_${request_label}', lock_step)
        self.assertIn('/tmp/general_nvidia_api_succeeded_${request_label}', lock_step)
        self.assertIn("python3 general_nvidia_guard.py lock --reason", lock_step)
        self.assertNotIn("rerun", lock_step.lower())
        self.assertNotIn("--retry", lock_step.lower())

        claim_step = self._workflow_step(
            workflow, "Claim one sealed interval before real-archive scoring"
        )
        self.assertIn("if: ${{ success() }}", claim_step)
        evaluation_step = self._workflow_step(
            workflow, "Run one-shot chronological evaluation"
        )
        self.assertIn("steps.sealed-claim.outputs.claim_created == 'true'", evaluation_step)

    def test_failed_research_memory_record_is_not_a_formula_promotion(self):
        memory_step = self._workflow_step(
            "open-research.yml", "Append sanitized GENERAL research memory"
        )
        self.assertIn("steps.nvidia-research.outcome == 'failure'", memory_step)
        self.assertIn("python3 general_research_memory.py record-cycle", memory_step)
        self.assertNotIn("general_method_registry", memory_step)
        self.assertNotIn("promote-candidate", memory_step.lower())

        rule_change_step = self._workflow_step(
            "open-research.yml",
            "Submit a structured rule-change request for Telegram approval if proposed",
        )
        self.assertIn("if: ${{ success() }}", rule_change_step)

    def test_active_global_lock_skips_research_and_wires_telegram_skipped_report(self):
        workflow = "general-autonomous-research.yml"
        skipped_step = self._workflow_step(
            workflow, "Report scheduled cycle skipped by global NVIDIA lock"
        )
        self.assertIn("steps.status.outputs.lock_clear == 'false'", skipped_step)
        self.assertIn("SKIPPED_GLOBAL_LOCK", skipped_step)
        self.assertIn("--status skipped", skipped_step)
        self.assertIn("--kind status", skipped_step)

        prepare_step = self._workflow_step(
            workflow, "Prepare one cycle for the oldest queued target and fresh sealed windows"
        )
        self.assertIn("steps.status.outputs.lock_clear == 'true'", prepare_step)
        attempt_step = self._workflow_step(
            workflow, "Claim this target before any provider request"
        )
        self.assertIn("steps.prepare.outputs.eligible == 'true'", attempt_step)
        research_step = self._workflow_step(
            workflow, "Run independent forward and reverse NVIDIA discovery"
        )
        self.assertIn("steps.attempt_reservation.outputs.attempt_id != ''", research_step)

