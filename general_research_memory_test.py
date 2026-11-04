import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import general_research_memory as memory


def _hash_entry(entry):
    body = {key: value for key, value in entry.items() if key != "content_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def sample_entry(direction="forward", entry_id=1, target="2026-10-20", status="HYPOTHESES_CAPTURED"):
    proposal = {
        "hypothesis_id": "HYP-001" if direction == "forward" else "HYP-501",
        "discovery_direction": direction,
        "proposed_family": "bounded transition hypothesis",
        "hypothesis": "Earlier-state counts may rank later values.",
        "exact_definition": "Count previous occurrences before each chronological cutoff.",
        "inputs": "Strictly earlier R60 history only.",
        "output_rule": "Rank candidates by descending count with a fixed tie-break.",
        "parameter_policy": "No fitted thresholds.",
        "selection_policy": "Select only on the direction's discovery interval.",
        "falsification_criterion": "Reject if forward performance does not exceed the null baseline.",
        "known_risks": ["Multiple testing and nonstationarity."],
        "evaluation_protocols": [
            "forward_walk_forward", "reverse_retrospective_stress_test",
            "time_ordered_cross_validation", "uniform_random_6_of_N",
        ],
    }
    entry = {
        "entry_id": entry_id,
        "cycle_id": hashlib.sha256(f"cycle-{entry_id}-{direction}".encode()).hexdigest(),
        "source": memory.SOURCE,
        "research_epoch": memory.RESEARCH_EPOCH,
        "direction": direction,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_date": target,
        "archive_sha256": "a" * 64,
        "runner_version": "0.9.0",
        "status": status,
        "research_report_sha256": "b" * 64,
        "research_report_content": "Synthetic report narrative for the " + direction + " direction.",
        "research_report_content_sha256": hashlib.sha256(
            ("Synthetic report narrative for the " + direction + " direction.").encode("utf-8")
        ).hexdigest(),
        "research_report_content_status": "STORED",
        "proposal_status": "VALID_PROPOSALS",
        "proposals": [proposal],
        "execution_spec_status": "VALID_EMPTY",
        "execution_spec": None,
        "execution_spec_sha256": None,
        "learning_notes": {"observations": ["The candidate is simple and reproducible."], "failed_approaches": [], "next_tests": ["Compare against a uniform random baseline."]},
        "evaluation_feedback": None,
        "failure": None,
        "discovery_window": {"start_date": "2026-09-01", "end_date": "2026-10-01", "draw_count": 20, "holdout_dates_copied": False, "archive_rows_copied": False},
    }
    entry["content_sha256"] = _hash_entry(entry)
    return entry


class ResearchMemoryValidationTests(unittest.TestCase):
    def test_empty_memory_is_fresh_and_has_no_legacy_import(self):
        value = memory.empty_memory()
        self.assertEqual(value["entries"], [])
        self.assertEqual(value["research_epoch"], memory.RESEARCH_EPOCH)
        self.assertNotIn("general_memory", json.dumps(value).lower())

    def test_valid_entry_passes_schema_and_hash(self):
        entry = sample_entry()
        self.assertEqual(memory._validate_entry(entry, 1), entry)

    def test_report_content_hash_is_required_and_verified(self):
        entry = sample_entry()
        self.assertEqual(entry["research_report_content_status"], "STORED")
        broken = copy.deepcopy(entry)
        broken["research_report_content"] += " changed"
        broken["content_sha256"] = _hash_entry(broken)
        with self.assertRaisesRegex(memory.ResearchMemoryError, "report content or its hash"):
            memory._validate_entry(broken, 1)

    def test_report_content_cannot_retain_number_sets_or_secret_like_values(self):
        for unsafe in (
            "Candidate values: 1, 7, 13, 22, 34, 48.",
            "Provider credential: nvapi-abcdefghijklmnopqrstuvwxyz123456.",
        ):
            entry = sample_entry()
            entry["research_report_content"] = unsafe
            entry["research_report_content_sha256"] = hashlib.sha256(unsafe.encode("utf-8")).hexdigest()
            entry["content_sha256"] = _hash_entry(entry)
            with self.assertRaises(memory.ResearchMemoryError):
                memory._validate_entry(entry, 1)

    def test_report_sanitizer_extracts_machine_blocks_and_preserves_full_narrative(self):
        report = (
            "Narrative section A.\\n"
            "<HYPOTHESIS_PROPOSALS_JSON>machine block</HYPOTHESIS_PROPOSALS_JSON>\\n"
            "Narrative section B."
        )
        sanitized, status = memory._sanitize_research_report(report)
        self.assertEqual(status, "STORED")
        self.assertIn("Narrative section A.", sanitized)
        self.assertIn("Narrative section B.", sanitized)
        self.assertIn("stored in their structured directional fields", sanitized)
        self.assertNotIn("machine block", sanitized)

    def test_report_sanitizer_omits_consumed_dates_number_sets_and_secrets(self):
        cases = (
            ("The consumed validation date was 2026-10-20.", {"2026-10-20"}),
            ("The consumed validation date was 04.11.2026.", {"2026-11-04"}),
            ("The consumed validation date was 11/04/2026.", {"2026-11-04"}),
            ("A list appeared: 1, 7, 13, 22, 34, 48.", set()),
            ("An echoed credential was nvapi-abcdefghijklmnopqrstuvwxyz123456.", set()),
        )
        for report, forbidden_dates in cases:
            with self.subTest(report=report):
                sanitized, status = memory._sanitize_research_report(
                    report,
                    forbidden_validation_dates=forbidden_dates,
                )
                self.assertIsNone(sanitized)
                self.assertEqual(status, "OMITTED_SENSITIVE_CONTENT")

    def test_report_sanitizer_omits_malformed_structured_blocks(self):
        sanitized, status = memory._sanitize_research_report(
            "Narrative <HYPOTHESIS_PROPOSALS_JSON>missing close marker"
        )
        self.assertIsNone(sanitized)
        self.assertEqual(status, "OMITTED_UNSUPPORTED_FORMAT")

    def test_consumed_validation_dates_are_rejected_from_structured_text_too(self):
        with self.assertRaisesRegex(memory.ResearchMemoryError, "consumed validation date"):
            memory._reject_consumed_validation_dates(
                {"hypothesis": "Do not repeat date 2026-10-20."},
                {"2026-10-20"},
                "Synthetic proposal",
            )

    def test_reserved_validation_windows_expand_forward_and_reverse_ranges(self):
        plan = {
            "regimes": {
                "R60": {
                    "validation_start_date": "2026-10-20",
                    "validation_end_date": "2026-10-22",
                    "reverse_validation_start_date": "2026-10-25",
                    "reverse_validation_end_date": "2026-10-26",
                }
            }
        }
        reserved = memory._reserved_validation_dates(plan)
        self.assertEqual(
            reserved,
            {
                "2026-10-20", "2026-10-21", "2026-10-22",
                "2026-10-25", "2026-10-26",
            },
        )
        sanitized, status = memory._sanitize_research_report(
            "The sealed validation interval includes 2026-10-21.",
            forbidden_validation_dates=reserved,
        )
        self.assertIsNone(sanitized)
        self.assertEqual(status, "OMITTED_SENSITIVE_CONTENT")

    def test_invalid_or_oversized_reserved_validation_windows_fail_closed(self):
        invalid_plans = (
            {},
            {"regimes": {}},
            {"regimes": {"R60": {}}},
            {
                "regimes": {"R60": {
                    "validation_start_date": "2026-10-20",
                    "validation_end_date": None,
                }}
            },
            {
                "regimes": {"R60": {
                    "validation_start_date": "not-a-date",
                    "validation_end_date": "2026-10-22",
                }}
            },
            {
                "regimes": {"R60": {
                    "validation_start_date": "2026-10-23",
                    "validation_end_date": "2026-10-22",
                }}
            },
            {
                "regimes": {"R60": {
                    "validation_start_date": "2020-01-01",
                    "validation_end_date": "2030-02-01",
                }}
            },
        )
        for plan in invalid_plans:
            with self.subTest(plan=plan):
                with self.assertRaises(memory.ResearchMemoryError):
                    memory._reserved_validation_dates(plan)

    def test_numeric_freeform_narrative_is_omitted_even_without_a_six_number_set(self):
        sanitized, status = memory._sanitize_research_report(
            "The exploratory approach was reviewed in 4 separate folds."
        )
        self.assertIsNone(sanitized)
        self.assertEqual(status, "OMITTED_NUMERIC_CONTENT")

    def test_structured_candidate_values_and_partial_rankings_are_rejected(self):
        unsafe_values = (
            {"hypothesis": "The candidate is 17."},
            {"output_rule": "Top-ranked values: 17, 22 and 31."},
            {"known_risks": ["Predicted number: 22."]},
            {"hypothesis": "17 is the highest-ranked candidate."},
            {"hypothesis": "Number 17 remains only a candidate value."},
            {"output_rule": "Candidate values include 17 and 22."},
        )
        for value in unsafe_values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    memory.ResearchMemoryError, "candidate/predicted number value"
                ):
                    memory._reject_candidate_value_references(value, "Synthetic proposal")

        memory._reject_candidate_value_references(
            {"exact_definition": "Use MOD5 and fixed 5-fold validation in R60."},
            "Synthetic proposal",
        )

    def test_stored_proposal_cannot_bypass_candidate_value_guard(self):
        entry = sample_entry()
        entry["proposals"][0]["hypothesis"] = "The candidate is 17."
        entry["content_sha256"] = _hash_entry(entry)
        with self.assertRaisesRegex(
            memory.ResearchMemoryError, "candidate/predicted number value"
        ):
            memory._validate_entry(entry, 1)

        entry = sample_entry()
        entry["learning_notes"]["observations"] = [
            "Predicted value 22 is not permitted in persistent memory."
        ]
        entry["content_sha256"] = _hash_entry(entry)
        with self.assertRaisesRegex(
            memory.ResearchMemoryError, "candidate/predicted number value"
        ):
            memory._validate_entry(entry, 1)

    def test_legacy_source_or_epoch_is_rejected(self):
        entry = sample_entry()
        entry["source"] = "GENERAL/legacy"
        entry["content_sha256"] = _hash_entry(entry)
        with self.assertRaisesRegex(memory.ResearchMemoryError, "GENERAL research engine"):
            memory._validate_entry(entry, 1)

    def test_wrong_direction_proposal_is_rejected(self):
        entry = sample_entry("forward")
        entry["proposals"][0]["discovery_direction"] = "reverse"
        entry["content_sha256"] = _hash_entry(entry)
        with self.assertRaisesRegex(memory.ResearchMemoryError, "another direction"):
            memory._validate_entry(entry, 1)

    def test_six_number_set_is_rejected_from_memory(self):
        entry = sample_entry()
        entry["learning_notes"]["observations"] = ["Example output: 3, 9, 15, 24, 36, 50"]
        entry["content_sha256"] = _hash_entry(entry)
        with self.assertRaisesRegex(memory.ResearchMemoryError, "six-number value set"):
            memory._validate_entry(entry, 1)

    def test_six_number_sets_with_common_delimiters_are_rejected(self):
        for value in (
            "1; 5; 9; 12; 20; 45",
            "1 5 9 12 20 45",
            "1/5/9/12/20/45",
            '"1", "5", "9", "12", "20", "45"',
            "01, 07, 13, 22, 34, 48.",
            "1-7-13-22-34-48",
        ):
            with self.subTest(value=value):
                with self.assertRaisesRegex(memory.ResearchMemoryError, "six-number value set"):
                    memory._reject_number_values({"notes": [value]}, "test")
        memory._reject_number_values(
            {"notes": ["Six independent facts: 6 draws, 60 rows, and 5 folds."]},
            "test",
        )

    def test_consumed_feedback_must_not_be_relabelled_as_independent(self):
        entry = sample_entry()
        entry["evaluation_feedback"] = {
            "holdout_consumed": False,
            "retrospective_only": False,
            "holdout": {},
            "cross_validation": {},
        }
        entry["content_sha256"] = _hash_entry(entry)
        with self.assertRaisesRegex(memory.ResearchMemoryError, "safe summary schema"):
            memory._validate_entry(entry, 1)

    def test_memory_rejects_tampered_content_hash(self):
        entry = sample_entry()
        entry["learning_notes"]["observations"] = ["Changed after hashing."]
        value = memory.empty_memory()
        value["entries"] = [entry]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(memory.ResearchMemoryError, "hash mismatch"):
                memory.load_memory(path)


class DirectionalMemoryContextTests(unittest.TestCase):
    def test_context_uses_only_same_direction_and_older_target_entries(self):
        value = memory.empty_memory()
        value["entries"] = [
            sample_entry("forward", 1, "2026-10-20"),
            sample_entry("reverse", 2, "2026-10-20"),
            sample_entry("forward", 3, "2026-11-04"),
            sample_entry("forward", 4, "2026-11-05"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            forward = memory.direction_context("forward", "2026-11-04", memory_path=path)
            reverse = memory.direction_context("reverse", "2026-11-04", memory_path=path)
        self.assertEqual(len(forward), 2)
        self.assertEqual(forward[0]["direction"], "forward")
        self.assertEqual(forward[0]["prior_target_date"], "2026-11-04")
        self.assertEqual(len(reverse), 1)
        self.assertEqual(reverse[0]["direction"], "reverse")
        self.assertNotIn("2026-11-05", json.dumps(forward))

    def test_large_memory_entry_is_compacted_without_discarding_all_feedback(self):
        entry = sample_entry("forward", 1, "2026-11-04")
        for index in range(2, 6):
            proposal = copy.deepcopy(entry["proposals"][0])
            proposal["hypothesis_id"] = f"HYP-{index:03d}"
            for key in (
                "proposed_family", "hypothesis", "exact_definition", "inputs",
                "output_rule", "parameter_policy", "selection_policy",
                "falsification_criterion",
            ):
                proposal[key] = "x" * 1800
            entry["proposals"].append(proposal)
        entry["learning_notes"] = {
            "observations": ["An observation with enough detail. " * 15],
            "failed_approaches": ["A failed approach with enough detail. " * 15],
            "next_tests": ["A next test with enough detail. " * 15],
        }
        entry["content_sha256"] = _hash_entry(entry)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            value = memory.empty_memory()
            value["entries"] = [entry]
            write_json(path, value)
            context = memory.direction_context("forward", "2026-11-04", memory_path=path)
        self.assertEqual(len(context), 1)
        self.assertEqual(context[0]["proposals_in_memory"], 5)
        self.assertEqual(len(context[0]["hypotheses"]), 2)
        self.assertLessEqual(len(context[0]["hypotheses"][1]["exact_definition"]), 850)
        self.assertIn("exact_definition", context[0]["hypotheses"][1]["truncated_fields"])
        self.assertIn("[TRUNCATED]", context[0]["hypotheses"][1]["exact_definition"])
        self.assertLessEqual(len(memory._canonical(context)), memory.MAX_CONTEXT_CHARS)

    def test_direction_context_refuses_invalid_direction(self):
        with self.assertRaisesRegex(memory.ResearchMemoryError, "Direction"):
            memory.direction_context("both", "2026-11-04")

    def test_learning_notes_block_is_structured_and_number_safe(self):
        report = (
            memory.LESSONS_START
            + json.dumps({
                "schema_version": 1,
                "observations": ["A fixed tie-break improves reproducibility."],
                "failed_approaches": ["A moving threshold was not reproducible."],
                "next_tests": ["Compare the fixed rule with a null baseline."],
            })
            + memory.LESSONS_END
        )
        notes = memory._parse_learning_notes(report)
        self.assertEqual(len(notes["observations"]), 1)
        bad = report.replace("A fixed tie-break improves reproducibility.", "Prediction: 3, 9, 15, 24, 36, 50")
        with self.assertRaisesRegex(memory.ResearchMemoryError, "six-number value set"):
            memory._parse_learning_notes(bad)
        partial = report.replace(
            "A fixed tie-break improves reproducibility.",
            "The candidate is 17 and remains unverified.",
        )
        with self.assertRaisesRegex(
            memory.ResearchMemoryError, "candidate/predicted number value"
        ):
            memory._parse_learning_notes(partial)


def directional_report(direction):
    source_id = "HYP-001"
    proposal = {
        "hypothesis_id": source_id,
        "discovery_direction": direction,
        "proposed_family": "bounded transition hypothesis",
        "hypothesis": "Earlier-state counts may rank later values.",
        "exact_definition": "Count previous occurrences before each chronological cutoff.",
        "inputs": "Strictly earlier R60 history only.",
        "output_rule": "Rank candidates by descending count with a fixed tie-break.",
        "parameter_policy": "No fitted thresholds.",
        "selection_policy": "Select only on the direction's discovery interval.",
        "falsification_criterion": "Reject if the result does not exceed a uniform random baseline.",
        "known_risks": ["Multiple testing and nonstationarity."],
        "evaluation_protocols": [
            "forward_walk_forward", "reverse_retrospective_stress_test",
            "time_ordered_cross_validation", "uniform_random_6_of_N",
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
    return (
        "Complete narrative report for " + direction + " direction.\\n\\n"
        "<HYPOTHESIS_PROPOSALS_JSON>"
        + json.dumps({"schema_version": 2, "proposals": [proposal]})
        + "</HYPOTHESIS_PROPOSALS_JSON>"
        + "<HYPOTHESIS_EXECUTION_SPECS_JSON>"
        + json.dumps({"schema_version": 1, "specifications": [{"hypothesis_id": source_id, "execution_spec": spec}]})
        + "</HYPOTHESIS_EXECUTION_SPECS_JSON>"
        + memory.LESSONS_START
        + json.dumps({
            "schema_version": 1,
            "observations": ["The fixed definition is reproducible."],
            "failed_approaches": ["A moving threshold was not stable."],
            "next_tests": ["Compare the candidate against a null baseline."],
        })
        + memory.LESSONS_END
    )


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ResearchMemoryPersistenceTests(unittest.TestCase):
    def test_append_only_writer_refuses_to_modify_existing_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            first = sample_entry("forward", 1)
            payload = memory.empty_memory()
            payload["entries"] = [first]
            write_json(path, payload)
            appended = copy.deepcopy(payload)
            appended["entries"].append(sample_entry("reverse", 2))
            memory._write_memory(appended, path)
            tampered = copy.deepcopy(appended)
            tampered["entries"][0]["learning_notes"]["observations"] = ["Old record changed."]
            tampered["entries"][0]["content_sha256"] = _hash_entry(tampered["entries"][0])
            tampered["entries"].append(sample_entry("forward", 3, "2026-10-22"))
            with self.assertRaisesRegex(memory.ResearchMemoryError, "not append-only"):
                memory._write_memory(tampered, path)
            backdated = copy.deepcopy(appended)
            backdated["entries"].append(sample_entry("forward", 3, "2026-10-19"))
            with self.assertRaisesRegex(memory.ResearchMemoryError, "chronology backwards"):
                memory._write_memory(backdated, path)

    def test_record_cycle_persists_only_directional_hypotheses_and_evaluation_feedback(self):
        from general_sealed_evaluation import load_archive

        rows = [
            {"date": "2026-11-01", "numbers": [1, 7, 13, 22, 34, 48]},
            {"date": "2026-11-02", "numbers": [2, 8, 14, 23, 35, 49]},
            {"date": "2026-11-03", "numbers": [3, 9, 15, 24, 36, 50]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "archive.json"
            archive_bytes = json.dumps({"draws": rows}, ensure_ascii=False, indent=2).encode("utf-8")
            archive_path.write_bytes(archive_bytes)
            archive = load_archive(archive_path)
            target = archive["last_date"]
            archive_hash = archive["archive_sha256"]
            ledger_path = root / "ledger.json"
            claim_id = "d" * 64
            write_json(ledger_path, {
                "schema_version": 1,
                "purpose": "append-only test ledger",
                "entries": [{
                    "claim_id": claim_id,
                    "state": "EVALUATED",
                    "target_date": target,
                    "archive_sha256": archive_hash,
                    "validation_dates": ["2026-10-20", "2026-10-21"],
                    "reverse_validation_dates": ["2026-10-10", "2026-10-11"],
                }],
            })
            write_json(root / "run-status.json", {
                "schema_version": 1,
                "status": "RESEARCH_COMPLETE",
                "stage": "research_complete",
                "target_date": target,
                "archive_sha256": archive_hash,
                "failed_direction": None,
                "error_type": None,
                "target_numbers_stored": False,
                "archive_rows_stored": False,
            })
            (root / "forward.md").write_text(directional_report("forward"), encoding="utf-8")
            (root / "reverse.md").write_text(directional_report("reverse"), encoding="utf-8")
            write_json(root / "sealed-plan.json", {
                "regimes": {
                    "R60": {
                        "discovery_start_date": "2026-09-01",
                        "discovery_end_date": "2026-10-01",
                        "discovery_count": 20,
                    }
                }
            })
            write_json(root / "claim.json", {"claim_created": True, "claim_id": claim_id})

            spec = json.loads(
                directional_report("forward").split("<HYPOTHESIS_EXECUTION_SPECS_JSON>")[1]
                .split("</HYPOTHESIS_EXECUTION_SPECS_JSON>")[0]
            )["specifications"][0]["execution_spec"]
            spec_hash = hashlib.sha256(
                json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest()
            evaluation = {
                "status": "EVALUATED_EXPLORATORY_FIXED_SPEC",
                "claim_id": claim_id,
                "archive_sha256": archive_hash,
                "target_date": target,
                "current_isolated_target_included": False,
                "current_target_numbers_exposed": False,
                "target_or_future_rows_used": False,
                "per_draw_target_numbers_reported": False,
                "candidate_rankings_reported": False,
                "candidate_selection": {
                    "forward_hypothesis_id": "HYP-001",
                    "forward_spec_sha256": spec_hash,
                    "reverse_hypothesis_id": "HYP-501",
                    "reverse_spec_sha256": spec_hash,
                    "selection_used_validation_scores": False,
                },
                "forward_walk_forward": {
                    "mean_hits_out_of_6": 0.71,
                    "standard_error_of_mean_hits": 0.08,
                    "uniform_random_expected_hits_out_of_6": 0.6,
                    "validation_draw_count": 10,
                    "hit_distribution": {"0_hits": 1, "1_hit": 3, "2_hits": 4, "3_or_more_hits": 2},
                    "per_draw": [{"test_date": "2026-10-20", "hit_count": 1}],
                },
                "time_ordered_cross_validation": {
                    "folds": 5,
                    "validation_draw_count": 10,
                    "mean_hits_out_of_6": 0.70,
                    "uniform_random_expected_hits_out_of_6": 0.6,
                },
                "reverse_discovered_candidate_holdout": {
                    "test_draw_count": 10,
                    "mean_hits_out_of_6": 0.62,
                    "uniform_random_expected_hits_out_of_6": 0.6,
                    "retrospective_only": True,
                },
                "reverse_time_ordered_cross_validation": {
                    "folds": 5,
                    "validation_draw_count": 10,
                    "mean_hits_out_of_6": 0.61,
                    "uniform_random_expected_hits_out_of_6": 0.6,
                    "predictive_evidence": False,
                },
            }
            write_json(root / "evaluation.json", evaluation)

            memory_path = root / "general_research_memory.json"
            result = memory.record_cycle(
                memory_path=memory_path,
                archive_path=archive_path,
                ledger_path=ledger_path,
                run_status_path=root / "run-status.json",
                forward_report_path=root / "forward.md",
                reverse_report_path=root / "reverse.md",
                sealed_plan_path=root / "sealed-plan.json",
                claim_path=root / "claim.json",
                evaluation_path=root / "evaluation.json",
                run_id="123456789",
                run_attempt="1",
            )
            saved = memory.load_memory(memory_path)
            serialized = json.dumps(saved, ensure_ascii=False)
            forward_context = memory.direction_context("forward", "2026-11-04", memory_path=memory_path)
            reverse_context = memory.direction_context("reverse", "2026-11-04", memory_path=memory_path)

            self.assertEqual(result["entries_added"], 2)
            self.assertEqual([item["status"] for item in saved["entries"]], ["EVALUATED", "EVALUATED"])
            self.assertEqual(saved["entries"][0]["proposals"][0]["hypothesis_id"], "HYP-001")
            self.assertEqual(saved["entries"][1]["proposals"][0]["hypothesis_id"], "HYP-501")
            self.assertTrue(saved["entries"][0]["evaluation_feedback"]["holdout_consumed"])
            invalid_feedback_entry = copy.deepcopy(saved["entries"][0])
            invalid_feedback_entry["evaluation_feedback"]["holdout_consumed"] = False
            invalid_feedback_entry["content_sha256"] = _hash_entry(invalid_feedback_entry)
            with self.assertRaisesRegex(memory.ResearchMemoryError, "permanently consumed"):
                memory._validate_entry(invalid_feedback_entry, 1)
            self.assertFalse(saved["entries"][0]["evaluation_feedback"]["retrospective_only"])
            self.assertTrue(saved["entries"][1]["evaluation_feedback"]["holdout_consumed"])
            self.assertTrue(saved["entries"][1]["evaluation_feedback"]["retrospective_only"])
            self.assertNotIn('"per_draw":', serialized)
            self.assertFalse(saved["entries"][0]["evaluation_feedback"]["per_draw_results_stored"])
            self.assertNotIn("validation_dates", serialized)
            self.assertNotIn("reverse_validation_dates", serialized)
            self.assertNotIn("numbers", saved["entries"][0])
            self.assertEqual(len(forward_context), 1)
            self.assertEqual(len(reverse_context), 1)
            self.assertEqual(forward_context[0]["direction"], "forward")
            self.assertEqual(reverse_context[0]["direction"], "reverse")
            self.assertIn("Complete narrative report for forward direction.", forward_context[0]["research_report_content"])
            self.assertNotIn("Complete narrative report for reverse direction.", forward_context[0]["research_report_content"])
            self.assertIn("Complete narrative report for reverse direction.", reverse_context[0]["research_report_content"])
            self.assertNotIn("Complete narrative report for forward direction.", reverse_context[0]["research_report_content"])
            self.assertTrue(forward_context[0]["research_report_content_is_unverified"])
            self.assertTrue(reverse_context[0]["research_report_content_is_unverified"])
            self.assertEqual(saved["entries"][0]["research_report_content_status"], "STORED")

            # The memory writer cross-checks report prose against every sealed
            # validation date already consumed in the authoritative ledger.
            (root / "forward.md").write_text(
                directional_report("forward") + "\\nConsumed validation date: 2026-10-20.",
                encoding="utf-8",
            )
            withheld_path = root / "withheld-report-memory.json"
            withheld_result = memory.record_cycle(
                memory_path=withheld_path,
                archive_path=archive_path,
                ledger_path=ledger_path,
                run_status_path=root / "run-status.json",
                forward_report_path=root / "forward.md",
                reverse_report_path=root / "reverse.md",
                sealed_plan_path=root / "sealed-plan.json",
                claim_path=root / "claim.json",
                evaluation_path=root / "evaluation.json",
                run_id="123456791",
                run_attempt="1",
            )
            withheld = memory.load_memory(withheld_path)
            self.assertEqual(withheld_result["entries_added"], 2)
            self.assertEqual(
                withheld["entries"][0]["research_report_content_status"],
                "OMITTED_SENSITIVE_CONTENT",
            )
            self.assertIsNone(withheld["entries"][0]["research_report_content"])
            self.assertEqual(
                withheld["entries"][1]["research_report_content_status"],
                "STORED",
            )
            self.assertNotIn("2026-10-20", json.dumps(withheld, ensure_ascii=False))
            (root / "forward.md").write_text(directional_report("forward"), encoding="utf-8")

            # A local evaluation report is not trusted while the append-only
            # ledger still says the claim is only CLAIMED.
            claimed_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            claimed_ledger["entries"][0]["state"] = "CLAIMED"
            write_json(ledger_path, claimed_ledger)
            claimed_memory_path = root / "claimed-memory.json"
            claimed_result = memory.record_cycle(
                memory_path=claimed_memory_path,
                archive_path=archive_path,
                ledger_path=ledger_path,
                run_status_path=root / "run-status.json",
                forward_report_path=root / "forward.md",
                reverse_report_path=root / "reverse.md",
                sealed_plan_path=root / "sealed-plan.json",
                claim_path=root / "claim.json",
                evaluation_path=root / "evaluation.json",
                run_id="123456790",
                run_attempt="1",
            )
            claimed_memory = memory.load_memory(claimed_memory_path)
            self.assertEqual(claimed_result["entries_added"], 2)
            self.assertTrue(all(item["status"] == "CLAIMED_EVALUATION_FAILED" for item in claimed_memory["entries"]))
            self.assertTrue(all(item["evaluation_feedback"] is None for item in claimed_memory["entries"]))
            self.assertTrue(all(item["failure"]["failure_metadata_only"] for item in claimed_memory["entries"]))

            with self.assertRaisesRegex(memory.ResearchMemoryError, "already exists"):
                memory.record_cycle(
                    memory_path=memory_path,
                    archive_path=archive_path,
                    ledger_path=ledger_path,
                    run_status_path=root / "run-status.json",
                    forward_report_path=root / "forward.md",
                    reverse_report_path=root / "reverse.md",
                    sealed_plan_path=root / "sealed-plan.json",
                    claim_path=root / "claim.json",
                    evaluation_path=root / "evaluation.json",
                    run_id="123456789",
                    run_attempt="1",
                )


class ResearchMemoryWorkflowWiringTests(unittest.TestCase):
    def test_manual_research_workflow_persists_memory_with_archive_and_ledger(self):
        workflow = Path(".github/workflows/open-research.yml").read_text(encoding="utf-8")
        self.assertIn("python3 general_research_memory.py record-cycle", workflow)
        self.assertIn("general_research_memory.json", workflow)
        self.assertIn("general_evaluation_ledger.json", workflow)
        self.assertIn("super_loto_archive.json", workflow)
        self.assertIn("git rebase origin/main", workflow)
        self.assertIn("general_research_memory_test.py", workflow)

    def test_autonomous_workflow_persists_memory_without_enabling_automation(self):
        workflow = Path(".github/workflows/general-autonomous-research.yml").read_text(encoding="utf-8")
        self.assertIn("python3 general_research_memory.py record-cycle", workflow)
        self.assertIn("general_research_memory.json", workflow)
        self.assertIn("general_evaluation_ledger.json", workflow)
        self.assertIn("general_autonomous_research_state.json", workflow)
        self.assertIn("git rebase origin/main", workflow)
        self.assertIn("steps.attempt_reservation.outputs.attempt_id != ''", workflow)




if __name__ == "__main__":
    unittest.main(verbosity=2)
