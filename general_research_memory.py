"""Append-only, direction-isolated memory for GENERAL's own blank-slate research.

This module deliberately never imports general_memory.json, legacy formula files,
or archive rows into model context. It stores validated GENERAL-origin hypotheses,
safe execution specifications, aggregate evaluation feedback, and sanitized
failure metadata. Consumed holdout rows are never copied into this memory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from general_hypothesis_proposals import (
    PROPOSAL_KEYS,
    _validate_proposal,
    parse_hypothesis_proposals,
)
from general_execution_spec_parser import parse_execution_spec_block
from general_hypothesis_execution import validate_execution_spec

VERSION = "0.1.0"
SCHEMA_VERSION = 1
PROJECT = "GENERAL Super Loto"
RESEARCH_EPOCH = "general-open-research-memory-v1"
RESEARCH_SCOPE_START = date(2020, 8, 2)
SOURCE = "GENERAL/open-research"
MEMORY_PATH = Path("general_research_memory.json")
RUN_STATUS_PATH = Path("open_research_run_status.json")
FORWARD_REPORT_PATH = Path("open_research_forward_report.md")
REVERSE_REPORT_PATH = Path("open_research_reverse_report.md")
SEALED_PLAN_PATH = Path("open_research_sealed_plan.json")
PROPOSALS_PATH = Path("open_research_proposals.json")
SPECS_PATH = Path("open_research_execution_specs.json")
CLAIM_PATH = Path("open_research_evaluation_claim.json")
EVALUATION_PATH = Path("open_research_evaluation_report.json")
ARCHIVE_PATH = Path("super_loto_archive.json")
LEDGER_PATH = Path("general_evaluation_ledger.json")

LESSONS_START = "<RESEARCH_LEARNING_NOTES_JSON>"
LESSONS_END = "</RESEARCH_LEARNING_NOTES_JSON>"
LESSON_KEYS = {"observations", "failed_approaches", "next_tests"}
MAX_LESSON_ITEMS = 6
MAX_LESSON_CHARS = 700
MAX_CONTEXT_ENTRIES = 6
MAX_CONTEXT_CHARS = 18000
MAX_ENTRIES = 10000
HEX64 = re.compile(r"^[0-9a-f]{64}$")
DRAW_SET_TEXT = re.compile(
    r"(?<![0-9.])(?:[1-9]|[1-5][0-9]|60)"
    r"(?:\s*,\s*(?:[1-9]|[1-5][0-9]|60)){5}(?![0-9.])"
)
ALLOWED_STATUSES = {
    "HYPOTHESES_CAPTURED",
    "NO_VALID_HYPOTHESIS",
    "EVALUATED",
    "CLAIMED_EVALUATION_FAILED",
    "REQUEST_FAILED",
    "RESEARCH_COMPLETE_UNEVALUATED",
}
METRIC_FIELDS = {
    "mean_hits_out_of_6",
    "standard_error_of_mean_hits",
    "uniform_random_expected_hits_out_of_6",
    "test_draw_count",
    "evaluated_draw_count",
    "validation_draw_count",
    "folds",
    "fold_count",
    "zero_hit_count",
    "skipped_draw_count",
}


class ResearchMemoryError(ValueError):
    """Raised when research-memory content fails provenance or safety checks."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _contains_six_number_set(value: Any) -> bool:
    if isinstance(value, str):
        return DRAW_SET_TEXT.search(value) is not None
    if isinstance(value, list):
        if (
            len(value) == 6
            and all(type(item) is int and 1 <= item <= 60 for item in value)
            and len(set(value)) == 6
        ):
            return True
        return any(_contains_six_number_set(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_six_number_set(item) for item in value.values())
    return False


def _reject_number_values(value: Any, label: str) -> None:
    if _contains_six_number_set(value):
        raise ResearchMemoryError(label + " contains a six-number value set; memory write refused.")


def empty_memory() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "project": PROJECT,
        "research_epoch": RESEARCH_EPOCH,
        "purpose": (
            "Append-only GENERAL-origin directional research memory. No legacy user/Pydroid "
            "experiments, raw archive rows, consumed holdout rows, target values, or predicted "
            "number values are permitted."
        ),
        "entries": [],
    }


def _validate_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ResearchMemoryError("Memory timestamp is missing.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ResearchMemoryError("Memory timestamp is invalid.") from None
    if parsed.tzinfo is None:
        raise ResearchMemoryError("Memory timestamp must include a timezone.")
    return parsed.astimezone(timezone.utc)


def _entry_hash(entry: dict[str, Any]) -> str:
    body = {key: value for key, value in entry.items() if key != "content_sha256"}
    return _sha(_canonical(body))


def _validate_entry(entry: Any, expected_id: int) -> dict[str, Any]:
    required = {
        "entry_id", "cycle_id", "source", "research_epoch", "direction",
        "created_at_utc", "target_date", "archive_sha256", "runner_version",
        "status", "research_report_sha256", "proposal_status", "proposals",
        "execution_spec_status", "execution_spec", "execution_spec_sha256",
        "learning_notes", "evaluation_feedback", "failure", "discovery_window",
        "content_sha256",
    }
    if not isinstance(entry, dict) or set(entry) != required:
        raise ResearchMemoryError("Research-memory entry keys do not match the schema.")
    if type(entry["entry_id"]) is not int or entry["entry_id"] != expected_id:
        raise ResearchMemoryError("Research-memory IDs must be continuous, unique, and increasing.")
    if not isinstance(entry["cycle_id"], str) or not HEX64.fullmatch(entry["cycle_id"]):
        raise ResearchMemoryError("Research-memory cycle ID is invalid.")
    if entry["source"] != SOURCE or entry["research_epoch"] != RESEARCH_EPOCH:
        raise ResearchMemoryError("Only this GENERAL research engine's own epoch may enter memory.")
    if entry["direction"] not in {"forward", "reverse"}:
        raise ResearchMemoryError("Research-memory direction must be forward or reverse.")
    _validate_timestamp(entry["created_at_utc"])
    try:
        day = date.fromisoformat(entry["target_date"])
    except (TypeError, ValueError):
        raise ResearchMemoryError("Research-memory target date is invalid.") from None
    if day.isoformat() != entry["target_date"]:
        raise ResearchMemoryError("Research-memory target date is not canonical.")
    if day < RESEARCH_SCOPE_START:
        raise ResearchMemoryError("Research-memory target is before the active 2020-08-02 scope.")
    if entry["archive_sha256"] is None:
        raise ResearchMemoryError("Research-memory archive provenance is required.")
    if entry["research_report_sha256"] is None and entry["status"] != "REQUEST_FAILED":
        raise ResearchMemoryError("A report-backed research entry must include its report hash.")
    for name in ("archive_sha256", "research_report_sha256", "content_sha256"):
        value = entry[name]
        if value is not None and (not isinstance(value, str) or not HEX64.fullmatch(value)):
            raise ResearchMemoryError("Research-memory " + name + " is invalid.")
    if entry["status"] not in ALLOWED_STATUSES:
        raise ResearchMemoryError("Research-memory status is invalid.")
    if not isinstance(entry["runner_version"], str) or not entry["runner_version"]:
        raise ResearchMemoryError("Research runner version is missing.")
    if entry["proposal_status"] not in {
        "VALID_PROPOSALS", "VALID_EMPTY", "MISSING_BLOCK", "REJECTED",
        "REJECTED_DIRECTION_MISMATCH", "NOT_RUN",
    }:
        raise ResearchMemoryError("Proposal status is invalid.")
    if not isinstance(entry["proposals"], list) or len(entry["proposals"]) > 5:
        raise ResearchMemoryError("Stored proposal list is invalid.")
    seen_proposals = set()
    for proposal in entry["proposals"]:
        if not isinstance(proposal, dict) or set(proposal) != PROPOSAL_KEYS:
            raise ResearchMemoryError("Stored proposal does not match the validated proposal schema.")
        proposal_id = proposal.get("hypothesis_id")
        if not isinstance(proposal_id, str) or not re.fullmatch(r"HYP-[0-9]{3}", proposal_id):
            raise ResearchMemoryError("Stored proposal ID is invalid.")
        numeric_id = int(proposal_id[-3:])
        if (
            proposal_id in seen_proposals
            or (entry["direction"] == "forward" and not 1 <= numeric_id <= 5)
            or (entry["direction"] == "reverse" and not 501 <= numeric_id <= 505)
        ):
            raise ResearchMemoryError("Stored proposal ID does not preserve its directional namespace.")
        seen_proposals.add(proposal_id)
        if proposal.get("discovery_direction") != entry["direction"]:
            raise ResearchMemoryError("A directional memory entry contains another direction's proposal.")
        try:
            checked_proposal = _validate_proposal(proposal)
        except (TypeError, ValueError):
            raise ResearchMemoryError("Stored proposal failed the canonical proposal validator.") from None
        if {key: checked_proposal[key] for key in PROPOSAL_KEYS} != proposal:
            raise ResearchMemoryError("Stored proposal is not in canonical validated form.")
        _reject_number_values(proposal, "Stored proposal")
    if entry["execution_spec_status"] not in {
        "VALIDATED_SPECS", "VALID_EMPTY", "MISSING_BLOCK", "REJECTED", "NOT_RUN",
    }:
        raise ResearchMemoryError("Execution-spec status is invalid.")
    if entry["execution_spec"] is not None and not isinstance(entry["execution_spec"], dict):
        raise ResearchMemoryError("Stored execution specification is invalid.")
    if entry["execution_spec_sha256"] is not None and not HEX64.fullmatch(str(entry["execution_spec_sha256"])):
        raise ResearchMemoryError("Stored execution-spec hash is invalid.")
    if entry["execution_spec"] is not None:
        try:
            checked_spec = validate_execution_spec(entry["execution_spec"])
        except Exception:
            raise ResearchMemoryError("Stored execution specification failed the canonical DSL validator.") from None
        if checked_spec != entry["execution_spec"]:
            raise ResearchMemoryError("Stored execution specification is not canonical.")
        if entry["execution_spec_sha256"] != _sha(_canonical(entry["execution_spec"])):
            raise ResearchMemoryError("Stored execution-spec hash does not match its definition.")
    elif entry["execution_spec_sha256"] is not None:
        raise ResearchMemoryError("An absent execution specification cannot have a specification hash.")
    if (entry["execution_spec_status"] == "VALIDATED_SPECS") != (entry["execution_spec"] is not None):
        raise ResearchMemoryError("Execution-spec status and stored specification disagree.")
    if entry["proposal_status"] == "VALID_PROPOSALS" and not entry["proposals"]:
        raise ResearchMemoryError("A valid proposal status must contain proposals.")
    if entry["proposal_status"] == "VALID_EMPTY" and entry["proposals"]:
        raise ResearchMemoryError("A valid-empty proposal status cannot contain proposals.")
    if entry["proposal_status"] not in {"VALID_PROPOSALS", "VALID_EMPTY"} and entry["proposals"]:
        raise ResearchMemoryError("Rejected or missing proposal status cannot contain proposals.")
    if not isinstance(entry["learning_notes"], dict) or set(entry["learning_notes"]) != LESSON_KEYS:
        raise ResearchMemoryError("Stored learning notes are invalid.")
    for note_list in entry["learning_notes"].values():
        if not isinstance(note_list, list) or len(note_list) > MAX_LESSON_ITEMS:
            raise ResearchMemoryError("Stored learning-note lists are invalid.")
        if any(not isinstance(note, str) or not 5 <= len(note.strip()) <= MAX_LESSON_CHARS for note in note_list):
            raise ResearchMemoryError("Stored learning-note items are invalid.")
    _reject_number_values(entry["learning_notes"], "Stored learning notes")
    if entry["evaluation_feedback"] is not None:
        feedback = entry["evaluation_feedback"]
        feedback_keys = {
            "status", "claim_id", "candidate_id", "spec_sha256", "holdout",
            "cross_validation", "holdout_consumed", "retrospective_only",
            "raw_holdout_dates_stored", "per_draw_results_stored",
            "draw_number_values_stored", "interpretation",
        }
        if not isinstance(feedback, dict) or set(feedback) != feedback_keys:
            raise ResearchMemoryError("Evaluation feedback does not match its safe summary schema.")
        if feedback.get("status") != "EVALUATED_EXPLORATORY_FIXED_SPEC":
            raise ResearchMemoryError("Evaluation feedback status is invalid.")
        if (
            not isinstance(feedback.get("claim_id"), str)
            or not HEX64.fullmatch(feedback["claim_id"])
            or not entry["proposals"]
            or feedback.get("candidate_id") != entry["proposals"][0]["hypothesis_id"]
            or feedback.get("spec_sha256") != entry["execution_spec_sha256"]
            or feedback.get("spec_sha256") is None
        ):
            raise ResearchMemoryError("Evaluation feedback is not bound to this direction's frozen candidate.")
        if not isinstance(feedback.get("interpretation"), str) or len(feedback["interpretation"]) > 300:
            raise ResearchMemoryError("Evaluation feedback interpretation is invalid.")
        for metric_group in ("holdout", "cross_validation"):
            group = feedback.get(metric_group)
            if not isinstance(group, dict) or set(group) - (METRIC_FIELDS | {"hit_distribution"}):
                raise ResearchMemoryError("Evaluation feedback contains non-aggregate fields.")
            for metric, metric_value in group.items():
                if metric == "hit_distribution":
                    if (
                        not isinstance(metric_value, dict)
                        or len(metric_value) > 8
                        or any(
                            not isinstance(key, str)
                            or len(key) > 12
                            or type(count) is not int
                            or not 0 <= count <= 10000
                            for key, count in metric_value.items()
                        )
                    ):
                        raise ResearchMemoryError("Evaluation hit distribution is invalid.")
                elif type(metric_value) not in {int, float} or not math.isfinite(float(metric_value)):
                    raise ResearchMemoryError("Evaluation aggregate metric is invalid.")
        if feedback.get("holdout_consumed") is not True:
            raise ResearchMemoryError("Evaluation feedback must mark the sealed holdout as consumed.")
        expected_retrospective = entry["direction"] == "reverse"
        if feedback.get("retrospective_only") is not expected_retrospective:
            raise ResearchMemoryError("Evaluation feedback direction classification is invalid.")
        if any(feedback.get(flag) is not False for flag in (
            "raw_holdout_dates_stored", "per_draw_results_stored", "draw_number_values_stored"
        )):
            raise ResearchMemoryError("Evaluation feedback must not contain raw holdout details or values.")
        _reject_number_values(feedback, "Evaluation feedback")
    if entry["failure"] is not None:
        failure = entry["failure"]
        basic_failure_keys = {"stage", "error_type", "failure_metadata_only"}
        rejection_failure_keys = basic_failure_keys | {"proposal_reasons", "execution_spec_reasons"}
        if not isinstance(failure, dict) or frozenset(failure) not in {frozenset(basic_failure_keys), frozenset(rejection_failure_keys)}:
            raise ResearchMemoryError("Failure metadata does not match its safe schema.")
        if (
            not isinstance(failure.get("stage"), str)
            or not 3 <= len(failure["stage"]) <= 80
            or not isinstance(failure.get("error_type"), str)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", failure["error_type"])
            or failure.get("failure_metadata_only") is not True
        ):
            raise ResearchMemoryError("Failure metadata contains an invalid stage/type.")
        for field in ("proposal_reasons", "execution_spec_reasons"):
            if field in failure:
                values = failure[field]
                if (
                    not isinstance(values, list)
                    or len(values) > 4
                    or any(not isinstance(item, str) or len(item) > 240 for item in values)
                ):
                    raise ResearchMemoryError("Structured rejection metadata is invalid.")
        _reject_number_values(failure, "Failure metadata")
    window = entry["discovery_window"]
    if not isinstance(window, dict) or set(window) != {
        "start_date", "end_date", "draw_count", "holdout_dates_copied", "archive_rows_copied"
    }:
        raise ResearchMemoryError("Discovery-window metadata is invalid.")
    if window.get("holdout_dates_copied") is not False or window.get("archive_rows_copied") is not False:
        raise ResearchMemoryError("Raw holdout/archive rows cannot be copied into research memory.")
    count = window.get("draw_count")
    if count is not None and (type(count) is not int or count < 0):
        raise ResearchMemoryError("Discovery-window draw count is invalid.")
    start = window.get("start_date")
    end = window.get("end_date")
    if (start is None) != (end is None):
        raise ResearchMemoryError("Discovery-window date bounds must both be present or both be null.")
    if start is None:
        if count not in {None, 0}:
            raise ResearchMemoryError("A non-empty discovery window must have date bounds.")
    else:
        try:
            start_day = date.fromisoformat(start)
            end_day = date.fromisoformat(end)
        except (TypeError, ValueError):
            raise ResearchMemoryError("Discovery-window dates are invalid.") from None
        if (
            start_day.isoformat() != start
            or end_day.isoformat() != end
            or start_day < RESEARCH_SCOPE_START
            or end_day < start_day
            or end_day >= day
            or count is None
            or count < 1
        ):
            raise ResearchMemoryError("Discovery-window chronology or scope is invalid.")
    _reject_number_values(window, "Discovery-window metadata")
    if entry["content_sha256"] != _entry_hash(entry):
        raise ResearchMemoryError("Research-memory entry hash mismatch.")
    return entry


def load_memory(path: str | Path = MEMORY_PATH) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return empty_memory()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ResearchMemoryError("Research-memory file could not be read as valid UTF-8 JSON.") from None
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "project", "research_epoch", "purpose", "entries"}
        or payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("project") != PROJECT
        or payload.get("research_epoch") != RESEARCH_EPOCH
        or not isinstance(payload.get("purpose"), str)
        or not isinstance(payload.get("entries"), list)
        or len(payload["entries"]) > MAX_ENTRIES
    ):
        raise ResearchMemoryError("Research-memory file has an invalid schema; failing closed.")
    seen_cycles: set[str] = set()
    previous_target: date | None = None
    for index, entry in enumerate(payload["entries"], start=1):
        checked = _validate_entry(entry, index)
        if checked["cycle_id"] in seen_cycles:
            raise ResearchMemoryError("Research-memory cycle IDs must be unique.")
        seen_cycles.add(checked["cycle_id"])
        current_target = date.fromisoformat(checked["target_date"])
        if previous_target is not None and current_target < previous_target:
            raise ResearchMemoryError("Research-memory entries must remain chronological.")
        previous_target = current_target
    return payload


def _parse_learning_notes(report: str) -> dict[str, list[str]]:
    empty = {key: [] for key in LESSON_KEYS}
    if report.count(LESSONS_START) == 0 and report.count(LESSONS_END) == 0:
        return empty
    if report.count(LESSONS_START) != 1 or report.count(LESSONS_END) != 1:
        raise ResearchMemoryError("Learning-notes block markers are duplicated or incomplete.")
    start = report.index(LESSONS_START) + len(LESSONS_START)
    end = report.index(LESSONS_END)
    if end <= start:
        raise ResearchMemoryError("Learning-notes block markers are out of order.")
    try:
        value = json.loads(report[start:end].strip())
    except json.JSONDecodeError:
        raise ResearchMemoryError("Learning-notes block is not valid JSON.") from None
    if not isinstance(value, dict) or set(value) != {"schema_version", *LESSON_KEYS}:
        raise ResearchMemoryError("Learning-notes block keys are invalid.")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ResearchMemoryError("Learning-notes schema version is unsupported.")
    result: dict[str, list[str]] = {}
    for key in LESSON_KEYS:
        items = value[key]
        if not isinstance(items, list) or len(items) > MAX_LESSON_ITEMS:
            raise ResearchMemoryError("Learning-notes " + key + " list is invalid.")
        checked = []
        for item in items:
            if not isinstance(item, str) or not 5 <= len(item.strip()) <= MAX_LESSON_CHARS:
                raise ResearchMemoryError("Learning-notes items must be bounded text.")
            checked.append(item.strip())
        result[key] = checked
    _reject_number_values(result, "Learning-notes block")
    return result


def _safe_metric_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    summary = {}
    for key in METRIC_FIELDS:
        item = value.get(key)
        if type(item) in {int, float} and math.isfinite(float(item)):
            summary[key] = item
    distribution = value.get("hit_distribution")
    if isinstance(distribution, dict) and len(distribution) <= 8:
        safe_distribution = {}
        for key, count in distribution.items():
            if (
                isinstance(key, str)
                and len(key) <= 12
                and type(count) is int
                and 0 <= count <= 10000
            ):
                safe_distribution[key] = count
        if safe_distribution:
            summary["hit_distribution"] = safe_distribution
    return summary


def _extract_evaluation_feedback(
    report: dict[str, Any],
    direction: str,
) -> dict[str, Any]:
    if report.get("status") != "EVALUATED_EXPLORATORY_FIXED_SPEC":
        raise ResearchMemoryError("Evaluation report status is not a completed exploratory evaluation.")
    if (
        report.get("current_isolated_target_included") is not False
        or report.get("current_target_numbers_exposed") is not False
        or report.get("target_or_future_rows_used") is not False
        or report.get("per_draw_target_numbers_reported") is not False
        or report.get("candidate_rankings_reported") is not False
    ):
        raise ResearchMemoryError("Evaluation report failed target/number-value safety flags.")
    selection = report.get("candidate_selection")
    if not isinstance(selection, dict) or selection.get("selection_used_validation_scores") is not False:
        raise ResearchMemoryError("Evaluation report candidate-freeze metadata is invalid.")
    if direction == "forward":
        candidate_id = selection.get("forward_hypothesis_id")
        spec_hash = selection.get("forward_spec_sha256")
        holdout = _safe_metric_summary(report.get("forward_walk_forward"))
        cv = _safe_metric_summary(report.get("time_ordered_cross_validation"))
        retrospective_only = False
    else:
        candidate_id = selection.get("reverse_hypothesis_id")
        spec_hash = selection.get("reverse_spec_sha256")
        holdout = _safe_metric_summary(report.get("reverse_discovered_candidate_holdout"))
        cv = _safe_metric_summary(report.get("reverse_time_ordered_cross_validation"))
        retrospective_only = True
    if not isinstance(candidate_id, str) or not isinstance(spec_hash, str) or not HEX64.fullmatch(spec_hash):
        raise ResearchMemoryError("Directional evaluation candidate provenance is invalid.")
    return {
        "status": report["status"],
        "claim_id": report.get("claim_id"),
        "candidate_id": candidate_id,
        "spec_sha256": spec_hash,
        "holdout": holdout,
        "cross_validation": cv,
        "holdout_consumed": True,
        "retrospective_only": retrospective_only,
        "raw_holdout_dates_stored": False,
        "per_draw_results_stored": False,
        "draw_number_values_stored": False,
        "interpretation": (
            "Forward chronological evaluation feedback; its sealed interval is permanently consumed."
            if direction == "forward"
            else "Reverse result is retrospective-only feedback; its sealed interval is permanently consumed."
        ),
    }


def _load_optional_json(path: str | Path) -> dict[str, Any] | None:
    file_path = Path(path)
    if not file_path.is_file():
        return None
    try:
        value = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ResearchMemoryError(file_path.name + " is not valid JSON.") from None
    return value if isinstance(value, dict) else None


def _direction_artifacts(
    direction: str,
    report_path: str | Path,
) -> tuple[str | None, dict[str, Any], dict[str, Any], dict[str, list[str]]]:
    path = Path(report_path)
    if not path.is_file():
        return None, {
            "status": "NOT_RUN", "proposals": [],
        }, {
            "status": "NOT_RUN", "specifications": [],
        }, {key: [] for key in LESSON_KEYS}
    try:
        report = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ResearchMemoryError("Directional model report could not be read.") from None
    proposals = parse_hypothesis_proposals(report)
    if proposals["status"] in {"VALID_PROPOSALS", "VALID_EMPTY"} and any(
        item.get("discovery_direction") != direction for item in proposals["proposals"]
    ):
        proposals["status"] = "REJECTED_DIRECTION_MISMATCH"
        proposals["proposals"] = []
        proposals["rejection_reasons"] = ["Proposal direction did not match the isolated request."]
    allowed_ids = {item["hypothesis_id"] for item in proposals["proposals"]}
    specs = parse_execution_spec_block(report, allowed_hypothesis_ids=allowed_ids)
    if len(specs["specifications"]) > 1:
        specs["status"] = "REJECTED"
        specs["specifications"] = []
        specs["rejection_reasons"] = ["Only one specification is permitted per directional request."]
    if any(item.get("hypothesis_id") not in allowed_ids for item in specs["specifications"]):
        specs["status"] = "REJECTED"
        specs["specifications"] = []
        specs["rejection_reasons"] = ["Execution specification is not attached to this direction's proposal."]
    if specs["status"] == "VALIDATED_SPECS" and (
        not proposals["proposals"]
        or len(specs["specifications"]) != 1
        or specs["specifications"][0].get("hypothesis_id") != proposals["proposals"][0]["hypothesis_id"]
    ):
        specs["status"] = "REJECTED"
        specs["specifications"] = []
        specs["rejection_reasons"] = ["Only a specification for this direction's first proposal can be retained."]
    notes = _parse_learning_notes(report)
    _reject_number_values(proposals["proposals"], "Directional proposals")
    _reject_number_values(specs["specifications"], "Directional specifications")
    _reject_number_values(notes, "Directional learning notes")
    return report, proposals, specs, notes


def _build_entry(
    *,
    entry_id: int,
    run_id: str,
    run_attempt: str,
    target_date: str,
    archive_sha256: str,
    direction: str,
    report_path: str | Path,
    discovery_window: dict[str, Any],
    outcome: str,
    failed_direction: str | None,
    failed_stage: str | None,
    error_type: str | None,
    shared_failure: bool,
    evaluation: dict[str, Any] | None,
) -> dict[str, Any]:
    report, proposal_doc, specs_doc, notes = _direction_artifacts(direction, report_path)
    has_report = report is not None
    source_proposals = proposal_doc.get("proposals", [])
    offset = 0 if direction == "forward" else 500
    proposals = []
    for index, item in enumerate(source_proposals):
        mapped = {key: item[key] for key in PROPOSAL_KEYS}
        mapped["hypothesis_id"] = "HYP-" + str(offset + index + 1).zfill(3)
        proposals.append(mapped)
    source_first_id = source_proposals[0]["hypothesis_id"] if source_proposals else None
    first_id = proposals[0]["hypothesis_id"] if proposals else None
    spec_items = specs_doc.get("specifications", [])
    matched_spec = next(
        (item for item in spec_items if item.get("hypothesis_id") == source_first_id),
        None,
    )
    spec = matched_spec.get("execution_spec") if matched_spec else None
    spec_hash = _sha(_canonical(spec)) if isinstance(spec, dict) else None
    feedback = _extract_evaluation_feedback(evaluation, direction) if evaluation is not None else None
    if feedback is not None:
        if feedback["candidate_id"] != first_id or feedback["spec_sha256"] != spec_hash:
            raise ResearchMemoryError("Evaluation feedback does not match this direction's stored frozen candidate.")
        status = "EVALUATED"
    elif not has_report and failed_direction == direction:
        status = "REQUEST_FAILED"
    elif outcome == "CLAIMED_EVALUATION_FAILED" and has_report:
        status = "CLAIMED_EVALUATION_FAILED"
    elif proposals:
        status = "HYPOTHESES_CAPTURED"
    elif has_report:
        status = "NO_VALID_HYPOTHESIS"
    else:
        status = "REQUEST_FAILED"
    failure = None
    if failed_direction == direction or status == "CLAIMED_EVALUATION_FAILED" or shared_failure:
        failure = {
            "stage": failed_stage or ("sealed_evaluation" if status == "CLAIMED_EVALUATION_FAILED" else "model_request"),
            "error_type": error_type or ("EvaluationIncomplete" if status == "CLAIMED_EVALUATION_FAILED" else "UnknownRequestFailure"),
            "failure_metadata_only": True,
        }
    elif proposal_doc.get("rejection_reasons") or specs_doc.get("rejection_reasons"):
        failure = {
            "stage": "response_contract_validation",
            "error_type": "StructuredOutputRejected",
            "proposal_reasons": proposal_doc.get("rejection_reasons", [])[:4],
            "execution_spec_reasons": specs_doc.get("rejection_reasons", [])[:4],
            "failure_metadata_only": True,
        }
    entry = {
        "entry_id": entry_id,
        "cycle_id": _sha((run_id + "|" + run_attempt + "|" + target_date + "|" + archive_sha256 + "|" + direction).encode()),
        "source": SOURCE,
        "research_epoch": RESEARCH_EPOCH,
        "direction": direction,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_date": target_date,
        "archive_sha256": archive_sha256,
        "runner_version": "0.7.0",
        "status": status,
        "research_report_sha256": _sha(report.encode("utf-8")) if report is not None else None,
        "proposal_status": proposal_doc.get("status", "NOT_RUN"),
        "proposals": proposals,
        "execution_spec_status": specs_doc.get("status", "NOT_RUN"),
        "execution_spec": spec,
        "execution_spec_sha256": spec_hash,
        "learning_notes": notes,
        "evaluation_feedback": feedback,
        "failure": failure,
        "discovery_window": discovery_window,
    }
    _reject_number_values(entry, "Research-memory entry")
    entry["content_sha256"] = _entry_hash(entry)
    _validate_entry(entry, entry_id)
    return entry


def record_cycle(
    *,
    memory_path: str | Path = MEMORY_PATH,
    archive_path: str | Path = ARCHIVE_PATH,
    ledger_path: str | Path = LEDGER_PATH,
    run_status_path: str | Path = RUN_STATUS_PATH,
    forward_report_path: str | Path = FORWARD_REPORT_PATH,
    reverse_report_path: str | Path = REVERSE_REPORT_PATH,
    sealed_plan_path: str | Path = SEALED_PLAN_PATH,
    claim_path: str | Path = CLAIM_PATH,
    evaluation_path: str | Path = EVALUATION_PATH,
    run_id: str,
    run_attempt: str = "1",
    failure_stage_override: str | None = None,
    error_type_override: str | None = None,
) -> dict[str, Any]:
    """Append one entry per completed/failed direction; raw draws never enter this function."""
    if not run_id or not run_attempt:
        raise ResearchMemoryError("GitHub run ID and attempt are required for idempotent memory records.")
    from general_sealed_evaluation import load_archive, load_ledger

    archive = load_archive(archive_path)
    target = archive["last_date"]
    archive_hash = archive["archive_sha256"]
    ledger = load_ledger(ledger_path)
    run_status = _load_optional_json(run_status_path) or {}
    claim = _load_optional_json(claim_path)
    evaluation_report = _load_optional_json(evaluation_path)
    if evaluation_report is not None and (
        not isinstance(claim, dict) or claim.get("claim_created") is not True
    ):
        raise ResearchMemoryError("An evaluation report without its successful current-cycle claim cannot enter memory.")
    plan = _load_optional_json(sealed_plan_path) or {}
    r60 = plan.get("regimes", {}).get("R60", {}) if isinstance(plan.get("regimes"), dict) else {}
    discovery_window = {
        "start_date": r60.get("discovery_start_date"),
        "end_date": r60.get("discovery_end_date"),
        "draw_count": r60.get("discovery_count"),
        "holdout_dates_copied": False,
        "archive_rows_copied": False,
    }
    has_any_directional_report = (
        Path(forward_report_path).is_file() or Path(reverse_report_path).is_file()
    )
    if has_any_directional_report or run_status.get("failed_direction") in {"forward", "reverse"}:
        if (
            not isinstance(discovery_window["start_date"], str)
            or not isinstance(discovery_window["end_date"], str)
            or type(discovery_window["draw_count"]) is not int
            or discovery_window["draw_count"] < 1
        ):
            raise ResearchMemoryError("A directional result requires its verified sealed discovery-window metadata.")
    outcome = "RESEARCH_COMPLETE_UNEVALUATED"
    claim_id = None
    if isinstance(claim, dict) and claim.get("claim_created") is True:
        claim_id = claim.get("claim_id")
    elif evaluation_report is not None:
        claim_id = evaluation_report.get("claim_id")
    ledger_entry = next(
        (item for item in ledger["entries"] if item.get("claim_id") == claim_id),
        None,
    ) if claim_id else None

    if evaluation_report is not None:
        if (
            ledger_entry is None
            or ledger_entry.get("archive_sha256") != archive_hash
            or ledger_entry.get("target_date") != target
        ):
            raise ResearchMemoryError("Evaluation report claim is not backed by the current append-only ledger.")
        if ledger_entry.get("state") == "EVALUATED":
            if (
                evaluation_report.get("archive_sha256") != archive_hash
                or evaluation_report.get("target_date") != target
            ):
                raise ResearchMemoryError("Evaluation report archive provenance does not match the ledger.")
            # The workflow stages any pending ledger/snapshot changes together
            # with this memory append, so an evaluated state and its feedback
            # become persistent in the same repository push.
            outcome = "EVALUATED"
        elif ledger_entry.get("state") == "CLAIMED":
            # The report may have been written immediately before a crash, but
            # the append-only ledger is authoritative. Do not retain its scores.
            evaluation_report = None
            outcome = "CLAIMED_EVALUATION_FAILED"
        else:
            raise ResearchMemoryError("Evaluation ledger state is not eligible for memory publication.")
    elif isinstance(claim, dict) and claim.get("claim_created") is True:
        if ledger_entry is None or ledger_entry.get("state") not in {"CLAIMED", "EVALUATED"}:
            raise ResearchMemoryError("Claimed holdout is not present in the append-only ledger.")
        if (
            ledger_entry.get("archive_sha256") != archive_hash
            or ledger_entry.get("target_date") != target
        ):
            raise ResearchMemoryError("Claimed holdout provenance does not match the current archive.")
        if ledger_entry.get("state") == "EVALUATED":
            raise ResearchMemoryError("An evaluated ledger claim is missing its evaluation report.")
        outcome = "CLAIMED_EVALUATION_FAILED"
    elif isinstance(claim, dict) and claim.get("claim_created") is False:
        outcome = "NO_ELIGIBLE_CANDIDATE"

    failed_direction = run_status.get("failed_direction")
    failed_stage = failure_stage_override or run_status.get("stage")
    error_type = error_type_override or run_status.get("error_type")
    if failed_direction not in {None, "forward", "reverse"}:
        failed_direction = None
    if failure_stage_override:
        if not isinstance(failure_stage_override, str) or not 3 <= len(failure_stage_override) <= 80:
            raise ResearchMemoryError("Workflow failure-stage override is invalid.")
        run_status = dict(run_status)
        run_status["status"] = "FAILED"
    if run_status:
        if run_status.get("target_date") not in {None, target}:
            raise ResearchMemoryError("Run-status target does not match the validated archive.")
        if run_status.get("archive_sha256") not in {None, archive_hash}:
            raise ResearchMemoryError("Run-status archive hash does not match the validated archive.")
    if run_status.get("status") == "FAILED" and not failed_direction and not has_any_directional_report:
        # Failures before a directional request are not research observations.
        return {"status": "SKIPPED_NO_DIRECTIONAL_FAILURE", "entries_added": 0}
    shared_failure = run_status.get("status") == "FAILED" and failed_direction is None

    memory = load_memory(memory_path)
    next_id = len(memory["entries"]) + 1
    additions = []
    for direction, report_path in (
        ("forward", forward_report_path),
        ("reverse", reverse_report_path),
    ):
        report_exists = Path(report_path).is_file()
        if not report_exists and failed_direction != direction:
            continue
        direction_eval = evaluation_report if evaluation_report is not None else None
        entry = _build_entry(
            entry_id=next_id + len(additions),
            run_id=run_id,
            run_attempt=run_attempt,
            target_date=target,
            archive_sha256=archive_hash,
            direction=direction,
            report_path=report_path,
            discovery_window=discovery_window,
            outcome=outcome,
            failed_direction=failed_direction,
            failed_stage=failed_stage,
            error_type=error_type,
            shared_failure=shared_failure,
            evaluation=direction_eval,
        )
        if any(old["cycle_id"] == entry["cycle_id"] for old in memory["entries"]):
            raise ResearchMemoryError("This run/direction already exists in memory; refusing a duplicate append.")
        additions.append(entry)
    if not additions:
        return {"status": "NO_DIRECTIONAL_RECORDS", "entries_added": 0}
    if len(memory["entries"]) + len(additions) > MAX_ENTRIES:
        raise ResearchMemoryError("Research memory reached its safety limit; no entries were written.")
    memory["entries"].extend(additions)
    _write_memory(memory, memory_path)
    return {
        "status": "APPENDED",
        "entries_added": len(additions),
        "forward_status": next((item["status"] for item in additions if item["direction"] == "forward"), None),
        "reverse_status": next((item["status"] for item in additions if item["direction"] == "reverse"), None),
        "raw_archive_rows_stored": False,
        "target_or_predicted_numbers_stored": False,
        "consumed_holdout_rows_stored": False,
    }


def _write_memory(memory: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    checked = load_memory(target)
    if memory.keys() != checked.keys() or memory["entries"][:len(checked["entries"])] != checked["entries"]:
        raise ResearchMemoryError("Memory update is not append-only; existing entries changed.")
    if len(memory["entries"]) < len(checked["entries"]):
        raise ResearchMemoryError("Memory entries cannot be deleted.")
    previous_target = (
        date.fromisoformat(checked["entries"][-1]["target_date"])
        if checked["entries"] else None
    )
    for index, entry in enumerate(memory["entries"], start=1):
        _validate_entry(entry, index)
        current_target = date.fromisoformat(entry["target_date"])
        if previous_target is not None and current_target < previous_target:
            raise ResearchMemoryError("Research-memory append would move chronology backwards.")
        previous_target = current_target
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    temp.write_text(json.dumps(memory, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(target)


def direction_context(
    direction: str,
    target_date: str,
    *,
    memory_path: str | Path = MEMORY_PATH,
    limit: int = MAX_CONTEXT_ENTRIES,
) -> list[dict[str, Any]]:
    """Return bounded same-direction GENERAL history from prior runs no later than this target."""
    if direction not in {"forward", "reverse"}:
        raise ResearchMemoryError("Direction must be forward or reverse.")
    try:
        current_target = date.fromisoformat(target_date)
    except (TypeError, ValueError):
        raise ResearchMemoryError("Current target date is invalid.") from None
    if current_target.isoformat() != target_date:
        raise ResearchMemoryError("Current target date is not canonical.")
    memory = load_memory(memory_path)
    entries = [
        item for item in memory["entries"]
        if item["direction"] == direction
        and date.fromisoformat(item["target_date"]) <= current_target
        and _validate_timestamp(item["created_at_utc"]) < datetime.now(timezone.utc)
    ]
    context = []
    for entry in reversed(entries):
        compact = {
            "prior_target_date": entry["target_date"],
            "prior_recorded_at_utc": entry["created_at_utc"],
            "archive_sha256": entry["archive_sha256"],
            "runner_version": entry["runner_version"],
            "research_report_sha256": entry["research_report_sha256"],
            "status": entry["status"],
            "proposal_status": entry["proposal_status"],
            "hypotheses": entry["proposals"],
            "execution_spec_status": entry["execution_spec_status"],
            "execution_spec": entry["execution_spec"],
            "execution_spec_sha256": entry["execution_spec_sha256"],
            "learning_notes": entry["learning_notes"],
            "evaluation_feedback": entry["evaluation_feedback"],
            "failure": entry["failure"],
            "holdout_reuse": "FORBIDDEN; any reported holdout is consumed development feedback only.",
            "source": SOURCE,
            "direction": direction,
        }
        _reject_number_values(compact, "Directional context")
        candidate = context + [compact]
        if len(candidate) > limit or len(_canonical(candidate)) > MAX_CONTEXT_CHARS:
            break
        context = candidate
    return context


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    record = sub.add_parser("record-cycle", help="Append sanitized directional results from a GENERAL run.")
    record.add_argument("--run-id", required=True)
    record.add_argument("--run-attempt", default="1")
    record.add_argument("--memory", type=Path, default=MEMORY_PATH)
    record.add_argument("--archive", type=Path, default=ARCHIVE_PATH)
    record.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    record.add_argument("--run-status", type=Path, default=RUN_STATUS_PATH)
    record.add_argument("--forward-report", type=Path, default=FORWARD_REPORT_PATH)
    record.add_argument("--reverse-report", type=Path, default=REVERSE_REPORT_PATH)
    record.add_argument("--sealed-plan", type=Path, default=SEALED_PLAN_PATH)
    record.add_argument("--claim", type=Path, default=CLAIM_PATH)
    record.add_argument("--evaluation", type=Path, default=EVALUATION_PATH)
    record.add_argument("--failure-stage", default="")
    record.add_argument("--error-type", default="WorkflowStepFailure")
    args = parser.parse_args(argv)
    try:
        result = record_cycle(
            memory_path=args.memory,
            archive_path=args.archive,
            ledger_path=args.ledger,
            run_status_path=args.run_status,
            forward_report_path=args.forward_report,
            reverse_report_path=args.reverse_report,
            sealed_plan_path=args.sealed_plan,
            claim_path=args.claim,
            evaluation_path=args.evaluation,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
            failure_stage_override=args.failure_stage or None,
            error_type_override=args.error_type if args.failure_stage else None,
        )
    except (ResearchMemoryError, OSError, ValueError) as exc:
        print("RESEARCH MEMORY FAILED:", type(exc).__name__, str(exc))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
