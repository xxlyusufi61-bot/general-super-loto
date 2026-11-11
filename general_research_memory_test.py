import contextlib
import copy
import hashlib
import io
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import general_autonomous_research as autonomous
import general_research_memory as memory
import general_offline_hypothesis_discovery as offline
import random
from general_nvidia_guard import initial_lock_state


def _hash_entry(entry):
    body = {key: value for key, value in entry.items() if key != "content_sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def sample_entry(direction="forward", entry_id=1, target="2026-10-20", status="HYPOTHESES_CAPTURED"):
    raw_proposal = {
        "hypothesis_id": "HYP-001" if direction == "forward" else "HYP-501",
        "discovery_direction": direction,
        "proposed_family": "bounded transition hypothesis",
        "research_rationale": "A simple historical relation is worth testing against chronological null baselines.",
        "known_risks": ["Multiple testing and nonstationarity."],
        "formal_execution_spec": None,
    }
    validated_proposal = memory._validate_proposal(raw_proposal)
    proposal = {key: validated_proposal[key] for key in memory.PROPOSAL_KEYS}
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
        "learning_notes": {
            "observations": ["The candidate is simple and reproducible."],
            "failed_approaches": [],
            "next_tests": ["Compare against a uniform random baseline."],
        },
        "evaluation_feedback": None,
        "failure": None,
        "discovery_window": {
            "start_date": "2026-09-01",
            "end_date": "2026-10-01",
            "draw_count": 20,
            "holdout_dates_copied": False,
            "archive_rows_copied": False,
        },
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


    def test_written_month_dates_are_rejected_when_they_match_reserved_holdouts(self):
        for report in (
            "The sealed date is October 21, 2026.",
            "Mühürlü tarih 21 Ekim 2026 olarak not edildi.",
        ):
            with self.subTest(report=report):
                self.assertIsNone(
                    memory._sanitize_research_report(
                        report,
                        forbidden_validation_dates={"2026-10-21"},
                    )[0]
                )
                with self.assertRaisesRegex(memory.ResearchMemoryError, "reserved validation date"):
                    memory._reject_consumed_validation_dates(
                        {"hypothesis": report},
                        {"2026-10-21"},
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

    def test_benign_mathematical_class_sizes_are_not_misclassified_as_predictions(self):
        safe_values = (
            "MOD5 residue classes each contain 12 numbers, giving a uniformity baseline.",
            "Each of the five residue classes contains twelve numbers.",
            "The expected count is 1.2 numbers per draw for each class.",
            "The 12-number class size is a combinatorial property, not a prediction.",
            "Twelve values belong to each residue class in the 1-to-60 pool.",
        )
        for value in safe_values:
            with self.subTest(value=value):
                memory._reject_candidate_value_references({"observations": [value]}, "Safe note")

    def test_single_drawn_number_values_are_rejected_without_blocking_math_counts(self):
        unsafe_values = (
            "Drawn number: 17.",
            "17 was drawn.",
            "17 has been drawn.",
            "The drawn number was 17.",
            "Çekilen sayı: 17.",
            "Çekilen sayı on yedi.",
            "17 çekildi.",
            "17 numarası çekildi.",
        )
        for value in unsafe_values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    memory.ResearchMemoryError, "candidate/predicted number value"
                ):
                    memory._reject_candidate_value_references(value, "Unsafe drawn value")

        safe_values = (
            "Each MOD5 class contains 12 numbers.",
            "17 observations were drawn from a synthetic sample.",
            "The experiment drew 17 observations from a synthetic distribution.",
            "Each residue class contains twelve values.",
        )
        for value in safe_values:
            with self.subTest(value=value):
                memory._reject_candidate_value_references(value, "Safe mathematical note")

        for value in ("17 was drawn.", "Çekilen sayı: 17.", "17 çekildi."):
            payload = {
                "schema_version": 1,
                "observations": [value],
                "failed_approaches": [],
                "next_tests": [],
            }
            report = (
                memory.LESSONS_START
                + json.dumps(payload, ensure_ascii=False)
                + memory.LESSONS_END
            )
            with self.subTest(learning_note=value):
                with self.assertRaisesRegex(
                    memory.ResearchMemoryError, "candidate/predicted number value"
                ):
                    memory._parse_learning_notes(report)

            entry = sample_entry()
            entry["learning_notes"]["observations"] = [value]
            entry["content_sha256"] = _hash_entry(entry)
            with self.subTest(stored_learning_note=value):
                with self.assertRaisesRegex(
                    memory.ResearchMemoryError, "candidate/predicted number value"
                ):
                    memory._validate_entry(entry, 1)

    def test_candidate_value_and_partial_ranking_phrases_remain_blocked(self):
        unsafe_values = (
            "Candidate number: 17.",
            "Predicted value 22.",
            "Top-ranked numbers: 17 and 22.",
            "Numbers: 17, 24.",
            "Values: 22.",
            "Numbers are 17 and 24.",
            "Sayılar: on yedi ve yirmi dört.",
            "Values: seventeen and twenty four.",
            "17 is the highest-ranked candidate.",
            "The candidate is 17 and remains unverified.",
            "Aday sayısı: on iki.",
            "Aday değer on yedi olarak seçildi.",
            "Tahmin edilen sayı on yedi.",
            "Kazanan değer: yirmi dört.",
            "On yedi aday olarak belirlendi.",
        )
        for value in unsafe_values:
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    memory.ResearchMemoryError, "candidate/predicted number value"
                ):
                    memory._reject_candidate_value_references(value, "Unsafe note")

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


    def test_written_number_sets_and_candidate_values_are_rejected(self):
        unsafe_sets = (
            "Candidate values: three, nine, fifteen, twenty-four, thirty-six and fifty.",
            "Aday sayıları: üç, dokuz, on beş, yirmi dört, otuz altı ve elli.",
            "The winning numbers were 3, 9, 15, 24, 36 and 50.",
        )
        for value in unsafe_sets:
            with self.subTest(value=value):
                with self.assertRaisesRegex(memory.ResearchMemoryError, "six-number value set"):
                    memory._reject_number_values({"notes": [value]}, "test")

        unsafe_candidates = (
            {"hypothesis": "The winning number is seventeen."},
            {"hypothesis": "The top-ranked value is twenty-four."},
            {"hypothesis": "17 is the highest-ranked candidate."},
            {"hypothesis": "Aday değer on yedi olarak seçildi."},
            {"hypothesis": "Aday sayıları: on yedi."},
            {"hypothesis": "Kazanan değerleri: yirmi dört."},
            {"hypothesis": "On yedi aday olarak belirlendi."},
            {"output_rule": "Predicted values: three, nine and fifteen."},
        )
        for value in unsafe_candidates:
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    memory.ResearchMemoryError, "candidate/predicted number value"
                ):
                    memory._reject_candidate_value_references(value, "Synthetic proposal")

        memory._reject_candidate_value_references(
            {"exact_definition": "Use MOD5 with fixed 5-fold validation in R60."},
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

    def test_stored_entries_reject_inflected_turkish_candidate_values(self):
        entry = sample_entry()
        entry["proposals"][0]["hypothesis"] = "Aday sayıları: on yedi."
        entry["content_sha256"] = _hash_entry(entry)
        with self.assertRaisesRegex(
            memory.ResearchMemoryError, "candidate/predicted number value"
        ):
            memory._validate_entry(entry, 1)

        entry = sample_entry()
        entry["learning_notes"]["observations"] = [
            "Kazanan değerleri: yirmi dört."
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
        self.assertNotIn("2026-11-05", [item["prior_target_date"] for item in forward])

    def test_large_memory_entry_is_compacted_without_discarding_all_feedback(self):
        entry = sample_entry("forward", 1, "2026-11-04")
        entry["proposals"][0]["proposed_family"] = "A" * 1800
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
        self.assertEqual(context[0]["proposals_in_memory"], 1)
        self.assertEqual(len(context[0]["hypotheses"]), 1)
        self.assertLessEqual(len(context[0]["hypotheses"][0]["proposed_family"]), 180)
        self.assertIn("proposed_family", context[0]["hypotheses"][0]["truncated_fields"])
        self.assertIn("[TRUNCATED]", context[0]["hypotheses"][0]["proposed_family"])
        self.assertLessEqual(len(memory._canonical(context)), memory.MAX_CONTEXT_CHARS)

    def test_offline_context_keeps_every_candidate_and_prior_directional_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "archive.json"
            memory_path = root / "research-memory.json"
            report_path = root / "offline-report.json"
            generator = random.Random(20261111)
            start = date(2020, 8, 2)
            archive = [
                {
                    "date": (start + timedelta(days=index * 3)).isoformat(),
                    "numbers": sorted(generator.sample(range(1, 61), 6)),
                }
                for index in range(120)
            ]
            archive_path.write_text(
                json.dumps(archive, separators=(",", ":")),
                encoding="utf-8",
            )
            result = offline.run_cycle(
                archive_path=archive_path,
                sealed_ledger_path=root / "missing-sealed-ledger.json",
                memory_path=memory_path,
                report_path=report_path,
            )
            self.assertEqual(result["candidate_count"], offline.MAX_CANDIDATES)

            persisted = memory.load_memory(memory_path)
            stored_candidates = persisted["offline_research"]["entries"][0]["candidates"]
            target = result["target_date"]
            persisted["entries"] = [
                sample_entry("forward", 1, target),
                sample_entry("forward", 2, target),
            ]
            memory_path.write_bytes(memory._canonical(persisted) + b"\n")
            before_context = memory_path.read_bytes()

            forward_context = memory.direction_context(
                "forward", target, memory_path=memory_path
            )
            reverse_context = memory.direction_context(
                "reverse", target, memory_path=memory_path
            )

            self.assertEqual(memory_path.read_bytes(), before_context)

        offline_context = next(
            item for item in forward_context
            if item.get("source") == "GENERAL/offline-symbolic-discovery"
        )
        self.assertEqual(offline_context["context_schema_version"], 2)
        self.assertEqual(offline_context["candidate_context_truncated"], False)
        self.assertEqual(
            offline_context["candidates_in_context"],
            len(stored_candidates),
        )
        self.assertEqual(
            offline_context["candidates_in_memory"],
            len(stored_candidates),
        )
        hypothesis_id_index = offline_context["candidate_fields"].index("hypothesis_id")
        self.assertEqual(
            [row[hypothesis_id_index] for row in offline_context["candidates"]],
            [candidate["hypothesis_id"] for candidate in stored_candidates],
        )
        self.assertTrue(
            all(len(row) == len(offline_context["candidate_fields"])
                for row in offline_context["candidates"])
        )
        self.assertLessEqual(
            len(memory._canonical([offline_context])),
            memory.MAX_OFFLINE_CONTEXT_CHARS,
        )
        self.assertLessEqual(
            len(memory._canonical(forward_context)),
            memory.MAX_CONTEXT_CHARS,
        )
        prior_forward_entries = [
            item for item in forward_context if item.get("source") == memory.SOURCE
        ]
        self.assertEqual(len(prior_forward_entries), 2)
        self.assertTrue(all(item["direction"] == "forward" for item in prior_forward_entries))
        self.assertEqual(reverse_context, [])
        self.assertNotIn("draw_numbers", json.dumps(forward_context))

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
        benign = report.replace(
            "A fixed tie-break improves reproducibility.",
            "MOD5 residue classes each contain 12 numbers.",
        )
        parsed_benign = memory._parse_learning_notes(benign)
        self.assertEqual(
            parsed_benign["observations"],
            ["MOD5 residue classes each contain 12 numbers."],
        )
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
    proposal = {
        "hypothesis_id": source_id,
        "discovery_direction": direction,
        "proposed_family": "bounded transition hypothesis",
        "research_rationale": "A simple count baseline is being tested against a chronological null.",
        "known_risks": ["Multiple testing and nonstationarity."],
        "formal_execution_spec": spec,
    }
    return (
        "Complete narrative report for " + direction + " direction.\n\n"
        "<HYPOTHESIS_PROPOSALS_JSON>"
        + json.dumps({"schema_version": 3, "proposals": [proposal]})
        + "</HYPOTHESIS_PROPOSALS_JSON>"
        + "<HYPOTHESIS_EXECUTION_SPECS_JSON>"
        + json.dumps({"schema_version": 1, "specifications": [{"hypothesis_id": source_id, "execution_spec": spec}]})
        + "</HYPOTHESIS_EXECUTION_SPECS_JSON>"
        + memory.LESSONS_START
        + json.dumps({
            "schema_version": 1,
            "observations": ["The fixed definition is reproducible."],
            "failed_approaches": ["A moving threshold was not reproducible."],
            "next_tests": ["Compare the candidate against a null baseline."],
        })
        + memory.LESSONS_END
    )

def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")



def make_archive_and_plan(root, used_validation_dates=None):
    from general_sealed_evaluation import load_archive, public_plan, split_archive

    start = date(2026, 9, 1)
    rows = []
    for index in range(60):
        draw_day = (start + timedelta(days=index)).isoformat()
        base = (index * 7) % 60 + 1
        numbers = sorted({((base + offset * 11 - 1) % 60) + 1 for offset in range(6)})
        rows.append({"date": draw_day, "numbers": numbers})
    target_base = (60 * 7) % 60 + 1
    rows.append({
        "date": (start + timedelta(days=60)).isoformat(),
        "numbers": sorted({((target_base + offset * 11 - 1) % 60) + 1 for offset in range(6)}),
    })
    archive_path = Path(root) / "archive.json"
    write_json(archive_path, {"draws": rows})
    archive = load_archive(archive_path)
    partition = split_archive(
        rows,
        archive_sha256=archive["archive_sha256"],
        used_validation_dates=set(used_validation_dates or set()),
    )
    return archive_path, archive, partition, public_plan(partition)


class SealedPlanBindingTests(unittest.TestCase):
    def test_plan_must_match_exact_archive_target_and_derived_partition(self):
        with tempfile.TemporaryDirectory() as directory:
            archive_path, archive, partition, plan = make_archive_and_plan(directory)
            ledger = {"entries": []}
            memory._validate_sealed_plan_binding(plan, archive, ledger, None)

            changed_target = copy.deepcopy(plan)
            changed_target["target_date"] = "2026-10-30"
            with self.assertRaisesRegex(memory.ResearchMemoryError, "archive/target isolation"):
                memory._validate_sealed_plan_binding(changed_target, archive, ledger, None)

            changed_hash = copy.deepcopy(plan)
            changed_hash["archive_sha256"] = "f" * 64
            with self.assertRaisesRegex(memory.ResearchMemoryError, "archive/target isolation"):
                memory._validate_sealed_plan_binding(changed_hash, archive, ledger, None)

            changed_window = copy.deepcopy(plan)
            changed_window["regimes"]["R60"]["validation_end_date"] = (
                changed_window["regimes"]["R60"]["validation_start_date"]
            )
            with self.assertRaisesRegex(memory.ResearchMemoryError, "archive-derived chronological partition"):
                memory._validate_sealed_plan_binding(changed_window, archive, ledger, None)

    def test_claimed_plan_uses_immutable_prior_consumption_snapshot(self):
        from general_sealed_evaluation import public_plan, split_archive

        with tempfile.TemporaryDirectory() as directory:
            archive_path, archive, _unused_partition, _unused_plan = make_archive_and_plan(directory)
            rows = archive["rows"]
            prior_date = rows[0]["date"]
            prior_dates = {prior_date}
            partition = split_archive(
                rows,
                archive_sha256=archive["archive_sha256"],
                used_validation_dates=prior_dates,
            )
            plan = public_plan(partition)
            current = partition["regimes"]["R60"]
            claim_id = "c" * 64
            ledger = {
                "entries": [{
                    "claim_id": claim_id,
                    "target_date": archive["last_date"],
                    "archive_sha256": archive["archive_sha256"],
                    "previously_consumed_validation_dates": [prior_date],
                    "validation_dates": [row["date"] for row in current["validation_rows"]],
                    "reverse_validation_dates": [row["date"] for row in current["reverse_validation_rows"]],
                }]
            }
            claim = {
                "claim_created": True,
                "claim_id": claim_id,
                "target_date": archive["last_date"],
                "archive_sha256": archive["archive_sha256"],
            }
            memory._validate_sealed_plan_binding(plan, archive, ledger, claim)

            ledger["entries"][0]["previously_consumed_validation_dates"] = []
            with self.assertRaisesRegex(memory.ResearchMemoryError, "archive-derived chronological partition"):
                memory._validate_sealed_plan_binding(plan, archive, ledger, claim)


class ResearchMemoryPersistenceTests(unittest.TestCase):
    def test_append_only_writer_validates_only_new_entries_and_preserves_chronology(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            first = sample_entry("forward", 1, "2026-11-03")
            second = sample_entry("forward", 2, "2026-11-05")
            payload = memory.empty_memory()
            payload["entries"] = [first, second]
            write_json(path, payload)

            # Regression for the production state: two existing dates, then a
            # new recovery event for the same latest target date.
            appended = copy.deepcopy(payload)
            appended["entries"].append(sample_entry("forward", 3, "2026-11-05"))
            memory._write_memory(appended, path)
            saved = memory.load_memory(path)
            self.assertEqual(
                [item["target_date"] for item in saved["entries"]],
                ["2026-11-03", "2026-11-05", "2026-11-05"],
            )
            self.assertEqual(saved["entries"][:2], payload["entries"])

            # Existing history remains immutable even when a caller recomputes
            # a modified entry hash.
            tampered = copy.deepcopy(saved)
            tampered["entries"][0]["learning_notes"]["observations"] = ["Old record changed."]
            tampered["entries"][0]["content_sha256"] = _hash_entry(tampered["entries"][0])
            tampered["entries"].append(sample_entry("forward", 4, "2026-11-06"))
            with self.assertRaisesRegex(memory.ResearchMemoryError, "not append-only"):
                memory._write_memory(tampered, path)

            # The suffix check must still reject a target date that moves
            # backwards relative to the final persisted target.
            backdated = copy.deepcopy(saved)
            backdated["entries"].append(sample_entry("reverse", 4, "2026-11-04"))
            with self.assertRaisesRegex(memory.ResearchMemoryError, "chronology backwards"):
                memory._write_memory(backdated, path)

    def test_record_cycle_persists_only_directional_hypotheses_and_evaluation_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path, archive, partition, sealed_plan = make_archive_and_plan(root)
            target = archive["last_date"]
            archive_hash = archive["archive_sha256"]
            r60 = partition["regimes"]["R60"]
            forward_validation_dates = [row["date"] for row in r60["validation_rows"]]
            reverse_validation_dates = [row["date"] for row in r60["reverse_validation_rows"]]
            consumed_date = forward_validation_dates[0]
            reserved_date = forward_validation_dates[1]
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
                    "previously_consumed_validation_dates": [],
                    "validation_dates": forward_validation_dates,
                    "reverse_validation_dates": reverse_validation_dates,
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
            write_json(root / "sealed-plan.json", sealed_plan)
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
                    "per_draw": [{"test_date": consumed_date, "hit_count": 1}],
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
                directional_report("forward") + "\\nConsumed validation date: " + consumed_date + ".",
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
            self.assertNotIn(consumed_date, json.dumps(withheld, ensure_ascii=False))

            # A validation-window date is reserved before it appears in the
            # append-only ledger, so an echoed date from that window is still
            # omitted from the directional narrative.
            (root / "forward.md").write_text(
                directional_report("forward") + "\\nReserved validation date: " + reserved_date + ".",
                encoding="utf-8",
            )
            reserved_path = root / "reserved-report-memory.json"
            reserved_result = memory.record_cycle(
                memory_path=reserved_path,
                archive_path=archive_path,
                ledger_path=ledger_path,
                run_status_path=root / "run-status.json",
                forward_report_path=root / "forward.md",
                reverse_report_path=root / "reverse.md",
                sealed_plan_path=root / "sealed-plan.json",
                claim_path=root / "claim.json",
                evaluation_path=root / "evaluation.json",
                run_id="123456792",
                run_attempt="1",
            )
            reserved_memory = memory.load_memory(reserved_path)
            self.assertEqual(reserved_result["entries_added"], 2)
            self.assertEqual(
                reserved_memory["entries"][0]["research_report_content_status"],
                "OMITTED_SENSITIVE_CONTENT",
            )
            self.assertIsNone(reserved_memory["entries"][0]["research_report_content"])
            self.assertNotIn(reserved_date, json.dumps(reserved_memory, ensure_ascii=False))
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
    def test_manual_research_workflow_persists_runtime_objects_to_state_store(self):
        workflow = Path(".github/workflows/open-research.yml").read_text(encoding="utf-8")
        self.assertIn("python3 general_research_memory.py record-cycle", workflow)
        self.assertIn("objects: archive", workflow)
        self.assertIn("objects: evaluation_ledger", workflow)
        self.assertIn("objects: research_memory", workflow)
        self.assertIn("general_evaluation_snapshots/", workflow)
        self.assertIn("git push origin HEAD:main", workflow)
        self.assertNotIn("git rebase origin/main", workflow)
        self.assertNotIn("git add general_evaluation_ledger.json", workflow)
        self.assertNotIn("git add general_research_memory.json", workflow)
        self.assertIn("general_research_memory_test.py", workflow)

    def test_autonomous_workflow_persists_memory_without_enabling_automation(self):
        workflow = Path(".github/workflows/general-autonomous-research.yml").read_text(encoding="utf-8")
        self.assertIn("python3 general_research_memory.py record-cycle", workflow)
        self.assertIn("objects: archive", workflow)
        self.assertIn("objects: evaluation_ledger", workflow)
        self.assertIn("objects: autonomous_research_state research_memory", workflow)
        self.assertIn("general_evaluation_snapshots/", workflow)
        self.assertNotIn("git rebase origin/main", workflow)
        self.assertIn("steps.attempt_reservation.outputs.attempt_id != ''", workflow)
        memory_step = workflow.index("- name: Append sanitized GENERAL research memory")
        self.assertLess(workflow.index("- name: Record successful evaluation outcome"), memory_step)
        self.assertLess(workflow.index("- name: Record no-candidate outcome without scoring the archive"), memory_step)
        self.assertLess(workflow.index("- name: Record failed claimed cycle"), memory_step)


class AutonomousResearchMemoryIntegrationTests(unittest.TestCase):
    AUTONOMOUS_NOW = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    GITHUB_RUN_ID = "987654321"

    def _make_claimed_cycle(self, root):
        import general_sealed_evaluation as sealed

        start = date(2026, 8, 1)
        rows = []
        for index in range(90):
            draw_day = (start + timedelta(days=index)).isoformat()
            base = (index * 7) % 60 + 1
            numbers = sorted({
                ((base + offset * 11 - 1) % 60) + 1
                for offset in range(6)
            })
            rows.append({"date": draw_day, "numbers": numbers})

        target_base = (90 * 7) % 60 + 1
        rows.append({
            "date": (start + timedelta(days=90)).isoformat(),
            "numbers": sorted({
                ((target_base + offset * 11 - 1) % 60) + 1
                for offset in range(6)
            }),
        })

        archive_path = root / "archive.json"
        ledger_path = root / "ledger.json"
        lock_path = root / "global-lock.json"
        scheduler_state_path = root / "autonomous-state.json"
        sealed_plan_path = root / "sealed-plan.json"

        write_json(archive_path, {"draws": rows})
        archive = sealed.load_archive(archive_path)
        write_json(ledger_path, {
            "schema_version": 1,
            "purpose": "synthetic autonomous memory integration test",
            "entries": [],
        })
        write_json(lock_path, initial_lock_state())

        partition = sealed.split_archive(
            archive["rows"],
            archive_sha256=archive["archive_sha256"],
            used_validation_dates=set(),
        )
        sealed_plan = sealed.public_plan(partition)
        write_json(sealed_plan_path, sealed_plan)

        state = autonomous.activate_state(
            autonomous.initial_state(),
            confirmation=autonomous.AUTO_CONFIRMATION_PHRASE,
            free_usage_verified=True,
            authorization_days=7,
            global_lock=initial_lock_state(),
            now=self.AUTONOMOUS_NOW,
        )
        prepared = autonomous.prepare_cycle(
            state,
            archive_path=archive_path,
            ledger_path=ledger_path,
            global_lock_path=lock_path,
            now=self.AUTONOMOUS_NOW,
        )
        self.assertTrue(prepared["eligible"], prepared)
        self.assertEqual(prepared["status"], "READY")
        self.assertIs(prepared["target_numbers_exposed"], False)
        self.assertNotIn("numbers", prepared)

        attempt = autonomous.claim_attempt(
            state,
            prepared,
            github_run_id=self.GITHUB_RUN_ID,
            now=self.AUTONOMOUS_NOW,
        )
        write_json(scheduler_state_path, state)

        return {
            "archive": archive,
            "archive_path": archive_path,
            "attempt": attempt,
            "ledger_path": ledger_path,
            "lock_path": lock_path,
            "memory_path": root / "general_research_memory.json",
            "run_status_path": root / "open_research_run_status.json",
            "forward_report_path": root / "open_research_forward_report.md",
            "reverse_report_path": root / "open_research_reverse_report.md",
            "scheduler_state_path": scheduler_state_path,
            "sealed_plan_path": sealed_plan_path,
        }

    def _finish_attempt(self, cycle, *, outcome, reason_code):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = autonomous.main([
                "finish",
                "--state", str(cycle["scheduler_state_path"]),
                "--attempt-id", cycle["attempt"]["attempt_id"],
                "--outcome", outcome,
                "--reason-code", reason_code,
            ])
        self.assertEqual(exit_code, 0, stderr.getvalue() or stdout.getvalue())
        saved_state = autonomous.read_state(cycle["scheduler_state_path"])
        self.assertEqual(len(saved_state["events"]), 1)
        self.assertEqual(
            saved_state["events"][0]["attempt_id"],
            cycle["attempt"]["attempt_id"],
        )
        self.assertEqual(saved_state["events"][0]["outcome"], outcome)
        return saved_state

    def _record_memory_via_workflow_cli(self, cycle):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            exit_code = memory.main([
                "record-cycle",
                "--run-id", self.GITHUB_RUN_ID,
                "--run-attempt", "1",
                "--memory", str(cycle["memory_path"]),
                "--archive", str(cycle["archive_path"]),
                "--ledger", str(cycle["ledger_path"]),
                "--run-status", str(cycle["run_status_path"]),
                "--forward-report", str(cycle["forward_report_path"]),
                "--reverse-report", str(cycle["reverse_report_path"]),
                "--sealed-plan", str(cycle["sealed_plan_path"]),
                "--claim", str(cycle["memory_path"].parent / "claim.json"),
                "--evaluation", str(cycle["memory_path"].parent / "evaluation.json"),
            ])
        self.assertEqual(exit_code, 0, stderr.getvalue() or stdout.getvalue())
        return json.loads(stdout.getvalue())

    def test_successful_autonomous_finish_flows_into_two_directional_memory_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cycle = self._make_claimed_cycle(root)
            target = cycle["archive"]["last_date"]
            archive_sha = cycle["archive"]["archive_sha256"]

            write_json(cycle["run_status_path"], {
                "schema_version": 1,
                "status": "RESEARCH_COMPLETE",
                "stage": "research_complete",
                "target_date": target,
                "archive_sha256": archive_sha,
                "failed_direction": None,
                "error_type": None,
                "target_numbers_stored": False,
                "archive_rows_stored": False,
            })
            cycle["forward_report_path"].write_text(
                directional_report("forward"), encoding="utf-8"
            )
            cycle["reverse_report_path"].write_text(
                directional_report("reverse"), encoding="utf-8"
            )

            scheduler_state = self._finish_attempt(
                cycle,
                outcome="SUCCEEDED",
                reason_code="EVALUATION_COMPLETED",
            )
            result = self._record_memory_via_workflow_cli(cycle)
            saved = memory.load_memory(cycle["memory_path"])

            self.assertEqual(result["status"], "APPENDED")
            self.assertEqual(result["entries_added"], 2)
            self.assertEqual(
                [item["direction"] for item in saved["entries"]],
                ["forward", "reverse"],
            )
            self.assertEqual(
                [item["status"] for item in saved["entries"]],
                ["HYPOTHESES_CAPTURED", "HYPOTHESES_CAPTURED"],
            )
            for item in saved["entries"]:
                self.assertEqual(item["target_date"], target)
                self.assertEqual(item["archive_sha256"], archive_sha)
                self.assertNotIn("numbers", item)
                self.assertIsNone(item["evaluation_feedback"])

            attempt = scheduler_state["attempts"][0]
            self.assertEqual(attempt["target_date"], target)
            self.assertEqual(attempt["archive_sha256"], archive_sha)
            self.assertEqual(scheduler_state["events"][0]["outcome"], "SUCCEEDED")

            forward_context = memory.direction_context(
                "forward", target, memory_path=cycle["memory_path"]
            )
            reverse_context = memory.direction_context(
                "reverse", target, memory_path=cycle["memory_path"]
            )
            self.assertEqual(len(forward_context), 1)
            self.assertEqual(len(reverse_context), 1)
            self.assertEqual(forward_context[0]["direction"], "forward")
            self.assertEqual(reverse_context[0]["direction"], "reverse")
            self.assertNotIn(
                "Complete narrative report for reverse direction.",
                forward_context[0]["research_report_content"],
            )
            self.assertNotIn(
                "Complete narrative report for forward direction.",
                reverse_context[0]["research_report_content"],
            )

            with self.assertRaisesRegex(
                memory.ResearchMemoryError, "already exists in memory"
            ):
                memory.record_cycle(
                    memory_path=cycle["memory_path"],
                    archive_path=cycle["archive_path"],
                    ledger_path=cycle["ledger_path"],
                    run_status_path=cycle["run_status_path"],
                    forward_report_path=cycle["forward_report_path"],
                    reverse_report_path=cycle["reverse_report_path"],
                    sealed_plan_path=cycle["sealed_plan_path"],
                    claim_path=root / "claim.json",
                    evaluation_path=root / "evaluation.json",
                    run_id=self.GITHUB_RUN_ID,
                    run_attempt="1",
                )
            self.assertEqual(len(memory.load_memory(cycle["memory_path"])["entries"]), 2)

    def test_failed_reverse_attempt_persists_forward_report_and_reverse_failure_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cycle = self._make_claimed_cycle(root)
            target = cycle["archive"]["last_date"]
            archive_sha = cycle["archive"]["archive_sha256"]

            write_json(cycle["run_status_path"], {
                "schema_version": 1,
                "status": "FAILED",
                "stage": "reverse_model_request",
                "target_date": target,
                "archive_sha256": archive_sha,
                "failed_direction": "reverse",
                "error_type": "TimeoutError",
                "target_numbers_stored": False,
                "archive_rows_stored": False,
            })
            cycle["forward_report_path"].write_text(
                directional_report("forward"), encoding="utf-8"
            )

            scheduler_state = self._finish_attempt(
                cycle,
                outcome="FAILED",
                reason_code="WORKFLOW_FAILED",
            )
            result = self._record_memory_via_workflow_cli(cycle)
            saved = memory.load_memory(cycle["memory_path"])

            self.assertEqual(result["status"], "APPENDED")
            self.assertEqual(result["entries_added"], 2)
            self.assertEqual(
                [item["direction"] for item in saved["entries"]],
                ["forward", "reverse"],
            )
            self.assertEqual(saved["entries"][0]["status"], "HYPOTHESES_CAPTURED")
            self.assertEqual(saved["entries"][1]["status"], "REQUEST_FAILED")
            self.assertIsNone(saved["entries"][1]["research_report_content"])
            self.assertEqual(
                saved["entries"][1]["failure"],
                {
                    "stage": "reverse_model_request",
                    "error_type": "TimeoutError",
                    "failure_metadata_only": True,
                },
            )
            self.assertEqual(scheduler_state["events"][0]["outcome"], "FAILED")
            self.assertEqual(
                scheduler_state["events"][0]["reason_code"], "WORKFLOW_FAILED"
            )
            self.assertEqual(
                len(memory.direction_context(
                    "forward", target, memory_path=cycle["memory_path"]
                )),
                1,
            )
            self.assertEqual(
                len(memory.direction_context(
                    "reverse", target, memory_path=cycle["memory_path"]
                )),
                1,
            )



    def test_schema_v2_memory_reads_as_schema_v3_without_mutating_the_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "research-memory.json"
            legacy = memory.empty_memory()
            legacy.pop("offline_research")
            legacy["schema_version"] = 2
            original = json.dumps(legacy, ensure_ascii=False, indent=2) + "\n"
            path.write_text(original, encoding="utf-8")
            loaded = memory.load_memory(path)
            self.assertEqual(loaded["schema_version"], 3)
            self.assertEqual(loaded["offline_research"]["entries"], [])
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_combined_research_memory_rejects_duplicate_root_keys(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "research-memory.json"
            path.write_text(
                '{"schema_version":2,"schema_version":2,"project":"GENERAL Super Loto",'
                '"research_epoch":"general-open-research-memory-v2","purpose":"x","entries":[]}',
                encoding="utf-8",
            )
            with self.assertRaises(memory.ResearchMemoryError):
                memory.load_memory(path)



if __name__ == "__main__":
    unittest.main(verbosity=2)
