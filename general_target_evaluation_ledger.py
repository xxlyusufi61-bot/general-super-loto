#!/usr/bin/env python3
"""Strict schema for private local target-evaluation results, never model context."""
from __future__ import annotations

import copy
import re
from datetime import date, datetime, timezone
from typing import Any

SCHEMA_VERSION = 2
PURPOSE = "PUBLIC_STATE_SAFE_TARGET_EVALUATION_RECEIPTS_WITHOUT_SCORES"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ENTRY_KEYS = {
    "claim_id", "attempt_id", "target_date", "archive_sha256",
    "archive_record_count", "forward_hypothesis_id", "forward_spec_sha256",
    "execution_spec_dsl_version", "candidate_count",
    "uniform_random_expected_hits_out_of_6", "evaluation_scope", "completed_at_utc",
}


class TargetEvaluationLedgerError(ValueError):
    """Raised when a private local target result is unsafe or inconsistent."""


def _date(value: Any) -> str:
    if not isinstance(value, str):
        raise TargetEvaluationLedgerError("target date must be YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise TargetEvaluationLedgerError("target date is invalid") from None
    if parsed.isoformat() != value or parsed < date(2020, 8, 2):
        raise TargetEvaluationLedgerError("target date is outside the active 6-of-60 scope")
    return value


def _utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise TargetEvaluationLedgerError("completion time must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise TargetEvaluationLedgerError("completion time is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise TargetEvaluationLedgerError("completion time must use UTC")
    return parsed.astimezone(timezone.utc)


def validate_entry(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != ENTRY_KEYS:
        raise TargetEvaluationLedgerError("local target result has an invalid schema")
    claim_id = value.get("claim_id")
    attempt_id = value.get("attempt_id")
    if not isinstance(claim_id, str) or not SHA256_RE.fullmatch(claim_id):
        raise TargetEvaluationLedgerError("claim id must be a lowercase SHA-256 digest")
    if not isinstance(attempt_id, str) or not SHA256_RE.fullmatch(attempt_id):
        raise TargetEvaluationLedgerError("attempt id must be a lowercase SHA-256 digest")
    _date(value.get("target_date"))
    if not isinstance(value.get("archive_sha256"), str) or not SHA256_RE.fullmatch(value["archive_sha256"]):
        raise TargetEvaluationLedgerError("archive hash must be a lowercase SHA-256 digest")
    if type(value.get("archive_record_count")) is not int or value["archive_record_count"] < 2:
        raise TargetEvaluationLedgerError("archive record count is invalid")
    hypothesis_id = value.get("forward_hypothesis_id")
    if not isinstance(hypothesis_id, str) or not re.fullmatch(r"HYP-(?!000$)(?:[0-9]{3}|[1-9][0-9]{3,})", hypothesis_id):
        raise TargetEvaluationLedgerError("forward hypothesis id is invalid")
    if not isinstance(value.get("forward_spec_sha256"), str) or not SHA256_RE.fullmatch(value["forward_spec_sha256"]):
        raise TargetEvaluationLedgerError("forward specification hash is invalid")
    if type(value.get("execution_spec_dsl_version")) is not int or value["execution_spec_dsl_version"] not in (1, 2):
        raise TargetEvaluationLedgerError("execution specification DSL version is invalid")
    if type(value.get("candidate_count")) is not int or value["candidate_count"] != 6:
        raise TargetEvaluationLedgerError("candidate count must be exactly six")
    if type(value.get("uniform_random_expected_hits_out_of_6")) not in (int, float) or value["uniform_random_expected_hits_out_of_6"] != 0.6:
        raise TargetEvaluationLedgerError("uniform random R60 reference must equal 0.600/6")
    if value.get("evaluation_scope") != "ONE_CURRENT_HIDDEN_TARGET":
        raise TargetEvaluationLedgerError("evaluation scope is not the one-current-target contract")
    _utc_timestamp(value.get("completed_at_utc"))
    return copy.deepcopy(value)


def validate_ledger(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "purpose", "entries"}:
        raise TargetEvaluationLedgerError("local target-evaluation history has an invalid schema")
    if type(value.get("schema_version")) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise TargetEvaluationLedgerError("unsupported local target-evaluation history schema")
    if value.get("purpose") != PURPOSE:
        raise TargetEvaluationLedgerError("local target-evaluation history purpose mismatch")
    entries = value.get("entries")
    if not isinstance(entries, list) or len(entries) > 100_000:
        raise TargetEvaluationLedgerError("local target-evaluation history exceeds its bounded capacity")
    claims: set[str] = set()
    attempts: set[str] = set()
    previous: datetime | None = None
    for item in entries:
        checked = validate_entry(item)
        if checked["claim_id"] in claims or checked["attempt_id"] in attempts:
            raise TargetEvaluationLedgerError("duplicate local target-evaluation claim or attempt id")
        claims.add(checked["claim_id"])
        attempts.add(checked["attempt_id"])
        current = _utc_timestamp(checked["completed_at_utc"])
        if previous is not None and current < previous:
            raise TargetEvaluationLedgerError("local target-evaluation history must be append-chronological")
        previous = current
    return value
