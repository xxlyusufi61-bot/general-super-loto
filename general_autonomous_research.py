"""Fail-closed scheduler state for one active FIFO target and its recorded attempts.

This module never calls NVIDIA or Telegram. It only manages an expiring, explicit
free-only authorization, verifies the shared lock and sealed-evaluation preflight,
and records number-free attempt metadata before the existing research workflow runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import general_sealed_evaluation as sealed
from general_nvidia_guard import validate_lock_state
import general_target_queue as target_queue

VERSION = "0.1.0"
PROJECT = "GENERAL Super Loto"
RESEARCH_SCOPE_START = date(2020, 8, 2)
AUTO_CONFIRMATION_PHRASE = "I_AUTHORIZE_AUTONOMOUS_FREE_ONLY_NVIDIA_RESEARCH_NO_BILLING"
MAX_AUTHORIZATION_DAYS = 30
MAX_ATTEMPTS_PER_TARGET = 3
RETRYABLE_FAILURE_REASON_CODES = {
    "NO_PROVIDER_REQUEST_SENT",
    "PROVIDER_DELIVERY_UNCERTAIN",
}
ALLOWED_OUTCOMES = {"SUCCEEDED", "FAILED", "NO_ELIGIBLE_CANDIDATE"}
ALLOWED_REASON_CODES = {
    "EVALUATION_COMPLETED",
    "WORKFLOW_FAILED",
    "NO_FROZEN_DIRECTIONAL_CANDIDATE",
    "NO_PROVIDER_REQUEST_SENT",
    "PROVIDER_DELIVERY_UNCERTAIN",
    "PROVIDER_HTTP_REJECTION_NO_RETRY",
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
        "schema_version": 2,
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
    }


def validate_state(value: Any) -> dict[str, Any]:
    legacy_keys = {"schema_version", "project", "authorization", "attempts", "events"}
    if isinstance(value, dict) and set(value) == legacy_keys and value.get("schema_version") == 1:
        # Upgrade the legacy scheduler document in memory only. The caller persists
        # it only through the ordinary, separately authorized state-store workflow.
        value["schema_version"] = 2
        value["target_queue"] = target_queue.initial_queue()
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "project", "authorization", "attempts", "events", "target_queue"
    }:
        raise AutonomousResearchError("Autonomous scheduler state has an invalid schema.")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 2:
        raise AutonomousResearchError("Unsupported autonomous scheduler schema version.")
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
    target_counts: dict[str, int] = {}
    for target in attempt_dates:
        target_counts[target] = target_counts.get(target, 0) + 1
    if any(count > MAX_ATTEMPTS_PER_TARGET for count in target_counts.values()):
        raise AutonomousResearchError("A target exceeded the existing three-attempt limit.")
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

    attempts_by_target: dict[str, list[dict[str, Any]]] = {}
    for item in attempts:
        attempts_by_target.setdefault(item["target_date"], []).append(item)
    event_by_attempt = {event["attempt_id"]: event for event in events}
    for target, target_attempts in attempts_by_target.items():
        if len(target_attempts) > 1:
            for prior_attempt in target_attempts[:-1]:
                prior_event = event_by_attempt.get(prior_attempt["attempt_id"])
                if (
                    prior_event is None
                    or prior_event["outcome"] != "FAILED"
                    or prior_event["reason_code"] not in RETRYABLE_FAILURE_REASON_CODES
                ):
                    raise AutonomousResearchError(
                        "Repeated target attempts require a recorded retryable failure; uncertain provider delivery remains blocked."
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


def manual_recovery_confirmed(lock_state: dict[str, Any], failed_event: dict[str, Any]) -> bool:
    """Require a recorded manual unlock strictly after an uncertain provider failure."""
    if failed_event.get("reason_code") != "PROVIDER_DELIVERY_UNCERTAIN":
        return False
    unlocked_at = lock_state.get("last_unlocked_at")
    if not isinstance(unlocked_at, str):
        return False
    try:
        return _timestamp(unlocked_at, "global_lock.last_unlocked_at") > _timestamp(
            failed_event.get("recorded_at_utc"),
            "failed_event.recorded_at_utc",
        )
    except AutonomousResearchError:
        return False


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
    if queue["items"]:
        front = queue["items"][0]
        if (
            target_date != front["target_date"]
            or archive["archive_sha256"] != front["archive_sha256"]
            or archive["record_count"] != front["archive_record_count"]
        ):
            raise AutonomousResearchError(
                "Prepared archive is not the immutable FIFO head snapshot."
            )
    attempts = state["attempts"]
    retryable = False
    retry_number = 1
    recovered_after_uncertain_delivery = False
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
        if target_date == last_target:
            if last_event is None:
                return {
                    "status": "TARGET_ATTEMPT_IN_PROGRESS",
                    "eligible": False,
                    "target_date": target_date,
                    "archive_sha256": archive["archive_sha256"],
                    **status,
                }
            if last_event["outcome"] == "FAILED":
                reason_code = last_event["reason_code"]
                if reason_code == "PROVIDER_DELIVERY_UNCERTAIN":
                    recovered_after_uncertain_delivery = manual_recovery_confirmed(lock, last_event)
                if (
                    reason_code in RETRYABLE_FAILURE_REASON_CODES
                    and (reason_code != "PROVIDER_DELIVERY_UNCERTAIN" or recovered_after_uncertain_delivery)
                ):
                    if same_target_attempts >= MAX_ATTEMPTS_PER_TARGET:
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
                        blocked_status = "WORKFLOW_FAILED_NO_AUTOMATIC_RETRY"
                    else:
                        blocked_status = "PROVIDER_DELIVERY_UNCERTAIN_MANUAL_RECOVERY_REQUIRED"
                    return {
                        "status": blocked_status,
                        "eligible": False,
                        "target_date": target_date,
                        "archive_sha256": archive["archive_sha256"],
                        **status,
                    }
            else:
                return {
                    "status": "TARGET_ALREADY_COMPLETED",
                    "eligible": False,
                    "target_date": target_date,
                    "archive_sha256": archive["archive_sha256"],
                    **status,
                }
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
                    last_event["reason_code"] == "NO_PROVIDER_REQUEST_SENT"
                )
                if last_event["reason_code"] == "PROVIDER_DELIVERY_UNCERTAIN":
                    previous_retry_is_ready = manual_recovery_confirmed(lock, last_event)
                previous_attempt_count = sum(
                    1 for item in attempts if item["target_date"] == last_target
                )
                if previous_retry_is_ready and previous_attempt_count >= MAX_ATTEMPTS_PER_TARGET:
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
        "archive_sha256": archive["archive_sha256"],
        "archive_record_count": archive["record_count"],
        "pre_target_draw_count": plan["pre_target_draw_count"],
        "discovery_count": discovery_count,
        "forward_validation_count": forward_count,
        "reverse_validation_count": reverse_count,
        "target_numbers_exposed": False,
        "forward_reverse_holdouts_disjoint": True,
        "previously_consumed_holdout_dates_reused": False,
        "retryable": retryable,
        "retry_number": retry_number,
        "manual_recovery_confirmed": recovered_after_uncertain_delivery,
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
    queue = target_queue.validate_queue(state["target_queue"])
    if queue["items"]:
        front = queue["items"][0]
        if (
            target_date != front["target_date"]
            or archive_sha != front["archive_sha256"]
            or plan["archive_record_count"] != front["archive_record_count"]
        ):
            raise AutonomousResearchError("Attempt does not match the immutable FIFO head.")
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
            if (
                last_event is None
                or last_event["outcome"] != "FAILED"
                or last_event["reason_code"] not in RETRYABLE_FAILURE_REASON_CODES
            ):
                raise AutonomousResearchError(
                    "A target retry requires a recorded retryable failure."
                )
            if (
                last_event["reason_code"] == "PROVIDER_DELIVERY_UNCERTAIN"
                and plan.get("manual_recovery_confirmed") is not True
            ):
                raise AutonomousResearchError(
                    "Uncertain provider delivery requires a manual unlock after the failed attempt."
                )
            if previous_count >= MAX_ATTEMPTS_PER_TARGET:
                raise AutonomousResearchError("The existing three-attempt target limit is exhausted.")
            attempt_number = previous_count + 1
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
    attempt = {
        "attempt_id": attempt_id,
        "target_date": target_date,
        "archive_sha256": archive_sha,
        "archive_record_count": plan["archive_record_count"],
        "claimed_at_utc": _now_utc(now).isoformat(),
        "github_run_id": github_run_id,
    }
    state["attempts"].append(attempt)
    validate_state(state)
    return attempt


def record_outcome(
    state: dict[str, Any],
    attempt_id: str,
    *,
    outcome: str,
    reason_code: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    validate_state(state)
    if outcome not in ALLOWED_OUTCOMES or reason_code not in ALLOWED_REASON_CODES:
        raise AutonomousResearchError("Attempt outcome or reason code is not allowed.")
    if not any(item["attempt_id"] == attempt_id for item in state["attempts"]):
        raise AutonomousResearchError("Cannot record an outcome for an unknown attempt.")
    if any(item["attempt_id"] == attempt_id for item in state["events"]):
        raise AutonomousResearchError("Attempt already has a terminal outcome.")
    state["events"].append({
        "attempt_id": attempt_id,
        "outcome": outcome,
        "recorded_at_utc": _now_utc(now).isoformat(),
        "reason_code": reason_code,
    })
    if outcome in {"SUCCEEDED", "NO_ELIGIBLE_CANDIDATE"} and state["target_queue"]["items"]:
        attempt = next(item for item in state["attempts"] if item["attempt_id"] == attempt_id)
        try:
            state["target_queue"] = target_queue.complete_front(
                state["target_queue"],
                target_date=attempt["target_date"],
                archive_sha256=attempt["archive_sha256"],
                archive_record_count=attempt["archive_record_count"],
                outcome=outcome,
                now=now,
            )
        except target_queue.TargetQueueError as exc:
            raise AutonomousResearchError(
                "Terminal outcome cannot release the FIFO head: " + str(exc)
            ) from None
    return validate_state(state)


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

    queue_capture = sub.add_parser("queue-capture")
    queue_capture.add_argument("--state", type=Path, default=Path("general_autonomous_research_state.json"))
    queue_capture.add_argument("--archive", type=Path, default=Path("super_loto_archive.json"))
    queue_capture.add_argument("--github-output")

    queue_materialize = sub.add_parser("queue-materialize")
    queue_materialize.add_argument("--state", type=Path, default=Path("general_autonomous_research_state.json"))
    queue_materialize.add_argument("--output", type=Path, default=Path("super_loto_archive.json"))

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
