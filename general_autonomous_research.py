"""Fail-closed scheduler state for one active target and its recorded attempts.

This module never calls NVIDIA or Telegram. It only manages an expiring, explicit
free-only authorization, verifies the shared lock and sealed-evaluation preflight,
and records number-free attempt metadata before the existing research workflow runs.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import general_sealed_evaluation as sealed
from general_nvidia_guard import validate_lock_state
import general_target_queue as target_queue
import general_target_evaluation_ledger as target_evaluation_ledger

VERSION = "0.2.0"
PROJECT = "GENERAL Super Loto"
RESEARCH_SCOPE_START = date(2020, 8, 2)
AUTO_CONFIRMATION_PHRASE = "I_AUTHORIZE_AUTONOMOUS_FREE_ONLY_NVIDIA_RESEARCH_NO_BILLING"
MAX_AUTHORIZATION_DAYS = 30
# Uniform cap for new targets; prior journals are retained as historical evidence.
MAX_ATTEMPTS_PER_TARGET = 10
CYCLE55_MANUAL_RETRY_TARGET_DATE = "2026-11-10"
CYCLE55_MANUAL_RETRY_ARCHIVE_SHA256 = "ae7b79e4922d4178a86c7d7880af3e4758d1a5e7f78d6cc3d2595fbb4fd66596"
CYCLE55_MANUAL_RETRY_ARCHIVE_RECORD_COUNT = 983
CYCLE55_MANUAL_RETRY_CONFIRMATION = "CYCLE55_MANUAL_RETRY_2026_11_13_ONE_TIME_ONLY"
CYCLE55_MANUAL_RETRY_CONFIRMATION_SHA256 = "e7a7845bafd4bef2191f2137663c60d1f855a9c1a46c5b3d0ce9e1a0bbcd2a67"
CYCLE55_MANUAL_RETRY_ACTOR_LOGIN = "xxlyusufi61-bot"
CYCLE55_MANUAL_RETRY_ACTOR_ID = 345811504
CYCLE55_MANUAL_RETRY_WORKFLOW_ID = 403181605
CYCLE55_MANUAL_RETRY_WORKFLOW_PATH = ".github/workflows/general-autonomous-research.yml"
CYCLE55_MANUAL_RETRY_PRIOR_ATTEMPT_IDS = (
    "a7f0195c30826b11cf6f8336d03d2f0954a196c2e76f336c7d0cbe83329740f3",
    "6892f02f5531ff1e8ca150d1bbe556b7f7ee5f6622cc17479020cb03b4fc6e06",
    "6ab3c502b6a74c16de8443c30ed7a8ecd589616c61804a0a8093a127ce27dce5",
)
CYCLE55_MANUAL_RETRY_PRIOR_FAILURE_AT_UTC = "2026-11-12T19:29:11.330937+00:00"
# One additional scheduled attempt is limited to the immutable 2026-11-10
# snapshot and the exact fourth-attempt 504 delivery failure from Cycle #57.
CYCLE57_RETRY_PRIOR_ATTEMPT_ID = "25135d06049ee281e793b792b5b1ae6f344418fc1038ea55b1524cfd8c3fdf4f"
CYCLE57_RETRY_FAILURE_AT_UTC = "2026-11-13T09:47:58.660638+00:00"
CYCLE57_RETRY_ATTEMPT_ID = "50c89cdfbb465f47491c6bfd67f709387fb1caf00ed9157651e99652cf2f3e6f"
RETRYABLE_FAILURE_REASON_CODES = {
    "NO_PROVIDER_REQUEST_SENT",
    "PROVIDER_DELIVERY_UNCERTAIN",
    "PROVIDER_HTTP_5XX_RETRYABLE",
    "DIRECTIONAL_CANDIDATE_SET_INCOMPLETE",
    "SEALED_EVALUATION_INCOMPLETE",
    "WORKFLOW_FAILED",
}
MANUAL_UNLOCK_REQUIRED_FAILURE_REASON_CODES = {
    "PROVIDER_DELIVERY_UNCERTAIN",
    "WORKFLOW_FAILED",
}
CYCLE55_RECOVERY_TARGET_DATE = "2026-11-10"
CYCLE55_RECOVERY_ARCHIVE_SHA256 = "ae7b79e4922d4178a86c7d7880af3e4758d1a5e7f78d6cc3d2595fbb4fd66596"
CYCLE55_RECOVERY_ARCHIVE_RECORD_COUNT = 983
CYCLE55_RECOVERY_CURRENT_GENERATION = 52
CYCLE55_RECOVERY_CURRENT_COMMIT_SHA = "0c9e5bbbe0cbafea188f941b0408e068080c1e68"
CYCLE55_RECOVERY_SNAPSHOT_GENERATION = 51
CYCLE55_RECOVERY_SNAPSHOT_COMMIT_SHA = "6197bfadb8af22633dcb22c5490d32c9ffbd52c7"
ALLOWED_OUTCOMES = {"SUCCEEDED", "FAILED", "NO_ELIGIBLE_CANDIDATE"}
ALLOWED_REASON_CODES = {
    "EVALUATION_COMPLETED",
    "WORKFLOW_FAILED",
    "NO_FROZEN_DIRECTIONAL_CANDIDATE",
    "NO_PROVIDER_REQUEST_SENT",
    "PROVIDER_DELIVERY_UNCERTAIN",
    "PROVIDER_HTTP_REJECTION_NO_RETRY",
    "PROVIDER_HTTP_5XX_RETRYABLE",
    "DIRECTIONAL_CANDIDATE_SET_INCOMPLETE",
    "SEALED_EVALUATION_INCOMPLETE",
}


class AutonomousResearchError(RuntimeError):
    """Raised when the autonomous scheduler cannot safely proceed."""


def _now_utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise AutonomousResearchError("Timestamp must include a timezone.")
    return current.astimezone(timezone.utc)


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise AutonomousResearchError(field + " must be an ISO-8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AutonomousResearchError(field + " is not a valid ISO-8601 timestamp.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AutonomousResearchError(field + " must include a timezone.")
    return parsed.astimezone(timezone.utc)


def _canonical_date(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise AutonomousResearchError(field + " must be YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise AutonomousResearchError(field + " is not a valid calendar date.") from None
    if parsed.isoformat() != value or parsed < RESEARCH_SCOPE_START:
        raise AutonomousResearchError(field + " is outside the active 6-of-60 research scope.")
    return value


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def initial_state() -> dict[str, Any]:
    return {
        "schema_version": 6,
        "project": PROJECT,
        "authorization": {
            "enabled": False,
            "authorized_at_utc": None,
            "expires_at_utc": None,
            "authorization_days": 0,
            "free_usage_verified": False,
            "max_requests_per_target": None,
            "last_paused_at_utc": None,
        },
        "attempts": [],
        "events": [],
        "target_queue": target_queue.initial_queue(),
        "recovery_history": [],
        "manual_retry_history": [],
        "workflow_retry_history": [],
        "target_evaluations": [],
    }


def validate_state(value: Any) -> dict[str, Any]:
    legacy_v1_keys = {"schema_version", "project", "authorization", "attempts", "events"}
    legacy_v2_keys = legacy_v1_keys | {"target_queue"}
    legacy_v3_keys = legacy_v2_keys | {"recovery_history"}
    legacy_v4_keys = legacy_v3_keys | {"manual_retry_history"}
    legacy_v5_keys = legacy_v4_keys | {"workflow_retry_history"}
    if isinstance(value, dict) and set(value) == legacy_v1_keys and value.get("schema_version") == 1:
        value["schema_version"] = 5
        value["target_queue"] = target_queue.initial_queue()
        value["recovery_history"] = []
        value["manual_retry_history"] = []
        value["workflow_retry_history"] = []
    elif isinstance(value, dict) and set(value) == legacy_v2_keys and value.get("schema_version") == 2:
        value["schema_version"] = 5
        value["recovery_history"] = []
        value["manual_retry_history"] = []
        value["workflow_retry_history"] = []
    elif isinstance(value, dict) and set(value) == legacy_v3_keys and value.get("schema_version") == 3:
        value["schema_version"] = 5
        value["manual_retry_history"] = []
        value["workflow_retry_history"] = []
    elif isinstance(value, dict) and set(value) == legacy_v4_keys and value.get("schema_version") == 4:
        value["schema_version"] = 5
        value["workflow_retry_history"] = []
    if isinstance(value, dict) and set(value) == legacy_v5_keys and value.get("schema_version") == 5:
        value["schema_version"] = 6
        value["target_evaluations"] = []
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "project", "authorization", "attempts", "events",
        "target_queue", "recovery_history", "manual_retry_history",
        "workflow_retry_history", "target_evaluations"
    }:
        raise AutonomousResearchError("Autonomous scheduler state has an invalid schema.")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 6:
        raise AutonomousResearchError("Unsupported autonomous scheduler state schema version.")
    try:
        target_queue.validate_queue(value["target_queue"])
    except target_queue.TargetQueueError as exc:
        raise AutonomousResearchError("Target FIFO queue failed validation: " + str(exc)) from None
    if value.get("project") != PROJECT:
        raise AutonomousResearchError("Autonomous scheduler project identity mismatch.")

    auth = value.get("authorization")
    required_auth = {
        "enabled", "authorized_at_utc", "expires_at_utc", "authorization_days",
        "free_usage_verified", "max_requests_per_target", "last_paused_at_utc",
    }
    if not isinstance(auth, dict) or set(auth) != required_auth:
        raise AutonomousResearchError("Autonomous authorization record has an invalid schema.")
    if type(auth["enabled"]) is not bool or type(auth["free_usage_verified"]) is not bool:
        raise AutonomousResearchError("Authorization flags must be booleans.")
    if type(auth["authorization_days"]) is not int or not 0 <= auth["authorization_days"] <= MAX_AUTHORIZATION_DAYS:
        raise AutonomousResearchError("Authorization duration is outside the supported range.")
    if auth["max_requests_per_target"] not in (None, 2):
        raise AutonomousResearchError("Deprecated target request-cap metadata is invalid.")
    # Legacy v1 states may still contain 2. It is ignored; all provider calls
    # are governed by the shared rolling budget and per-package limiter.
    for key in ("authorized_at_utc", "expires_at_utc", "last_paused_at_utc"):
        if auth[key] is not None:
            _timestamp(auth[key], "authorization." + key)
    if auth["enabled"]:
        if not auth["free_usage_verified"] or auth["authorization_days"] < 1:
            raise AutonomousResearchError("Active automation lacks verified free-only authorization.")
        if auth["authorized_at_utc"] is None or auth["expires_at_utc"] is None:
            raise AutonomousResearchError("Active automation is missing its authorization window.")
        if _timestamp(auth["expires_at_utc"], "authorization.expires_at_utc") <= _timestamp(
            auth["authorized_at_utc"], "authorization.authorized_at_utc"
        ):
            raise AutonomousResearchError("Authorization expiry must follow its start time.")

    attempts = value.get("attempts")
    events = value.get("events")
    if not isinstance(attempts, list) or len(attempts) > 10000:
        raise AutonomousResearchError("Attempt ledger is not a valid bounded list.")
    if not isinstance(events, list) or len(events) > 20000:
        raise AutonomousResearchError("Event ledger is not a valid bounded list.")

    attempt_ids: set[str] = set()
    attempt_dates: list[str] = []
    required_attempt = {
        "attempt_id", "target_date", "archive_sha256", "archive_record_count",
        "claimed_at_utc", "github_run_id",
    }
    for item in attempts:
        if not isinstance(item, dict) or set(item) != required_attempt:
            raise AutonomousResearchError("Attempt entry has an invalid schema.")
        attempt_id = item["attempt_id"]
        if not isinstance(attempt_id, str) or len(attempt_id) != 64 or any(
            character not in "0123456789abcdef" for character in attempt_id
        ):
            raise AutonomousResearchError("Attempt identifier is invalid.")
        if attempt_id in attempt_ids:
            raise AutonomousResearchError("Attempt identifier is duplicated.")
        attempt_ids.add(attempt_id)
        attempt_dates.append(_canonical_date(item["target_date"], "attempt.target_date"))
        if not _is_sha256(item["archive_sha256"]):
            raise AutonomousResearchError("Attempt archive hash is invalid.")
        if type(item["archive_record_count"]) is not int or item["archive_record_count"] < 1:
            raise AutonomousResearchError("Attempt archive record count is invalid.")
        _timestamp(item["claimed_at_utc"], "attempt.claimed_at_utc")
        run_id = item["github_run_id"]
        if run_id is not None and (
            not isinstance(run_id, str) or not run_id.isdigit() or len(run_id) > 32
        ):
            raise AutonomousResearchError("GitHub run identifier is invalid.")
    if attempt_dates != sorted(attempt_dates):
        raise AutonomousResearchError("Attempt targets must remain chronological.")
    terminal_attempts: set[str] = set()
    required_event = {"attempt_id", "outcome", "recorded_at_utc", "reason_code"}
    for event in events:
        if not isinstance(event, dict) or set(event) != required_event:
            raise AutonomousResearchError("Event entry has an invalid schema.")
        if event["attempt_id"] not in attempt_ids:
            raise AutonomousResearchError("Event references an unknown attempt.")
        if event["outcome"] not in ALLOWED_OUTCOMES:
            raise AutonomousResearchError("Event outcome is not allowed.")
        if event["reason_code"] not in ALLOWED_REASON_CODES:
            raise AutonomousResearchError("Event reason code is not allowed.")
        _timestamp(event["recorded_at_utc"], "event.recorded_at_utc")
        if event["attempt_id"] in terminal_attempts:
            raise AutonomousResearchError("An attempt already has a terminal event.")
        terminal_attempts.add(event["attempt_id"])

    event_by_attempt = {event["attempt_id"]: event for event in events}
    attempt_by_id = {item["attempt_id"]: item for item in attempts}

    target_evaluations = value.get("target_evaluations")
    if not isinstance(target_evaluations, list) or len(target_evaluations) > 100_000:
        raise AutonomousResearchError("Private local target-evaluation history is not a bounded list.")
    try:
        target_evaluation_ledger.validate_ledger({
            "schema_version": target_evaluation_ledger.SCHEMA_VERSION,
            "purpose": target_evaluation_ledger.PURPOSE,
            "entries": target_evaluations,
        })
    except target_evaluation_ledger.TargetEvaluationLedgerError as exc:
        raise AutonomousResearchError(
            "Private local target-evaluation history failed validation: " + str(exc)
        ) from None
    for entry in target_evaluations:
        attempt = attempt_by_id.get(entry["attempt_id"])
        if attempt is None or (
            attempt["target_date"] != entry["target_date"]
            or attempt["archive_sha256"] != entry["archive_sha256"]
            or attempt["archive_record_count"] != entry["archive_record_count"]
        ):
            raise AutonomousResearchError("Private local target result does not match a recorded scheduler attempt.")

    recovery_history = value.get("recovery_history")
    if not isinstance(recovery_history, list) or len(recovery_history) > 256:
        raise AutonomousResearchError("Recovery journal is not a valid bounded list.")
    recovery_ids: set[str] = set()
    recovered_attempt_ids: set[str] = set()
    required_recovery = {
        "schema_version", "recovery_id", "recovery_type", "recorded_at_utc",
        "attempt_id", "target_date", "archive_sha256", "archive_record_count",
        "failure_at_utc", "reason_code", "source_state", "snapshot_source",
        "archived_terminal_event", "archived_completed_record",
        "restored_snapshot_item_sha256",
    }
    source_keys = {"generation", "commit_sha", "blob_sha", "content_sha256"}
    for entry in recovery_history:
        if not isinstance(entry, dict) or set(entry) != required_recovery:
            raise AutonomousResearchError("Recovery journal entry has an invalid schema.")
        if type(entry["schema_version"]) is not int or entry["schema_version"] != 1:
            raise AutonomousResearchError("Unsupported recovery journal entry version.")
        recovery_id = entry["recovery_id"]
        attempt_id = entry["attempt_id"]
        if not _is_sha256(recovery_id) or recovery_id in recovery_ids:
            raise AutonomousResearchError("Recovery journal identifier is invalid or duplicated.")
        if not isinstance(attempt_id, str) or len(attempt_id) != 64 or attempt_id not in attempt_ids:
            raise AutonomousResearchError("Recovery journal references an unknown attempt.")
        if attempt_id in recovered_attempt_ids:
            raise AutonomousResearchError("An attempt cannot have multiple recovery journal entries.")
        recovery_ids.add(recovery_id)
        recovered_attempt_ids.add(attempt_id)
        if entry["recovery_type"] != "CYCLE55_FALSE_TERMINAL_COMPLETION":
            raise AutonomousResearchError("Recovery journal type is not allowed.")
        if entry["reason_code"] != "POST_PROVIDER_MEMORY_APPEND_FAILURE":
            raise AutonomousResearchError("Recovery journal reason code is not allowed.")
        target = _canonical_date(entry["target_date"], "recovery.target_date")
        if not _is_sha256(entry["archive_sha256"]):
            raise AutonomousResearchError("Recovery archive hash is invalid.")
        if type(entry["archive_record_count"]) is not int or entry["archive_record_count"] < 1:
            raise AutonomousResearchError("Recovery archive record count is invalid.")
        if not _is_sha256(entry["restored_snapshot_item_sha256"]):
            raise AutonomousResearchError("Recovery snapshot item hash is invalid.")
        _timestamp(entry["recorded_at_utc"], "recovery.recorded_at_utc")
        _timestamp(entry["failure_at_utc"], "recovery.failure_at_utc")
        for field in ("source_state", "snapshot_source"):
            provenance = entry[field]
            if not isinstance(provenance, dict) or set(provenance) != source_keys:
                raise AutonomousResearchError("Recovery source provenance has an invalid schema.")
            if type(provenance["generation"]) is not int or provenance["generation"] < 1:
                raise AutonomousResearchError("Recovery source generation is invalid.")
            if not isinstance(provenance["commit_sha"], str) or len(provenance["commit_sha"]) != 40 or any(c not in "0123456789abcdef" for c in provenance["commit_sha"]):
                raise AutonomousResearchError("Recovery source commit SHA is invalid.")
            if not isinstance(provenance["blob_sha"], str) or len(provenance["blob_sha"]) != 40 or any(c not in "0123456789abcdef" for c in provenance["blob_sha"]):
                raise AutonomousResearchError("Recovery source blob SHA is invalid.")
            if not _is_sha256(provenance["content_sha256"]):
                raise AutonomousResearchError("Recovery source content SHA-256 is invalid.")
        if entry["snapshot_source"]["generation"] >= entry["source_state"]["generation"]:
            raise AutonomousResearchError("Recovery snapshot source must predate the faulty state.")
        if (
            entry["source_state"]["generation"] != CYCLE55_RECOVERY_CURRENT_GENERATION
            or entry["source_state"]["commit_sha"] != CYCLE55_RECOVERY_CURRENT_COMMIT_SHA
            or entry["snapshot_source"]["generation"] != CYCLE55_RECOVERY_SNAPSHOT_GENERATION
            or entry["snapshot_source"]["commit_sha"] != CYCLE55_RECOVERY_SNAPSHOT_COMMIT_SHA
            or target != CYCLE55_RECOVERY_TARGET_DATE
            or entry["archive_sha256"] != CYCLE55_RECOVERY_ARCHIVE_SHA256
            or entry["archive_record_count"] != CYCLE55_RECOVERY_ARCHIVE_RECORD_COUNT
        ):
            raise AutonomousResearchError("Recovery journal does not match the pinned Cycle #55 provenance.")
        failure_time = _timestamp(entry["failure_at_utc"], "recovery.failure_at_utc")
        attempt = next(item for item in attempts if item["attempt_id"] == attempt_id)
        if (
            attempt["target_date"] != target
            or attempt["archive_sha256"] != entry["archive_sha256"]
            or attempt["archive_record_count"] != entry["archive_record_count"]
        ):
            raise AutonomousResearchError("Recovery journal does not match its attempt metadata.")
        archived_event = entry["archived_terminal_event"]
        if (
            not isinstance(archived_event, dict)
            or set(archived_event) != required_event
            or archived_event["attempt_id"] != attempt_id
            or archived_event["outcome"] != "NO_ELIGIBLE_CANDIDATE"
            or archived_event["reason_code"] != "NO_FROZEN_DIRECTIONAL_CANDIDATE"
        ):
            raise AutonomousResearchError("Archived terminal event is not the expected false completion.")
        archived_event_time = _timestamp(
            archived_event["recorded_at_utc"], "recovery.archived_terminal_event.recorded_at_utc"
        )
        if archived_event_time > failure_time:
            raise AutonomousResearchError("Archived terminal event postdates the recorded provider failure.")
        archived_completed = entry["archived_completed_record"]
        if (
            not isinstance(archived_completed, dict)
            or set(archived_completed) != {
                "target_date", "archive_sha256", "archive_record_count",
                "completed_at_utc", "outcome",
            }
            or archived_completed["target_date"] != target
            or archived_completed["archive_sha256"] != entry["archive_sha256"]
            or archived_completed["archive_record_count"] != entry["archive_record_count"]
            or archived_completed["outcome"] != "NO_ELIGIBLE_CANDIDATE"
        ):
            raise AutonomousResearchError("Archived FIFO completion record is invalid.")
        archived_completed_time = _timestamp(
            archived_completed["completed_at_utc"],
            "recovery.archived_completed_record.completed_at_utc",
        )
        if archived_completed_time > failure_time:
            raise AutonomousResearchError("Archived FIFO completion postdates the recorded provider failure.")

        recovery_payload = {key: val for key, val in entry.items() if key != "recovery_id"}
        expected_recovery_id = hashlib.sha256(
            json.dumps(recovery_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        if recovery_id != expected_recovery_id:
            raise AutonomousResearchError("Recovery journal digest does not match its metadata.")

    for entry in recovery_history:
        active_event = event_by_attempt.get(entry["attempt_id"])
        if (
            active_event is None
            or active_event["outcome"] != "FAILED"
            or active_event["reason_code"] != "WORKFLOW_FAILED"
            or active_event["recorded_at_utc"] != entry["failure_at_utc"]
        ):
            raise AutonomousResearchError("Recovered attempt is not actively classified as WORKFLOW_FAILED.")
        # The recovery journal may outlive the legacy FIFO schema. Resolve
        # the current head through the queue API so schema-v2 uses its single
        # active snapshot rather than dereferencing the removed "items" field.
        queue_head = target_queue.active_item(value["target_queue"])
        queue_head_matches = False
        if queue_head is not None:
            queue_head_sha = hashlib.sha256(
                json.dumps(queue_head, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            ).hexdigest()
            queue_head_matches = (
                queue_head["target_date"] == entry["target_date"]
                and queue_head["archive_sha256"] == entry["archive_sha256"]
                and queue_head["archive_record_count"] == entry["archive_record_count"]
                and queue_head_sha == entry["restored_snapshot_item_sha256"]
            )
        if not queue_head_matches:
            # The original recovery journal remains valid after the explicitly
            # authorized fourth attempt completes, but only if the exact FIFO
            # item was atomically moved into completed by that attempt.
            manual_history = value.get("manual_retry_history", [])
            manual = manual_history[0] if isinstance(manual_history, list) and len(manual_history) == 1 else None
            manual_event = event_by_attempt.get(manual.get("attempt_id")) if isinstance(manual, dict) else None
            completed = [
                item for item in value["target_queue"]["completed"]
                if item.get("target_date") == entry["target_date"]
                and item.get("archive_sha256") == entry["archive_sha256"]
                and item.get("archive_record_count") == entry["archive_record_count"]
            ]
            manual_retry_completed = (
                isinstance(manual, dict)
                and manual.get("prior_attempt_id") == entry["attempt_id"]
                and manual.get("target_date") == entry["target_date"]
                and manual_event is not None
                and len(completed) == 1
                and (
                    (
                        manual_event.get("outcome") == "SUCCEEDED"
                        and completed[0].get("outcome") in {"EVALUATED", "SUCCEEDED"}
                    )
                    or (
                        manual_event.get("outcome") == "NO_ELIGIBLE_CANDIDATE"
                        and completed[0].get("outcome") == "NO_ELIGIBLE_CANDIDATE"
                    )
                )
            )
            exhausted_attempts = [
                attempt for attempt in attempts
                if attempt["target_date"] == entry["target_date"]
            ]
            # Cycle #55's recovery journal points to the already-retired historical
            # five-attempt record. Keep that exact audit shape independent of the
            # new ten-attempt policy used for all future targets.
            exhausted_events_complete = (
                len(exhausted_attempts) == 5
                and all(
                    event_by_attempt.get(attempt["attempt_id"]) is not None
                    and event_by_attempt[attempt["attempt_id"]]["outcome"] == "FAILED"
                    for attempt in exhausted_attempts
                )
            )
            exhausted_target_completed = (
                len(completed) == 1
                and completed[0].get("outcome") == "EXHAUSTED_RETRY_BUDGET"
                and exhausted_events_complete
            )
            if not manual_retry_completed and not exhausted_target_completed:
                raise AutonomousResearchError("Recovered Cycle #55 snapshot is neither the exact FIFO head nor a valid historical completion record.")
    manual_retry_history = value.get("manual_retry_history")
    if not isinstance(manual_retry_history, list) or len(manual_retry_history) > 1:
        raise AutonomousResearchError("Cycle #55 one-time manual retry journal is not a valid single-use list.")
    manual_retry = manual_retry_history[0] if manual_retry_history else None
    if manual_retry is not None:
        required_manual_retry = {
            "schema_version", "authorization_id", "authorization_type", "target_date",
            "archive_sha256", "archive_record_count", "prior_attempt_id",
            "prior_failure_at_utc", "prior_failure_reason_code", "attempt_id",
            "github_run_id", "github_workflow_id", "github_workflow_path",
            "github_actor_login", "github_actor_id", "approval_phrase_sha256",
            "authorized_at_utc", "claimed_at_utc", "preflight_sha256",
            "forward_validation_count", "reverse_validation_count", "discovery_count",
            "fifo_snapshot_sha256", "consumed",
        }
        if not isinstance(manual_retry, dict) or set(manual_retry) != required_manual_retry:
            raise AutonomousResearchError("Cycle #55 manual retry authorization has an invalid schema.")
        if type(manual_retry["schema_version"]) is not int or manual_retry["schema_version"] != 1:
            raise AutonomousResearchError("Unsupported Cycle #55 manual retry journal version.")
        if (
            manual_retry["authorization_type"] != "CYCLE55_PR103_ONE_TIME_EXCEPTION"
            or manual_retry["target_date"] != CYCLE55_MANUAL_RETRY_TARGET_DATE
            or manual_retry["archive_sha256"] != CYCLE55_MANUAL_RETRY_ARCHIVE_SHA256
            or manual_retry["archive_record_count"] != CYCLE55_MANUAL_RETRY_ARCHIVE_RECORD_COUNT
            or manual_retry["prior_attempt_id"] != CYCLE55_MANUAL_RETRY_PRIOR_ATTEMPT_IDS[-1]
            or manual_retry["prior_failure_reason_code"] != "WORKFLOW_FAILED"
            or manual_retry["prior_failure_at_utc"] != CYCLE55_MANUAL_RETRY_PRIOR_FAILURE_AT_UTC
            or manual_retry["github_workflow_id"] != CYCLE55_MANUAL_RETRY_WORKFLOW_ID
            or manual_retry["github_workflow_path"] != CYCLE55_MANUAL_RETRY_WORKFLOW_PATH
            or manual_retry["github_actor_login"] != CYCLE55_MANUAL_RETRY_ACTOR_LOGIN
            or manual_retry["github_actor_id"] != CYCLE55_MANUAL_RETRY_ACTOR_ID
            or manual_retry["approval_phrase_sha256"] != CYCLE55_MANUAL_RETRY_CONFIRMATION_SHA256
            or manual_retry["consumed"] is not True
        ):
            raise AutonomousResearchError("Cycle #55 manual retry journal exceeds or mismatches the one-target authorization.")
        if (
            not _is_sha256(manual_retry["authorization_id"])
            or not _is_sha256(manual_retry["preflight_sha256"])
            or not _is_sha256(manual_retry["fifo_snapshot_sha256"])
        ):
            raise AutonomousResearchError("Cycle #55 manual retry journal digest is invalid.")
        if (
            not isinstance(manual_retry["github_run_id"], str)
            or not manual_retry["github_run_id"].isdigit()
            or len(manual_retry["github_run_id"]) > 32
        ):
            raise AutonomousResearchError("Cycle #55 manual retry GitHub run ID is invalid.")
        _timestamp(manual_retry["authorized_at_utc"], "manual_retry.authorized_at_utc")
        claimed_at = _timestamp(manual_retry["claimed_at_utc"], "manual_retry.claimed_at_utc")
        if claimed_at < _timestamp(manual_retry["authorized_at_utc"], "manual_retry.authorized_at_utc"):
            raise AutonomousResearchError("Manual retry claim predates its dispatch authorization.")
        for field, minimum in (
            ("forward_validation_count", sealed.MIN_VALIDATION_DRAWS),
            ("reverse_validation_count", sealed.MIN_VALIDATION_DRAWS),
            ("discovery_count", sealed.DEFAULT_MIN_TRAIN_DRAWS),
        ):
            count = manual_retry[field]
            if type(count) is not int or count < minimum:
                raise AutonomousResearchError("Manual retry journal does not prove minimum fresh sealed windows.")
        target_attempts = [item for item in attempts if item["target_date"] == CYCLE55_MANUAL_RETRY_TARGET_DATE]
        if len(target_attempts) not in (4, 5) or [item["attempt_id"] for item in target_attempts[:3]] != list(CYCLE55_MANUAL_RETRY_PRIOR_ATTEMPT_IDS):
            raise AutonomousResearchError("Manual retry journal is not bound to exactly the three pinned prior attempts.")
        fourth = target_attempts[3]
        if (
            fourth["attempt_id"] != manual_retry["attempt_id"]
            or fourth["github_run_id"] != manual_retry["github_run_id"]
            or fourth["archive_sha256"] != CYCLE55_MANUAL_RETRY_ARCHIVE_SHA256
            or fourth["archive_record_count"] != CYCLE55_MANUAL_RETRY_ARCHIVE_RECORD_COUNT
            or fourth["claimed_at_utc"] != manual_retry["claimed_at_utc"]
        ):
            raise AutonomousResearchError("The fourth attempt does not match its consumed one-time authorization.")
        if len(target_attempts) == 5:
            fifth = target_attempts[4]
            fourth_event = event_by_attempt.get(fourth["attempt_id"])
            if (
                fifth["attempt_id"] != CYCLE57_RETRY_ATTEMPT_ID
                or fifth["archive_sha256"] != CYCLE55_MANUAL_RETRY_ARCHIVE_SHA256
                or fifth["archive_record_count"] != CYCLE55_MANUAL_RETRY_ARCHIVE_RECORD_COUNT
                or fourth_event is None
                or fourth_event["outcome"] != "FAILED"
                or fourth_event["reason_code"] != "PROVIDER_DELIVERY_UNCERTAIN"
                or fourth_event["recorded_at_utc"] != CYCLE57_RETRY_FAILURE_AT_UTC
            ):
                raise AutonomousResearchError("The fifth attempt does not match the one-time Cycle #57 scheduled retry authorization.")
        prior_event = event_by_attempt.get(manual_retry["prior_attempt_id"])
        if (
            prior_event is None
            or prior_event["outcome"] != "FAILED"
            or prior_event["reason_code"] != "WORKFLOW_FAILED"
            or prior_event["recorded_at_utc"] != manual_retry["prior_failure_at_utc"]
        ):
            raise AutonomousResearchError("Manual retry exception does not match Cycle #55's final WORKFLOW_FAILED event.")
        payload = {key: item for key, item in manual_retry.items() if key != "authorization_id"}
        expected_id = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        if manual_retry["authorization_id"] != expected_id:
            raise AutonomousResearchError("Cycle #55 manual retry authorization digest does not match its immutable metadata.")

    workflow_retry_history = value.get("workflow_retry_history")
    if not isinstance(workflow_retry_history, list) or len(workflow_retry_history) > 10000:
        raise AutonomousResearchError("Workflow retry journal is not a valid bounded list.")
    attempt_by_id = {item["attempt_id"]: item for item in attempts}
    attempt_position = {item["attempt_id"]: index for index, item in enumerate(attempts)}
    workflow_retry_by_prior_attempt: dict[str, dict[str, Any]] = {}
    workflow_retry_ids: set[str] = set()
    workflow_retry_attempt_ids: set[str] = set()
    required_workflow_retry = {
        "schema_version", "retry_id", "target_date", "archive_sha256",
        "archive_record_count", "prior_attempt_id", "prior_failure_reason_code",
        "prior_failure_at_utc", "manual_unlocked_at_utc", "retry_attempt_id",
        "retry_github_run_id", "claimed_at_utc", "consumed",
    }
    for entry in workflow_retry_history:
        if not isinstance(entry, dict) or set(entry) != required_workflow_retry:
            raise AutonomousResearchError("Workflow retry journal entry has an invalid schema.")
        if type(entry["schema_version"]) is not int or entry["schema_version"] != 1:
            raise AutonomousResearchError("Unsupported workflow retry journal version.")
        retry_id = entry["retry_id"]
        prior_id = entry["prior_attempt_id"]
        retry_attempt_id = entry["retry_attempt_id"]
        if not _is_sha256(retry_id) or retry_id in workflow_retry_ids:
            raise AutonomousResearchError("Workflow retry journal identifier is invalid or duplicated.")
        if (
            not isinstance(prior_id, str) or prior_id not in attempt_by_id
            or not isinstance(retry_attempt_id, str) or retry_attempt_id not in attempt_by_id
            or prior_id in workflow_retry_by_prior_attempt
            or retry_attempt_id in workflow_retry_attempt_ids
        ):
            raise AutonomousResearchError("Workflow retry journal references an unknown or reused attempt.")
        if entry["consumed"] is not True or entry["prior_failure_reason_code"] != "WORKFLOW_FAILED":
            raise AutonomousResearchError("Workflow retry journal is not a consumed WORKFLOW_FAILED authorization.")
        target = _canonical_date(entry["target_date"], "workflow_retry.target_date")
        if not _is_sha256(entry["archive_sha256"]):
            raise AutonomousResearchError("Workflow retry archive hash is invalid.")
        if type(entry["archive_record_count"]) is not int or entry["archive_record_count"] < 1:
            raise AutonomousResearchError("Workflow retry archive record count is invalid.")
        failure_at = _timestamp(entry["prior_failure_at_utc"], "workflow_retry.prior_failure_at_utc")
        unlocked_at = _timestamp(entry["manual_unlocked_at_utc"], "workflow_retry.manual_unlocked_at_utc")
        claimed_at = _timestamp(entry["claimed_at_utc"], "workflow_retry.claimed_at_utc")
        if not failure_at < unlocked_at <= claimed_at:
            raise AutonomousResearchError("Manual unlock must postdate the failure and predate the retry claim.")
        run_id = entry["retry_github_run_id"]
        if not isinstance(run_id, str) or not run_id.isdigit() or len(run_id) > 32:
            raise AutonomousResearchError("Workflow retry GitHub run identifier is invalid.")
        prior_attempt = attempt_by_id[prior_id]
        retry_attempt = attempt_by_id[retry_attempt_id]
        prior_event = event_by_attempt.get(prior_id)
        if (
            attempt_position[retry_attempt_id] != attempt_position[prior_id] + 1
            or prior_event is None
            or prior_event["outcome"] != "FAILED"
            or prior_event["reason_code"] != "WORKFLOW_FAILED"
            or prior_event["recorded_at_utc"] != entry["prior_failure_at_utc"]
            or prior_attempt["target_date"] != target
            or retry_attempt["target_date"] != target
            or prior_attempt["archive_sha256"] != entry["archive_sha256"]
            or retry_attempt["archive_sha256"] != entry["archive_sha256"]
            or prior_attempt["archive_record_count"] != entry["archive_record_count"]
            or retry_attempt["archive_record_count"] != entry["archive_record_count"]
            or retry_attempt["github_run_id"] != run_id
            or retry_attempt["claimed_at_utc"] != entry["claimed_at_utc"]
        ):
            raise AutonomousResearchError("Workflow retry journal does not match the exact failed FIFO attempt and its immediate retry.")
        payload = {key: item for key, item in entry.items() if key != "retry_id"}
        expected_retry_id = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        if retry_id != expected_retry_id:
            raise AutonomousResearchError("Workflow retry journal digest does not match its metadata.")
        workflow_retry_ids.add(retry_id)
        workflow_retry_attempt_ids.add(retry_attempt_id)
        workflow_retry_by_prior_attempt[prior_id] = entry

    attempts_by_target: dict[str, list[dict[str, Any]]] = {}
    for item in attempts:
        attempts_by_target.setdefault(item["target_date"], []).append(item)
    for target, target_attempts in attempts_by_target.items():
        failed_attempt_count = sum(
            1 for attempt in target_attempts
            if event_by_attempt.get(attempt["attempt_id"], {}).get("outcome") == "FAILED"
        )
        if failed_attempt_count > MAX_ATTEMPTS_PER_TARGET:
            raise AutonomousResearchError("A target exceeded the ten-failed-attempt limit.")
    for target, target_attempts in attempts_by_target.items():
        if len(target_attempts) > 1:
            for index, prior_attempt in enumerate(target_attempts[:-1]):
                prior_event = event_by_attempt.get(prior_attempt["attempt_id"])
                normal_retry = (
                    prior_event is not None
                    and (
                        (
                            prior_event["outcome"] == "FAILED"
                            and prior_event["reason_code"] in RETRYABLE_FAILURE_REASON_CODES
                        )
                        or (
                            prior_event["outcome"] == "SUCCEEDED"
                            and prior_event["reason_code"] == "EVALUATION_COMPLETED"
                        )
                        or (
                            prior_event["outcome"] == "NO_ELIGIBLE_CANDIDATE"
                            and prior_event["reason_code"] == "NO_FROZEN_DIRECTIONAL_CANDIDATE"
                        )
                    )
                )
                if prior_event is not None and prior_event["reason_code"] == "WORKFLOW_FAILED":
                    authorization = workflow_retry_by_prior_attempt.get(prior_attempt["attempt_id"])
                    next_attempt_id = target_attempts[index + 1]["attempt_id"]
                    normal_retry = (
                        authorization is not None
                        and authorization["retry_attempt_id"] == next_attempt_id
                    )
                approved_cycle55_exception = (
                    target == CYCLE55_MANUAL_RETRY_TARGET_DATE
                    and len(target_attempts) in (4, 5)
                    and manual_retry is not None
                    and index == 2
                    and prior_attempt["attempt_id"] == manual_retry["prior_attempt_id"]
                    and prior_event is not None
                    and prior_event["outcome"] == "FAILED"
                    and prior_event["reason_code"] == "WORKFLOW_FAILED"
                )
                if not (normal_retry or approved_cycle55_exception):
                    raise AutonomousResearchError(
                        "Repeated target attempts require a retryable failure or a recorded no-candidate outcome; only the consumed Cycle #55 authorization permits its exact fourth attempt."
                    )

    return value


def read_state(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return initial_state()
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise AutonomousResearchError("Autonomous scheduler state could not be read as valid JSON.") from None
    return validate_state(value)


def write_state(path: str | Path, value: dict[str, Any]) -> None:
    checked = validate_state(value)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(checked, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def read_global_lock(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise AutonomousResearchError("Global NVIDIA lock is missing or malformed; automation is blocked.") from None
    try:
        return validate_lock_state(value)
    except Exception:
        raise AutonomousResearchError("Global NVIDIA lock failed validation; automation is blocked.") from None


def manual_recovery_unlock_timestamp(
    lock_state: dict[str, Any], failed_event: dict[str, Any]
) -> str | None:
    """Return only a manual unlock recorded strictly after a gated failure."""
    if failed_event.get("reason_code") not in MANUAL_UNLOCK_REQUIRED_FAILURE_REASON_CODES:
        return None
    unlocked_at = lock_state.get("last_unlocked_at")
    if not isinstance(unlocked_at, str):
        return None
    try:
        if _timestamp(unlocked_at, "global_lock.last_unlocked_at") > _timestamp(
            failed_event.get("recorded_at_utc"),
            "failed_event.recorded_at_utc",
        ):
            return unlocked_at
    except AutonomousResearchError:
        return None
    return None


def manual_recovery_confirmed(lock_state: dict[str, Any], failed_event: dict[str, Any]) -> bool:
    return manual_recovery_unlock_timestamp(lock_state, failed_event) is not None



def activate_state(
    state: dict[str, Any],
    *,
    confirmation: str,
    free_usage_verified: bool,
    authorization_days: int,
    global_lock: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_state(state)
    if confirmation != AUTO_CONFIRMATION_PHRASE:
        raise AutonomousResearchError("Exact autonomous NVIDIA authorization phrase is required.")
    if free_usage_verified is not True:
        raise AutonomousResearchError("Free-only/no-billing status must be independently verified.")
    if type(authorization_days) is not int or not 1 <= authorization_days <= MAX_AUTHORIZATION_DAYS:
        raise AutonomousResearchError("Authorization duration must be between 1 and 30 days.")
    try:
        checked_lock = validate_lock_state(global_lock)
    except Exception:
        raise AutonomousResearchError("Global NVIDIA lock failed validation; activation is blocked.") from None
    if checked_lock["locked"]:
        raise AutonomousResearchError("Global NVIDIA lock is active; use the separate manual unlock workflow first.")

    current = _now_utc(now)
    expires = current + timedelta(days=authorization_days)
    state["authorization"] = {
        "enabled": True,
        "authorized_at_utc": current.isoformat(),
        "expires_at_utc": expires.isoformat(),
        "authorization_days": authorization_days,
        "free_usage_verified": True,
        "max_requests_per_target": None,
        "last_paused_at_utc": state["authorization"].get("last_paused_at_utc"),
    }
    return validate_state(state)


def pause_state(state: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    validate_state(state)
    state["authorization"]["enabled"] = False
    state["authorization"]["last_paused_at_utc"] = _now_utc(now).isoformat()
    return validate_state(state)


def authorization_status(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    checked = validate_state(state)
    current = _now_utc(now)
    auth = checked["authorization"]
    not_expired = (
        auth["expires_at_utc"] is not None
        and current < _timestamp(auth["expires_at_utc"], "authorization.expires_at_utc")
    )
    authorized = auth["enabled"] and auth["free_usage_verified"] and not_expired
    return {
        "authorized": authorized,
        "authorization_enabled": auth["enabled"],
        "authorization_expired": bool(auth["enabled"] and not not_expired),
        "expires_at_utc": auth["expires_at_utc"],
        "authorization_days": auth["authorization_days"],
        "attempted_target_count": len(checked["attempts"]),
        "last_attempted_target_date": (
            checked["attempts"][-1]["target_date"] if checked["attempts"] else None
        ),
    }


def _cycle55_manual_retry_preconditions(
    state: dict[str, Any], *, archive_path: str | Path, ledger_path: str | Path,
    global_lock_path: str | Path, now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Retired historical exception; the normal ten-attempt policy applies."""
    raise AutonomousResearchError("The historical one-time retry path is retired; the standard ten-attempt budget applies.")


def prepare_cycle55_manual_retry(
    state: dict[str, Any], *, archive_path: str | Path, ledger_path: str | Path,
    global_lock_path: str | Path, now: datetime | None = None,
) -> dict[str, Any]:
    """Reject the retired exception without reading or changing state."""
    raise AutonomousResearchError("The historical one-time retry path is retired; the standard ten-attempt budget applies.")


def claim_cycle55_manual_retry(
    state: dict[str, Any], plan: dict[str, Any], *, github_run: dict[str, Any],
    confirmation: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Reject the retired exception without reserving an attempt."""
    raise AutonomousResearchError("The historical one-time retry path is retired; the standard ten-attempt budget applies.")


def prepare_cycle(
    state: dict[str, Any],
    *,
    archive_path: str | Path,
    ledger_path: str | Path,
    global_lock_path: str | Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_state(state)
    status = authorization_status(state, now=now)
    if not status["authorized"]:
        return {"status": "AUTOMATION_NOT_AUTHORIZED", "eligible": False, **status}

    lock = read_global_lock(global_lock_path)
    if lock["locked"]:
        return {"status": "GLOBAL_NVIDIA_LOCK_ACTIVE", "eligible": False, **status}

    archive = sealed.load_archive(archive_path)
    target_date = _canonical_date(archive["last_date"], "archive.last_date")
    queue = target_queue.validate_queue(state["target_queue"])
    active_item = target_queue.active_item(queue)
    if active_item is None:
        updated_queue, _ = target_queue.capture_new_targets(
            queue,
            archive_path=archive_path,
            attempts=state["attempts"],
            events=state["events"],
            now=now,
        )
        state["target_queue"] = updated_queue
        queue = target_queue.validate_queue(updated_queue)
        active_item = target_queue.active_item(queue)
    if active_item is None:
        return {
            "status": "WAITING_FOR_NEW_LATEST_DRAW",
            "eligible": False,
            "latest_observed_date": queue.get("observed_through_date"),
            **status,
        }
    if target_date < active_item["target_date"]:
        raise AutonomousResearchError(
            "Archive target moved backwards relative to the immutable active-target snapshot."
        )
    archive_hash_matches_snapshot = archive["archive_sha256"] == active_item["archive_sha256"]
    archive_hash_matches_source = archive["archive_sha256"] == active_item["source_archive_sha256"]
    if (
        target_date != active_item["target_date"]
        or not (archive_hash_matches_snapshot or archive_hash_matches_source)
        or archive["record_count"] != active_item["archive_record_count"]
    ):
        raise AutonomousResearchError(
            "Prepared archive is not the immutable active-target snapshot."
        )
    attempts = state["attempts"]
    retryable = False
    retry_number = 1
    manual_recovery_confirmed_for_failure = False
    manual_recovery_unlock_at_utc: str | None = None
    if attempts:
        last_attempt = attempts[-1]
        last_target = last_attempt["target_date"]
        if target_date < last_target:
            raise AutonomousResearchError("Archive target moved backwards relative to the attempt ledger.")
        last_event = next(
            (event for event in reversed(state["events"]) if event["attempt_id"] == last_attempt["attempt_id"]),
            None,
        )
        same_target_attempts = sum(1 for item in attempts if item["target_date"] == target_date)
        event_by_attempt = {event["attempt_id"]: event for event in state["events"]}
        same_target_failed_attempts = sum(
            1 for item in attempts
            if item["target_date"] == target_date
            and event_by_attempt.get(item["attempt_id"], {}).get("outcome") == "FAILED"
        )
        if target_date == last_target:
            if last_event is None:
                return {
                    "status": "TARGET_ATTEMPT_IN_PROGRESS",
                    "eligible": False,
                    "target_date": target_date,
                    "archive_sha256": archive["archive_sha256"],
                    **status,
                }
            if same_target_failed_attempts >= MAX_ATTEMPTS_PER_TARGET:
                return {
                    "status": "TARGET_RETRY_LIMIT_REACHED",
                    "eligible": False,
                    "target_date": target_date,
                    "archive_sha256": archive["archive_sha256"],
                    **status,
                }
            if last_event["outcome"] == "FAILED":
                reason_code = last_event["reason_code"]
                if reason_code in MANUAL_UNLOCK_REQUIRED_FAILURE_REASON_CODES:
                    manual_recovery_unlock_at_utc = manual_recovery_unlock_timestamp(lock, last_event)
                    manual_recovery_confirmed_for_failure = manual_recovery_unlock_at_utc is not None
                if (
                    reason_code in RETRYABLE_FAILURE_REASON_CODES
                    and (
                        reason_code not in MANUAL_UNLOCK_REQUIRED_FAILURE_REASON_CODES
                        or manual_recovery_confirmed_for_failure
                    )
                ):
                    retry_limit = MAX_ATTEMPTS_PER_TARGET
                    if same_target_failed_attempts >= retry_limit:
                        return {
                            "status": "TARGET_RETRY_LIMIT_REACHED",
                            "eligible": False,
                            "target_date": target_date,
                            "archive_sha256": archive["archive_sha256"],
                            **status,
                        }
                    retryable = True
                    retry_number = same_target_attempts + 1
                else:
                    if reason_code == "PROVIDER_HTTP_REJECTION_NO_RETRY":
                        blocked_status = "PROVIDER_HTTP_REJECTION_NO_RETRY"
                    elif reason_code == "WORKFLOW_FAILED":
                        blocked_status = "WORKFLOW_FAILED_MANUAL_RECOVERY_REQUIRED"
                    else:
                        blocked_status = "PROVIDER_DELIVERY_UNCERTAIN_MANUAL_RECOVERY_REQUIRED"
                    return {
                        "status": blocked_status,
                        "eligible": False,
                        "target_date": target_date,
                        "archive_sha256": archive["archive_sha256"],
                        **status,
                    }
            elif (
                last_event["outcome"] == "NO_ELIGIBLE_CANDIDATE"
                and last_event["reason_code"] == "NO_FROZEN_DIRECTIONAL_CANDIDATE"
            ) or (
                last_event["outcome"] == "SUCCEEDED"
                and last_event["reason_code"] == "EVALUATION_COMPLETED"
            ):
                # A completed package may be followed by fresh discovery on the
                # same active target. A valid no-candidate pass is continuable,
                # but neither outcome increments the FAILED-attempt budget.
                retryable = last_event["outcome"] == "NO_ELIGIBLE_CANDIDATE"
                retry_number = same_target_attempts + 1
            else:
                return {
                    "status": "TARGET_ALREADY_COMPLETED",
                    "eligible": False,
                    "target_date": target_date,
                    "archive_sha256": archive["archive_sha256"],
                    **status,
                }
        else:
            completed_previous_target = next(
                (
                    item
                    for item in queue["completed"]
                    if item["target_date"] == last_target
                ),
                None,
            )
            if completed_previous_target is not None:
                if (
                    completed_previous_target["archive_sha256"] != last_attempt["archive_sha256"]
                    or completed_previous_target["archive_record_count"] != last_attempt["archive_record_count"]
                ):
                    raise AutonomousResearchError(
                        "Completed previous FIFO target does not match its archived attempt identity."
                    )
                # A terminal FIFO record already closed this exact target. Its
                # historical failures must not block preparation of the next head.
            else:
                if last_event is None:
                    return {
                        "status": "PREVIOUS_TARGET_INCOMPLETE",
                        "eligible": False,
                        "target_date": target_date,
                        "pending_target_date": last_target,
                        **status,
                    }
                if last_event["outcome"] == "FAILED":
                    previous_retry_is_ready = (
                        last_event["reason_code"] in RETRYABLE_FAILURE_REASON_CODES
                    )
                    if last_event["reason_code"] in MANUAL_UNLOCK_REQUIRED_FAILURE_REASON_CODES:
                        previous_retry_is_ready = manual_recovery_confirmed(lock, last_event)
                    previous_failed_count = sum(
                        1 for item in attempts
                        if item["target_date"] == last_target
                        and event_by_attempt.get(item["attempt_id"], {}).get("outcome") == "FAILED"
                    )
                    if previous_retry_is_ready and previous_failed_count >= MAX_ATTEMPTS_PER_TARGET:
                        blocking_status = "PREVIOUS_TARGET_RETRY_LIMIT_REACHED"
                    else:
                        blocking_status = (
                            "PREVIOUS_TARGET_RETRY_REQUIRED"
                            if previous_retry_is_ready
                            else "PREVIOUS_TARGET_MANUAL_RECOVERY_REQUIRED"
                        )
                    return {
                        "status": blocking_status,
                        "eligible": False,
                        "target_date": target_date,
                        "pending_target_date": last_target,
                        **status,
                    }
                if last_event["outcome"] not in {"NO_ELIGIBLE_CANDIDATE", "SUCCEEDED"}:
                    return {
                        "status": "PREVIOUS_TARGET_INCOMPLETE",
                        "eligible": False,
                        "target_date": target_date,
                        "pending_target_date": last_target,
                        **status,
                    }
                return {
                    "status": "PREVIOUS_TARGET_COMPLETION_RECORD_REQUIRED",
                    "eligible": False,
                    "target_date": target_date,
                    "pending_target_date": last_target,
                    **status,
                }

    try:
        plan = sealed.preflight(archive_path, ledger_path)
    except sealed.SealedEvaluationError as exc:
        reason = str(exc)
        lower = reason.lower()
        if (
            "fresh forward sealed validation interval" in lower
            or "fresh reverse sealed validation interval" in lower
            or "no sufficiently sized forward r60 validation interval" in lower
            or "no sufficiently sized reverse r60 validation interval" in lower
            or "adjacency-safe bidirectional partition" in lower
            or "r60 discovery history is shorter than the default minimum training window" in lower
            or "discovery history is shorter than the default minimum" in lower
        ):
            return {
                "status": "WAITING_FOR_FRESH_SEALED_WINDOWS",
                "eligible": False,
                "target_date": target_date,
                "archive_sha256": archive["archive_sha256"],
                **status,
            }
        raise AutonomousResearchError("Sealed preflight rejected the cycle: " + reason) from None

    if plan.get("target_date") != target_date or plan.get("archive_sha256") != archive["archive_sha256"]:
        raise AutonomousResearchError("Archive changed between autonomous preflight checks.")
    if (
        plan.get("target_numbers_exposed") is not False
        or plan.get("target_in_discovery") is not False
        or plan.get("target_in_validation") is not False
    ):
        raise AutonomousResearchError("Target isolation metadata did not pass.")
    regime = plan.get("regimes", {}).get("R60", {})
    forward_count = len(plan.get("forward_validation_dates", []))
    reverse_count = len(plan.get("reverse_validation_dates", []))
    discovery_count = regime.get("discovery_count")
    if (
        regime.get("status") != "READY"
        or not isinstance(discovery_count, int)
        or discovery_count < sealed.DEFAULT_MIN_TRAIN_DRAWS
        or forward_count < sealed.MIN_VALIDATION_DRAWS
        or reverse_count < sealed.MIN_VALIDATION_DRAWS
        or set(plan["forward_validation_dates"]).intersection(plan["reverse_validation_dates"])
    ):
        raise AutonomousResearchError("Directional sealed preflight did not satisfy its fixed minimums.")

    return {
        "status": "READY",
        "eligible": True,
        "target_date": target_date,
        # Attempts and evaluation claims bind to the canonical immutable
        # active snapshot, not to the byte representation of the source archive.
        "archive_sha256": active_item["archive_sha256"],
        "archive_record_count": active_item["archive_record_count"],
        "pre_target_draw_count": plan["pre_target_draw_count"],
        "discovery_count": discovery_count,
        "forward_validation_count": forward_count,
        "reverse_validation_count": reverse_count,
        "partition_version": plan.get("partition_version"),
        "target_numbers_exposed": False,
        "forward_reverse_holdouts_disjoint": True,
        "previously_consumed_holdout_dates_reused": False,
        "retryable": retryable,
        "retry_number": retry_number,
        "manual_recovery_confirmed": manual_recovery_confirmed_for_failure,
        "manual_recovery_unlock_at_utc": manual_recovery_unlock_at_utc,
        "pending_claim_resume_available": plan.get("pending_claim_resume_available") is True,
        "pending_claim_id": plan.get("pending_claim_id"),
    }


def claim_attempt(
    state: dict[str, Any],
    plan: dict[str, Any],
    *,
    github_run_id: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_state(state)
    status = authorization_status(state, now=now)
    if not status["authorized"]:
        raise AutonomousResearchError("Autonomous authorization expired or is disabled.")
    if not isinstance(plan, dict) or plan.get("eligible") is not True or plan.get("status") != "READY":
        raise AutonomousResearchError("Only a ready sealed cycle can be claimed.")
    target_date = _canonical_date(plan.get("target_date"), "plan.target_date")
    archive_sha = plan.get("archive_sha256")
    if not _is_sha256(archive_sha):
        raise AutonomousResearchError("Plan archive hash is invalid.")
    if type(plan.get("archive_record_count")) is not int or plan["archive_record_count"] < 1:
        raise AutonomousResearchError("Plan archive record count is invalid.")
    attempt_number = 1
    workflow_failure_to_recover: tuple[dict[str, Any], dict[str, Any]] | None = None
    event_by_attempt = {event["attempt_id"]: event for event in state["events"]}
    queue = target_queue.validate_queue(state["target_queue"])
    active_item = target_queue.active_item(queue)
    if active_item is None or (
        target_date != active_item["target_date"]
        or archive_sha != active_item["archive_sha256"]
        or plan["archive_record_count"] != active_item["archive_record_count"]
    ):
        raise AutonomousResearchError("Attempt does not match the immutable active-target snapshot.")
    if state["attempts"]:
        last_attempt = state["attempts"][-1]
        last_target = last_attempt["target_date"]
        if target_date < last_target:
            raise AutonomousResearchError("Target date is older than the current active target.")
        last_event = next(
            (event for event in reversed(state["events"]) if event["attempt_id"] == last_attempt["attempt_id"]),
            None,
        )
        if target_date == last_target:
            previous_count = sum(1 for item in state["attempts"] if item["target_date"] == target_date)
            retryable_no_candidate = (
                last_event is not None
                and (
                    (
                        last_event["outcome"] == "NO_ELIGIBLE_CANDIDATE"
                        and last_event["reason_code"] == "NO_FROZEN_DIRECTIONAL_CANDIDATE"
                    )
                    or (
                        last_event["outcome"] == "SUCCEEDED"
                        and last_event["reason_code"] == "EVALUATION_COMPLETED"
                    )
                )
            )
            retryable_failure = (
                last_event is not None
                and last_event["outcome"] == "FAILED"
                and last_event["reason_code"] in RETRYABLE_FAILURE_REASON_CODES
            )
            if not (retryable_no_candidate or retryable_failure):
                raise AutonomousResearchError(
                    "A target retry requires a retryable failure or a recorded no-candidate outcome."
                )
            if last_event["reason_code"] in MANUAL_UNLOCK_REQUIRED_FAILURE_REASON_CODES:
                if plan.get("manual_recovery_confirmed") is not True:
                    raise AutonomousResearchError(
                        "Post-provider workflow failure or uncertain delivery requires a manual unlock after the failed attempt."
                    )
                if last_event["reason_code"] == "WORKFLOW_FAILED":
                    unlock_at = plan.get("manual_recovery_unlock_at_utc")
                    if not isinstance(unlock_at, str):
                        raise AutonomousResearchError("A WORKFLOW_FAILED retry is missing its manual unlock provenance.")
                    if _timestamp(unlock_at, "plan.manual_recovery_unlock_at_utc") <= _timestamp(
                        last_event["recorded_at_utc"], "failed_event.recorded_at_utc"
                    ):
                        raise AutonomousResearchError("The manual unlock predates the WORKFLOW_FAILED event.")
                    workflow_failure_to_recover = (last_attempt, last_event)
            previous_failed_count = sum(
                1 for item in state["attempts"]
                if item["target_date"] == target_date
                and event_by_attempt.get(item["attempt_id"], {}).get("outcome") == "FAILED"
            )
            if previous_failed_count >= MAX_ATTEMPTS_PER_TARGET:
                raise AutonomousResearchError("The ten-failed-attempt target retry limit is exhausted.")
            attempt_number = previous_count + 1
        else:
            completed_previous_target = next(
                (
                    item
                    for item in queue["completed"]
                    if item["target_date"] == last_target
                ),
                None,
            )
            if completed_previous_target is not None:
                if (
                    completed_previous_target["archive_sha256"] != last_attempt["archive_sha256"]
                    or completed_previous_target["archive_record_count"] != last_attempt["archive_record_count"]
                ):
                    raise AutonomousResearchError(
                        "Completed previous FIFO target does not match its archived attempt identity."
                    )
                # A validated terminal FIFO record closes this exact prior target.
                # Historical FAILED attempts for it must not block the next FIFO head.
            else:
                if last_event is None:
                    raise AutonomousResearchError("The previous target is incomplete; new target activation is blocked.")
                if last_event["outcome"] == "FAILED":
                    raise AutonomousResearchError("The previous target must be resolved before a new target is activated.")
    if github_run_id is not None and (not github_run_id.isdigit() or len(github_run_id) > 32):
        raise AutonomousResearchError("GitHub run identifier is invalid.")

    attempt_id = hashlib.sha256(
        ("GENERAL-AUTO-RESEARCH-V2|" + target_date + "|" + archive_sha + "|" + str(attempt_number)).encode("utf-8")
    ).hexdigest()
    if any(item["attempt_id"] == attempt_id for item in state["attempts"]):
        raise AutonomousResearchError("This autonomous attempt was already claimed.")
    claimed_at = _now_utc(now).isoformat()
    if workflow_failure_to_recover is not None:
        if github_run_id is None:
            raise AutonomousResearchError("A workflow-failure retry requires a GitHub run identifier.")
        unlock_at = _timestamp(
            plan["manual_recovery_unlock_at_utc"],
            "plan.manual_recovery_unlock_at_utc",
        )
        if unlock_at > _timestamp(claimed_at, "attempt.claimed_at_utc"):
            raise AutonomousResearchError("Manual unlock provenance postdates the retry claim.")
    attempt = {
        "attempt_id": attempt_id,
        "target_date": target_date,
        "archive_sha256": archive_sha,
        "archive_record_count": plan["archive_record_count"],
        "claimed_at_utc": claimed_at,
        "github_run_id": github_run_id,
    }
    state["attempts"].append(attempt)
    if workflow_failure_to_recover is not None:
        prior_attempt, prior_event = workflow_failure_to_recover
        retry_entry = {
            "schema_version": 1,
            "target_date": target_date,
            "archive_sha256": archive_sha,
            "archive_record_count": plan["archive_record_count"],
            "prior_attempt_id": prior_attempt["attempt_id"],
            "prior_failure_reason_code": "WORKFLOW_FAILED",
            "prior_failure_at_utc": prior_event["recorded_at_utc"],
            "manual_unlocked_at_utc": plan["manual_recovery_unlock_at_utc"],
            "retry_attempt_id": attempt_id,
            "retry_github_run_id": github_run_id,
            "claimed_at_utc": claimed_at,
            "consumed": True,
        }
        retry_entry["retry_id"] = hashlib.sha256(
            json.dumps(retry_entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest()
        state["workflow_retry_history"].append(retry_entry)
    validate_state(state)
    return attempt


def record_target_evaluation(
    state: dict[str, Any],
    report: dict[str, Any],
) -> dict[str, Any]:
    """Append only the number-free aggregate result of a local hidden-target score."""
    validate_state(state)
    if not isinstance(report, dict) or report.get("status") != "LOCAL_TARGET_EVALUATION_COMPLETED":
        raise AutonomousResearchError("Only a completed local target-evaluation report may be recorded.")
    required_safety_flags = {
        "target_numbers_exposed": False,
        "target_numbers_reported": False,
        "candidate_numbers_exposed": False,
        "candidate_numbers_reported": False,
        "predicted_number_values_reported": False,
        "per_number_matches_reported": False,
        "target_values_sent_to_model": False,
        "target_score_sent_to_model": False,
        "target_score_saved_to_research_memory": False,
        "reverse_spec_used_for_prediction": False,
        "live_hosted_model_request_made": False,
        "telegram_request_made": False,
        "method_registry_modified": False,
        "prior_draws_used_only_as_conditioning_context": True,
        "prior_holdouts_reused_as_evaluation_targets": False,
        "prior_draws_reused_for_discovery_or_tuning": False,
    }
    if any(report.get(key) is not expected for key, expected in required_safety_flags.items()):
        raise AutonomousResearchError("Local target-evaluation report failed its no-leakage safety flags.")
    entry = {
        key: report.get(key)
        for key in target_evaluation_ledger.ENTRY_KEYS
    }
    try:
        checked_entry = target_evaluation_ledger.validate_entry(entry)
    except target_evaluation_ledger.TargetEvaluationLedgerError as exc:
        raise AutonomousResearchError(
            "Local target-evaluation report failed its private result schema: " + str(exc)
        ) from None
    attempt = next(
        (item for item in state["attempts"] if item["attempt_id"] == checked_entry["attempt_id"]),
        None,
    )
    if attempt is None or (
        attempt["target_date"] != checked_entry["target_date"]
        or attempt["archive_sha256"] != checked_entry["archive_sha256"]
        or attempt["archive_record_count"] != checked_entry["archive_record_count"]
    ):
        raise AutonomousResearchError("Local target result does not match its claimed attempt and archive.")
    if any(event["attempt_id"] == checked_entry["attempt_id"] for event in state["events"]):
        raise AutonomousResearchError("Local target result must be recorded before the attempt's terminal outcome.")

    existing = next(
        (item for item in state["target_evaluations"] if item["claim_id"] == checked_entry["claim_id"]),
        None,
    )
    if existing is not None:
        # A retry may have a new attempt id, but the exact EVALUATED claim and
        # local score remain immutable. Accept only identical result metadata.
        comparable_keys = set(target_evaluation_ledger.ENTRY_KEYS) - {
            "attempt_id", "completed_at_utc"
        }
        if any(existing[key] != checked_entry[key] for key in comparable_keys):
            raise AutonomousResearchError("A recorded local target claim has conflicting result metadata.")
        return validate_state(state)
    active = target_queue.active_item(state["target_queue"])
    if active is None or (
        active["target_date"] != checked_entry["target_date"]
        or active["archive_sha256"] != checked_entry["archive_sha256"]
        or active["archive_record_count"] != checked_entry["archive_record_count"]
    ):
        raise AutonomousResearchError("Local target result does not match the immutable active target.")
    updated = copy.deepcopy(state)
    updated["target_evaluations"].append(checked_entry)
    return validate_state(updated)


def _require_evaluated_target(
    ledger_path: str | Path,
    *,
    target_date: str,
    archive_sha256: str,
    archive_record_count: int,
) -> None:
    """Require an exact persisted EVALUATED claim before a FIFO target can be consumed."""
    try:
        ledger = sealed.load_ledger(ledger_path)
    except sealed.SealedEvaluationError as exc:
        raise AutonomousResearchError(
            "Evaluation ledger could not prove target consumption: " + str(exc)
        ) from None
    matches = [
        entry for entry in ledger["entries"]
        if entry.get("target_date") == target_date
        and entry.get("archive_sha256") == archive_sha256
        and entry.get("archive_record_count") == archive_record_count
    ]
    evaluated = [entry for entry in matches if entry.get("state") == "EVALUATED"]
    if not evaluated:
        raise AutonomousResearchError(
            "A successful target requires a matching sealed claim persisted as EVALUATED before it can leave the FIFO."
        )


def record_outcome(
    state: dict[str, Any],
    attempt_id: str,
    *,
    outcome: str,
    reason_code: str,
    evaluation_ledger_path: str | Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_state(state)
    if outcome not in ALLOWED_OUTCOMES or reason_code not in ALLOWED_REASON_CODES:
        raise AutonomousResearchError("Attempt outcome or reason code is not allowed.")
    attempt = next(
        (item for item in state["attempts"] if item["attempt_id"] == attempt_id),
        None,
    )
    if attempt is None:
        raise AutonomousResearchError("Cannot record an outcome for an unknown attempt.")
    if any(item["attempt_id"] == attempt_id for item in state["events"]):
        raise AutonomousResearchError("Attempt already has a terminal outcome.")

    if outcome == "SUCCEEDED":
        if reason_code != "EVALUATION_COMPLETED" or evaluation_ledger_path is None:
            raise AutonomousResearchError(
                "Successful completion requires an exact persisted EVALUATED sealed-ledger entry."
            )
        _require_evaluated_target(
            evaluation_ledger_path,
            target_date=attempt["target_date"],
            archive_sha256=attempt["archive_sha256"],
            archive_record_count=attempt["archive_record_count"],
        )
    elif outcome == "NO_ELIGIBLE_CANDIDATE" and reason_code != "NO_FROZEN_DIRECTIONAL_CANDIDATE":
        raise AutonomousResearchError(
            "A no-candidate outcome must use NO_FROZEN_DIRECTIONAL_CANDIDATE."
        )

    state["events"].append({
        "attempt_id": attempt_id,
        "outcome": outcome,
        "recorded_at_utc": _now_utc(now).isoformat(),
        "reason_code": reason_code,
    })
    # EVALUATED completes this frozen-formula test, not the target's entire
    # research lifetime. The active target remains available for new discovery
    # packages until a newer draw is observed.
    return validate_state(state)



def retire_exhausted_target(
    state: dict[str, Any],
    *,
    target_date: str,
    archive_sha256: str,
    archive_record_count: int,
    expected_attempt_count: int | None = None,
    expected_failed_attempt_count: int | None = None,
    expected_next_target_date: str | None = None,
    expected_next_archive_sha256: str | None = None,
    expected_next_archive_record_count: int | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Manually retire an active target only after exactly ten failed attempts."""
    updated = copy.deepcopy(state)
    validate_state(updated)
    target = _canonical_date(target_date, "exhausted_target.target_date")
    expected_failures = (
        expected_failed_attempt_count
        if expected_failed_attempt_count is not None
        else expected_attempt_count
    )
    if expected_failures != MAX_ATTEMPTS_PER_TARGET:
        raise AutonomousResearchError("Exhaustion retirement requires exactly ten failed attempts.")
    if not _is_sha256(archive_sha256):
        raise AutonomousResearchError("Exhausted-target archive identity is invalid.")
    if type(archive_record_count) is not int or archive_record_count < 1:
        raise AutonomousResearchError("Exhausted-target archive record count is invalid.")

    target_attempts = [item for item in updated["attempts"] if item["target_date"] == target]
    if any(
        item["archive_sha256"] != archive_sha256
        or item["archive_record_count"] != archive_record_count
        for item in target_attempts
    ):
        raise AutonomousResearchError("Target attempts do not share the approved immutable archive identity.")
    events_by_attempt = {event["attempt_id"]: event for event in updated["events"]}
    if any(events_by_attempt.get(item["attempt_id"]) is None for item in target_attempts):
        raise AutonomousResearchError(
            "Every target attempt must be terminal; exhaustion requires exactly ten terminal FAILED attempts."
        )
    failed_count = sum(
        1 for item in target_attempts
        if events_by_attempt[item["attempt_id"]]["outcome"] == "FAILED"
    )
    if failed_count != MAX_ATTEMPTS_PER_TARGET:
        raise AutonomousResearchError("Exhaustion retirement requires exactly ten failed attempts.")
    if not target_attempts or events_by_attempt[target_attempts[-1]["attempt_id"]]["outcome"] != "FAILED":
        raise AutonomousResearchError("The exhausted target must end with its tenth failed attempt.")

    active = target_queue.active_item(updated["target_queue"])
    if active is None or (
        active["target_date"] != target
        or active["archive_sha256"] != archive_sha256
        or active["archive_record_count"] != archive_record_count
    ):
        raise AutonomousResearchError("Exhausted target does not match the immutable active target.")

    expected_successor = None
    if updated["target_queue"]["schema_version"] == 1:
        items = updated["target_queue"]["items"]
        expected_successor = items[1] if len(items) > 1 else None
    else:
        latest_item = target_queue.latest_snapshot_item(updated["target_queue"])
        if latest_item is not None and latest_item["target_date"] > target:
            expected_successor = latest_item
    supplied_successor = (
        expected_next_target_date,
        expected_next_archive_sha256,
        expected_next_archive_record_count,
    )
    if any(value is not None for value in supplied_successor):
        if expected_successor is None or supplied_successor != (
            expected_successor["target_date"],
            expected_successor["archive_sha256"],
            expected_successor["archive_record_count"],
        ):
            raise AutonomousResearchError("The manually confirmed next target does not match the staged latest snapshot.")
    try:
        updated["target_queue"] = target_queue.retire_exhausted_front(
            updated["target_queue"],
            target_date=target,
            archive_sha256=archive_sha256,
            archive_record_count=archive_record_count,
            failed_attempt_count=failed_count,
            now=now,
        )
    except target_queue.TargetQueueError as exc:
        raise AutonomousResearchError("Exhausted target could not be closed: " + str(exc)) from None

    validate_state(updated)
    latest = target_queue.latest_snapshot_item(updated["target_queue"])
    latest_available_date = (
        latest["target_date"] if latest is not None and latest["target_date"] > target else None
    )
    report = {
        "status": "EXHAUSTED_TARGET_RETIRED",
        "target_date": target,
        "outcome": "EXHAUSTED_RETRY_BUDGET",
        "attempts_preserved_count": len(target_attempts),
        "failed_attempt_count": failed_count,
        "latest_available_date": latest_available_date,
        "next_target_date": (
            expected_successor["target_date"] if expected_successor is not None else None
        ),
        "active_target_released": True,
        "target_values_rendered": False,
    }
    if expected_next_target_date is not None:
        report["legacy_expected_next_target_date"] = expected_next_target_date
    return updated, report


def _write_github_output(path: str | None, values: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            if value is None:
                continue
            if isinstance(value, bool):
                rendered = "true" if value else "false"
            else:
                rendered = str(value)
            if "\n" in rendered or "\r" in rendered:
                raise AutonomousResearchError("GitHub output values must be single-line.")
            stream.write(key + "=" + rendered + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    activate = sub.add_parser("activate")
    activate.add_argument("--state", type=Path, default=Path("general_autonomous_research_state.json"))
    activate.add_argument("--global-lock", type=Path, default=Path("general_nvidia_lock.json"))
    activate.add_argument("--confirmation", required=True)
    activate.add_argument("--free-usage-verified", choices=("true", "false"), required=True)
    activate.add_argument("--days", type=int, choices=(1, 7, 30), required=True)

    pause = sub.add_parser("pause")
    pause.add_argument("--state", type=Path, default=Path("general_autonomous_research_state.json"))

    status = sub.add_parser("status")
    status.add_argument("--state", type=Path, default=Path("general_autonomous_research_state.json"))
    status.add_argument("--global-lock", type=Path, default=Path("general_nvidia_lock.json"))
    status.add_argument("--github-output")

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--state", type=Path, default=Path("general_autonomous_research_state.json"))
    prepare.add_argument("--archive", type=Path, default=Path("super_loto_archive.json"))
    prepare.add_argument("--ledger", type=Path, default=Path("general_evaluation_ledger.json"))
    prepare.add_argument("--global-lock", type=Path, default=Path("general_nvidia_lock.json"))
    prepare.add_argument("--github-output")

    claim = sub.add_parser("claim")
    claim.add_argument("--state", type=Path, default=Path("general_autonomous_research_state.json"))
    claim.add_argument("--archive", type=Path, default=Path("super_loto_archive.json"))
    claim.add_argument("--ledger", type=Path, default=Path("general_evaluation_ledger.json"))
    claim.add_argument("--global-lock", type=Path, default=Path("general_nvidia_lock.json"))
    claim.add_argument("--run-id", default="")
    claim.add_argument("--github-output")

    finish = sub.add_parser("finish")
    finish.add_argument("--state", type=Path, default=Path("general_autonomous_research_state.json"))
    finish.add_argument("--attempt-id", required=True)
    finish.add_argument("--outcome", choices=sorted(ALLOWED_OUTCOMES), required=True)
    finish.add_argument("--reason-code", choices=sorted(ALLOWED_REASON_CODES), required=True)
    finish.add_argument("--ledger", type=Path, default=Path("general_evaluation_ledger.json"))

    queue_capture = sub.add_parser("queue-capture")
    queue_capture.add_argument("--state", type=Path, default=Path("general_autonomous_research_state.json"))
    queue_capture.add_argument("--archive", type=Path, default=Path("super_loto_archive.json"))
    queue_capture.add_argument("--github-output")

    queue_materialize = sub.add_parser("queue-materialize")
    queue_materialize.add_argument("--state", type=Path, default=Path("general_autonomous_research_state.json"))
    queue_materialize.add_argument("--output", type=Path, default=Path("super_loto_archive.json"))

    retire_exhausted = sub.add_parser("retire-exhausted-target")
    retire_exhausted.add_argument("--state", type=Path, default=Path("general_autonomous_research_state.json"))
    retire_exhausted.add_argument("--target-date", required=True)
    retire_exhausted.add_argument("--archive-sha256", required=True)
    retire_exhausted.add_argument("--archive-record-count", type=int, required=True)
    retire_exhausted.add_argument(
        "--expected-failed-attempt-count", "--expected-attempt-count",
        dest="expected_failed_attempt_count", type=int, required=True,
    )
    retire_exhausted.add_argument("--expected-next-target-date")
    retire_exhausted.add_argument("--expected-next-archive-sha256")
    retire_exhausted.add_argument("--expected-next-archive-record-count", type=int)

    args = parser.parse_args(argv)
    try:
        state = read_state(args.state)
        if args.command == "activate":
            lock = read_global_lock(args.global_lock)
            state = activate_state(
                state,
                confirmation=args.confirmation,
                free_usage_verified=(args.free_usage_verified == "true"),
                authorization_days=args.days,
                global_lock=lock,
            )
            write_state(args.state, state)
            result = authorization_status(state)
            result["status"] = "AUTONOMOUS_RESEARCH_AUTHORIZED"
        elif args.command == "pause":
            state = pause_state(state)
            write_state(args.state, state)
            result = {"status": "AUTONOMOUS_RESEARCH_PAUSED", **authorization_status(state)}
        elif args.command == "status":
            result = authorization_status(state)
            try:
                lock = read_global_lock(args.global_lock)
                result["global_lock_clear"] = not lock["locked"]
            except AutonomousResearchError:
                result["global_lock_clear"] = False
            result["status"] = "ACTIVE" if result["authorized"] and result["global_lock_clear"] else "PAUSED_OR_BLOCKED"
            _write_github_output(args.github_output, {
                "authorized": result["authorized"],
                "lock_clear": result["global_lock_clear"],
            })
        elif args.command == "queue-capture":
            updated_queue, queue_summary = target_queue.capture_new_targets(
                state["target_queue"],
                archive_path=args.archive,
                attempts=state["attempts"],
                events=state["events"],
            )
            state["target_queue"] = updated_queue
            if queue_summary["changed"]:
                write_state(args.state, state)
            result = {"status": "FIFO_CAPTURE_COMPLETE", **queue_summary}
            _write_github_output(args.github_output, {
                "changed": queue_summary["changed"],
                "has_pending": queue_summary["has_pending"],
                "pending_target_count": queue_summary["pending_target_count"],
                "front_target_date": queue_summary["front_target_date"],
            })
        elif args.command == "queue-materialize":
            result = target_queue.materialize_front(
                state["target_queue"], args.output
            )
        elif args.command == "retire-exhausted-target":
            state, result = retire_exhausted_target(
                state,
                target_date=args.target_date,
                archive_sha256=args.archive_sha256,
                archive_record_count=args.archive_record_count,
                expected_failed_attempt_count=args.expected_failed_attempt_count,
                expected_next_target_date=args.expected_next_target_date,
                expected_next_archive_sha256=args.expected_next_archive_sha256,
                expected_next_archive_record_count=args.expected_next_archive_record_count,
            )
            write_state(args.state, state)
        elif args.command == "prepare":
            result = prepare_cycle(
                state,
                archive_path=args.archive,
                ledger_path=args.ledger,
                global_lock_path=args.global_lock,
            )
            _write_github_output(args.github_output, {
                "eligible": result.get("eligible") is True,
                "status": result.get("status"),
                "target_date": result.get("target_date"),
                "archive_sha256": result.get("archive_sha256"),
                "archive_record_count": result.get("archive_record_count"),
                "forward_validation_count": result.get("forward_validation_count"),
                "reverse_validation_count": result.get("reverse_validation_count"),
                "discovery_count": result.get("discovery_count"),
                "retryable": result.get("retryable") is True,
                "retry_number": result.get("retry_number"),
            })
        elif args.command == "claim":
            plan = prepare_cycle(
                state,
                archive_path=args.archive,
                ledger_path=args.ledger,
                global_lock_path=args.global_lock,
            )
            if plan.get("eligible") is not True:
                raise AutonomousResearchError("Autonomous cycle is no longer eligible; no attempt was claimed.")
            attempt = claim_attempt(
                state,
                plan,
                github_run_id=args.run_id or None,
            )
            write_state(args.state, state)
            result = {"status": "ATTEMPT_CLAIMED_BEFORE_PROVIDER_CALL", **attempt}
            _write_github_output(args.github_output, {"attempt_id": attempt["attempt_id"]})
        else:
            state = record_outcome(
                state,
                args.attempt_id,
                outcome=args.outcome,
                reason_code=args.reason_code,
                evaluation_ledger_path=args.ledger,
            )
            write_state(args.state, state)
            result = {"status": "ATTEMPT_OUTCOME_RECORDED", "attempt_id": args.attempt_id, "outcome": args.outcome}
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except Exception as exc:
        print("AUTONOMOUS RESEARCH BLOCKED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
