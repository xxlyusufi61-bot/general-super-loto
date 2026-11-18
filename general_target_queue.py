#!/usr/bin/env python3
"""One active target snapshot plus a staged latest canonical archive snapshot.

The queue keeps unfinished research bound to an immutable target snapshot while
tracking the newest archive independently. Intermediate draws are never promoted
into separate target work items.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import zlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import general_sealed_evaluation as sealed

QUEUE_SCHEMA_VERSION = 2
RESEARCH_SCOPE_START = date(2020, 8, 2)
MAX_PENDING_TARGETS = 128
MAX_COMPLETED_TARGETS = 10_000
MAX_SNAPSHOT_BYTES = 1_500_000
MAX_COMPRESSED_QUEUE_BYTES = 5_000_000
# Legacy outcomes remain readable for state-history compatibility. New target
# consumption records are written only as EVALUATED.
TERMINAL_TARGET_OUTCOMES = {
    "EVALUATED",
    "SUCCEEDED",
    "NO_ELIGIBLE_CANDIDATE",
    "EXHAUSTED_RETRY_BUDGET",
}


class TargetQueueError(ValueError):
    """Raised when a target queue or an archive snapshot is unsafe."""


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise TargetQueueError(field + " must be an ISO-8601 timestamp.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise TargetQueueError(field + " is not a valid ISO-8601 timestamp.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TargetQueueError(field + " must include a timezone.")
    return parsed.astimezone(timezone.utc)


def _canonical_date(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TargetQueueError(field + " must be YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise TargetQueueError(field + " is not a valid calendar date.") from None
    if parsed.isoformat() != value or parsed < RESEARCH_SCOPE_START:
        raise TargetQueueError(field + " is outside the active 6-of-60 research scope.")
    return value


def _strict_json_loads(raw: bytes) -> Any:
    def reject_constant(value: str) -> None:
        raise TargetQueueError("Non-standard JSON constant is forbidden: " + value)

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise TargetQueueError("Duplicate JSON key is forbidden: " + key)
            result[key] = value
        return result

    try:
        return json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError):
        raise TargetQueueError("Queued archive snapshot is not strict UTF-8 JSON.") from None


def _copy_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    previous_date: str | None = None
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"date", "numbers"}:
            raise TargetQueueError("Canonical archive row has an unexpected shape.")
        day = _canonical_date(row.get("date"), f"archive.rows[{index}].date")
        if previous_date is not None and day <= previous_date:
            raise TargetQueueError("Canonical archive dates must be strictly increasing.")
        numbers = row.get("numbers")
        if (
            not isinstance(numbers, list)
            or len(numbers) != 6
            or any(type(number) is not int or not 1 <= number <= 60 for number in numbers)
            or numbers != sorted(numbers)
            or len(set(numbers)) != 6
        ):
            raise TargetQueueError("Canonical archive row is not a sorted 6-of-60 draw.")
        result.append({"date": day, "numbers": list(numbers)})
        previous_date = day
    if not result:
        raise TargetQueueError("Canonical archive is empty.")
    return result


def _snapshot_bytes(rows: list[dict[str, Any]]) -> bytes:
    payload = {"draws": _copy_rows(rows)}
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    if len(raw) > MAX_SNAPSHOT_BYTES:
        raise TargetQueueError("A target archive snapshot exceeds the configured byte limit.")
    return raw


def _decode_item(item: dict[str, Any]) -> tuple[bytes, list[dict[str, Any]]]:
    encoded = item.get("snapshot_zlib_base64")
    if not isinstance(encoded, str):
        raise TargetQueueError("Queued snapshot payload is missing.")
    try:
        compressed = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise TargetQueueError("Queued snapshot payload is not valid base64.") from None
    if not compressed:
        raise TargetQueueError("Queued snapshot payload is empty.")
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(compressed, MAX_SNAPSHOT_BYTES + 1)
        if len(raw) > MAX_SNAPSHOT_BYTES or decoder.unconsumed_tail:
            raise TargetQueueError("Queued snapshot exceeds the decompression limit.")
        raw += decoder.flush()
    except zlib.error:
        raise TargetQueueError("Queued snapshot compression is corrupt.") from None
    if len(raw) > MAX_SNAPSHOT_BYTES or not decoder.eof or decoder.unused_data:
        raise TargetQueueError("Queued snapshot framing or size is invalid.")
    if len(raw) != item.get("snapshot_byte_count"):
        raise TargetQueueError("Queued snapshot byte count does not match its metadata.")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != item.get("archive_sha256"):
        raise TargetQueueError("Queued snapshot hash does not match its metadata.")
    payload = _strict_json_loads(raw)
    if not isinstance(payload, dict) or set(payload) != {"draws"}:
        raise TargetQueueError("Queued snapshot root must contain only the draws list.")
    rows = _copy_rows(payload["draws"])
    if len(rows) != item.get("archive_record_count"):
        raise TargetQueueError("Queued snapshot record count does not match its metadata.")
    if rows[-1]["date"] != item.get("target_date"):
        raise TargetQueueError("Queued snapshot target does not match its final archive row.")
    canonical = _snapshot_bytes(rows)
    if canonical != raw:
        raise TargetQueueError("Queued snapshot is not in canonical serialization.")
    return raw, rows


def initial_queue() -> dict[str, Any]:
    return {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "observed_through_date": None,
        "active": None,
        "latest_snapshot": None,
        "completed": [],
    }


def _validate_queue_v1(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "observed_through_date", "items", "completed"
    }:
        raise TargetQueueError("Target queue has an invalid schema.")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise TargetQueueError("Unsupported target queue schema version.")
    cursor = value.get("observed_through_date")
    if cursor is not None:
        cursor = _canonical_date(cursor, "queue.observed_through_date")
    items = value.get("items")
    completed = value.get("completed")
    if not isinstance(items, list) or len(items) > MAX_PENDING_TARGETS:
        raise TargetQueueError("Pending target queue exceeds its bounded capacity.")
    if not isinstance(completed, list) or len(completed) > MAX_COMPLETED_TARGETS:
        raise TargetQueueError("Completed target history exceeds its bounded capacity.")

    previous_target: str | None = None
    compressed_bytes = 0
    pending_dates: set[str] = set()
    required_item = {
        "target_date", "archive_sha256", "source_archive_sha256",
        "archive_record_count", "snapshot_byte_count", "snapshot_zlib_base64",
        "enqueued_at_utc",
    }
    for item in items:
        if not isinstance(item, dict) or set(item) != required_item:
            raise TargetQueueError("Queued target entry has an invalid schema.")
        target = _canonical_date(item.get("target_date"), "queue.item.target_date")
        if previous_target is not None and target <= previous_target:
            raise TargetQueueError("Pending targets must be unique and chronologically ordered.")
        previous_target = target
        pending_dates.add(target)
        for field in ("archive_sha256", "source_archive_sha256"):
            digest = item.get(field)
            if not isinstance(digest, str) or len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise TargetQueueError("Queued target hash is invalid.")
        count = item.get("archive_record_count")
        byte_count = item.get("snapshot_byte_count")
        if type(count) is not int or count < 1 or type(byte_count) is not int or not 1 <= byte_count <= MAX_SNAPSHOT_BYTES:
            raise TargetQueueError("Queued target size metadata is invalid.")
        _timestamp(item.get("enqueued_at_utc"), "queue.item.enqueued_at_utc")
        raw, _ = _decode_item(item)
        compressed_bytes += len(base64.b64decode(item["snapshot_zlib_base64"], validate=True))
        if len(raw) != byte_count:
            raise TargetQueueError("Queued target byte count is invalid.")
    if compressed_bytes > MAX_COMPRESSED_QUEUE_BYTES:
        raise TargetQueueError("Pending target snapshots exceed the total compressed-byte limit.")

    required_completed = {
        "target_date", "archive_sha256", "archive_record_count",
        "completed_at_utc", "outcome",
    }
    completed_dates: list[str] = []
    for item in completed:
        if not isinstance(item, dict) or set(item) != required_completed:
            raise TargetQueueError("Completed target record has an invalid schema.")
        target = _canonical_date(item.get("target_date"), "queue.completed.target_date")
        if target in pending_dates:
            raise TargetQueueError("A target cannot be both pending and completed.")
        completed_dates.append(target)
        digest = item.get("archive_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise TargetQueueError("Completed target hash is invalid.")
        count = item.get("archive_record_count")
        if type(count) is not int or count < 1:
            raise TargetQueueError("Completed target record count is invalid.")
        _timestamp(item.get("completed_at_utc"), "queue.completed.completed_at_utc")
        if item.get("outcome") not in TERMINAL_TARGET_OUTCOMES:
            raise TargetQueueError("Completed target outcome is not an approved terminal target outcome.")
    if completed_dates != sorted(completed_dates) or len(set(completed_dates)) != len(completed_dates):
        raise TargetQueueError("Completed target history must be unique and chronological.")
    if completed_dates and items and items[0]["target_date"] <= completed_dates[-1]:
        raise TargetQueueError("Pending FIFO targets must follow completed target history.")
    if cursor is None:
        if items or completed:
            raise TargetQueueError("A populated target queue must have an observed-through cursor.")
    else:
        if completed_dates and completed_dates[-1] > cursor:
            raise TargetQueueError("Completed target history is newer than the queue cursor.")
        if items and items[-1]["target_date"] > cursor:
            raise TargetQueueError("Pending target is newer than the queue cursor.")
    return value


def _validate_snapshot_item(item: Any, field: str) -> dict[str, Any]:
    required_item = {
        "target_date", "archive_sha256", "source_archive_sha256",
        "archive_record_count", "snapshot_byte_count", "snapshot_zlib_base64",
        "enqueued_at_utc",
    }
    if not isinstance(item, dict) or set(item) != required_item:
        raise TargetQueueError(field + " has an invalid schema.")
    _canonical_date(item.get("target_date"), field + ".target_date")
    for name in ("archive_sha256", "source_archive_sha256"):
        digest = item.get(name)
        if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise TargetQueueError(field + " hash is invalid.")
    if type(item.get("archive_record_count")) is not int or item["archive_record_count"] < 1:
        raise TargetQueueError(field + " record count is invalid.")
    if type(item.get("snapshot_byte_count")) is not int or not 1 <= item["snapshot_byte_count"] <= MAX_SNAPSHOT_BYTES:
        raise TargetQueueError(field + " byte count is invalid.")
    _timestamp(item.get("enqueued_at_utc"), field + ".enqueued_at_utc")
    _decode_item(item)
    return item


def validate_queue(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and value.get("schema_version") == 1:
        return _validate_queue_v1(value)
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "observed_through_date", "active",
        "latest_snapshot", "completed"
    }:
        raise TargetQueueError("Target queue has an invalid schema.")
    if type(value.get("schema_version")) is not int or value["schema_version"] != QUEUE_SCHEMA_VERSION:
        raise TargetQueueError("Unsupported target queue schema version.")
    cursor = value.get("observed_through_date")
    if cursor is not None:
        cursor = _canonical_date(cursor, "queue.observed_through_date")
    active, latest = value.get("active"), value.get("latest_snapshot")
    if active is not None:
        _validate_snapshot_item(active, "queue.active")
    if latest is not None:
        _validate_snapshot_item(latest, "queue.latest_snapshot")
    if (cursor is None) != (latest is None):
        raise TargetQueueError("Latest archive snapshot and observed-through cursor must be present together.")
    if latest is not None and latest["target_date"] != cursor:
        raise TargetQueueError("Latest archive snapshot does not match the observed-through cursor.")
    completed = value.get("completed")
    if not isinstance(completed, list) or len(completed) > MAX_COMPLETED_TARGETS:
        raise TargetQueueError("Completed target history exceeds its bounded capacity.")
    required_completed = {
        "target_date", "archive_sha256", "archive_record_count",
        "completed_at_utc", "outcome",
    }
    completed_dates = []
    for item in completed:
        if not isinstance(item, dict) or set(item) != required_completed:
            raise TargetQueueError("Completed target record has an invalid schema.")
        day = _canonical_date(item.get("target_date"), "queue.completed.target_date")
        digest = item.get("archive_sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(
            char not in "0123456789abcdef" for char in digest
        ):
            raise TargetQueueError("Completed target hash is invalid.")
        if type(item.get("archive_record_count")) is not int or item["archive_record_count"] < 1:
            raise TargetQueueError("Completed target record count is invalid.")
        _timestamp(item.get("completed_at_utc"), "queue.completed.completed_at_utc")
        if item.get("outcome") not in TERMINAL_TARGET_OUTCOMES:
            raise TargetQueueError("Completed target outcome is not an approved terminal target outcome.")
        completed_dates.append(day)
    if completed_dates != sorted(completed_dates) or len(set(completed_dates)) != len(completed_dates):
        raise TargetQueueError("Completed target history must be unique and chronological.")
    last_completed = completed_dates[-1] if completed_dates else None
    if latest is not None and last_completed is not None and latest["target_date"] < last_completed:
        raise TargetQueueError("Latest archive snapshot predates completed target history.")
    if active is not None:
        if last_completed is not None and active["target_date"] <= last_completed:
            raise TargetQueueError("Active target is not newer than completed target history.")
        if latest is None or active["target_date"] > latest["target_date"]:
            raise TargetQueueError("Active target is newer than the latest canonical archive snapshot.")
        if active["target_date"] == latest["target_date"] and active["archive_sha256"] != latest["archive_sha256"]:
            raise TargetQueueError("Active/latest snapshots disagree for the same target date.")
    return value


def active_item(queue_value: Any) -> dict[str, Any] | None:
    queue = validate_queue(queue_value)
    if queue["schema_version"] == 1:
        return queue["items"][0] if queue["items"] else None
    return queue["active"]


def latest_snapshot_item(queue_value: Any) -> dict[str, Any] | None:
    queue = validate_queue(queue_value)
    if queue["schema_version"] == 1:
        return queue["items"][-1] if queue["items"] else None
    return queue["latest_snapshot"]


def _make_item(
    rows: list[dict[str, Any]],
    *,
    source_archive_sha256: str,
    enqueued_at_utc: str,
) -> dict[str, Any]:
    raw = _snapshot_bytes(rows)
    compressed = zlib.compress(raw, level=9)
    item = {
        "target_date": rows[-1]["date"],
        "archive_sha256": hashlib.sha256(raw).hexdigest(),
        "source_archive_sha256": source_archive_sha256,
        "archive_record_count": len(rows),
        "snapshot_byte_count": len(raw),
        "snapshot_zlib_base64": base64.b64encode(compressed).decode("ascii"),
        "enqueued_at_utc": enqueued_at_utc,
    }
    _decode_item(item)
    return item


def _attempt_state_for_item(
    item: dict[str, Any],
    attempts: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    matches = [
        attempt for attempt in attempts
        if attempt.get("target_date") == item["target_date"]
        and attempt.get("archive_sha256") == item["archive_sha256"]
        and attempt.get("archive_record_count") == item["archive_record_count"]
    ]
    if not matches:
        return "UNATTEMPTED", None, None
    attempt = matches[-1]
    event = next(
        (entry for entry in reversed(events) if entry.get("attempt_id") == attempt.get("attempt_id")),
        None,
    )
    if event is None:
        return "IN_PROGRESS", attempt, None
    return event.get("outcome", "UNKNOWN"), attempt, event


def _completed_record(item: dict[str, Any], outcome: str, completed_at_utc: str) -> dict[str, Any]:
    return {
        "target_date": item["target_date"],
        "archive_sha256": item["archive_sha256"],
        "archive_record_count": item["archive_record_count"],
        "completed_at_utc": completed_at_utc,
        "outcome": outcome,
    }


def _verify_item_against_archive(item: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    prefix = [row for row in rows if row["date"] <= item["target_date"]]
    if not prefix or prefix[-1]["date"] != item["target_date"]:
        raise TargetQueueError("A preserved active target is absent from the canonical archive.")
    snapshot = _snapshot_bytes(prefix)
    if hashlib.sha256(snapshot).hexdigest() != item["archive_sha256"] or len(prefix) != item["archive_record_count"]:
        raise TargetQueueError("Canonical archive changed beneath the preserved active snapshot.")


def capture_new_targets(
    queue_value: Any,
    *,
    archive_path: str | Path,
    attempts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Keep one active target and stage only the newest canonical archive snapshot."""
    archive = sealed.load_archive(archive_path)
    rows = _copy_rows(archive["rows"])
    source_sha = archive["archive_sha256"]
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise TargetQueueError("Queue timestamp must include a timezone.")
    stamp = current.astimezone(timezone.utc).isoformat()
    latest_date = rows[-1]["date"]
    original = json.dumps(queue_value, sort_keys=True, separators=(",", ":"))
    previous = validate_queue(queue_value)
    completed = copy.deepcopy(previous["completed"])
    active = None

    if previous["schema_version"] == 1:
        legacy_items = previous["items"]
        if previous["observed_through_date"] is not None:
            cursor = previous["observed_through_date"]
            matching = [index for index, row in enumerate(rows) if row["date"] == cursor]
            if not matching:
                raise TargetQueueError("Canonical archive no longer contains the persisted legacy queue cursor.")
            cursor_index = matching[-1]
            if any(row["date"] < cursor for row in rows[cursor_index + 1:]):
                raise TargetQueueError("Canonical archive chronology regressed after the legacy queue cursor.")

        for item in legacy_items:
            state, _, event = _attempt_state_for_item(item, attempts, events)
            if state in {"IN_PROGRESS", "FAILED"}:
                active = copy.deepcopy(item)
                break
            if state in {"SUCCEEDED", "NO_ELIGIBLE_CANDIDATE"}:
                if latest_date > item["target_date"]:
                    outcome = "EVALUATED" if state == "SUCCEEDED" else "NO_ELIGIBLE_CANDIDATE"
                    if not any(done["target_date"] == item["target_date"] for done in completed):
                        completed.append(_completed_record(item, outcome, event["recorded_at_utc"]))
                elif latest_date == item["target_date"]:
                    active = copy.deepcopy(item)
                    break

        if active is None and legacy_items:
            last_legacy = legacy_items[-1]
            state, _, _ = _attempt_state_for_item(last_legacy, attempts, events)
            completed_dates = [entry["target_date"] for entry in completed]
            if state == "UNATTEMPTED" and (
                not completed_dates or last_legacy["target_date"] > completed_dates[-1]
            ) and latest_date == last_legacy["target_date"]:
                active = copy.deepcopy(last_legacy)

        if active is None and previous["observed_through_date"] is None and attempts:
            last_attempt = attempts[-1]
            last_event = next(
                (entry for entry in reversed(events) if entry.get("attempt_id") == last_attempt.get("attempt_id")),
                None,
            )
            if last_event is None or last_event.get("outcome") == "FAILED":
                if (
                    latest_date != last_attempt.get("target_date")
                    or source_sha != last_attempt.get("archive_sha256")
                    or len(rows) != last_attempt.get("archive_record_count")
                ):
                    raise TargetQueueError(
                        "Legacy active target source archive identity cannot be proven; manual recovery is required."
                    )
                active = _make_item(
                    rows, source_archive_sha256=source_sha,
                    enqueued_at_utc=last_attempt["claimed_at_utc"],
                )
    else:
        if previous["latest_snapshot"] is not None:
            _verify_item_against_archive(previous["latest_snapshot"], rows)
        if previous["active"] is not None:
            _verify_item_against_archive(previous["active"], rows)
            active = copy.deepcopy(previous["active"])
            state, _, event = _attempt_state_for_item(active, attempts, events)
            if state in {"SUCCEEDED", "NO_ELIGIBLE_CANDIDATE"} and latest_date > active["target_date"]:
                outcome = "EVALUATED" if state == "SUCCEEDED" else "NO_ELIGIBLE_CANDIDATE"
                if not any(done["target_date"] == active["target_date"] for done in completed):
                    completed.append(_completed_record(active, outcome, event["recorded_at_utc"]))
                active = None
            elif state == "UNATTEMPTED" and latest_date > active["target_date"]:
                active = None
            # An in-progress or failed package stays pinned to its exact snapshot.
            # A successful/no-candidate pass stays active while no newer draw exists.

    completed.sort(key=lambda item: item["target_date"])
    if len({item["target_date"] for item in completed}) != len(completed):
        raise TargetQueueError("Completed target history contains duplicate dates.")
    previous_latest = (
        previous["latest_snapshot"]
        if previous["schema_version"] == 2
        else (previous["items"][-1] if previous["items"] else None)
    )
    if (
        previous_latest is not None
        and previous_latest["target_date"] == latest_date
        and previous_latest["source_archive_sha256"] == source_sha
        and previous_latest["archive_record_count"] == len(rows)
    ):
        latest_item = copy.deepcopy(previous_latest)
    else:
        latest_item = _make_item(rows, source_archive_sha256=source_sha, enqueued_at_utc=stamp)
    latest_completed = completed[-1]["target_date"] if completed else None
    if active is None and (latest_completed is None or latest_date > latest_completed):
        active = copy.deepcopy(latest_item)
    if active is not None:
        _verify_item_against_archive(active, rows)
        if latest_completed is not None and active["target_date"] <= latest_completed:
            raise TargetQueueError("Selected active target was already completed.")

    next_queue = {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "observed_through_date": latest_date,
        "active": active,
        "latest_snapshot": latest_item,
        "completed": completed[-MAX_COMPLETED_TARGETS:],
    }
    validate_queue(next_queue)
    changed = original != json.dumps(next_queue, sort_keys=True, separators=(",", ":"))
    return next_queue, {
        "changed": changed,
        "pending_target_count": 1 if active is not None else 0,
        "completed_target_count": len(next_queue["completed"]),
        "has_pending": active is not None,
        "front_target_date": active["target_date"] if active is not None else None,
        "active_target_date": active["target_date"] if active is not None else None,
        "latest_observed_date": latest_date,
        "observed_through_date": latest_date,
        "source_archive_sha256": source_sha,
        "target_values_rendered": False,
        "intermediate_draws_promoted_as_targets": False,
    }


def materialize_front(queue_value: Any, output_path: str | Path) -> dict[str, Any]:
    """Write only the active immutable target snapshot to the workspace."""
    validate_queue(queue_value)
    item = active_item(queue_value)
    if item is None:
        raise TargetQueueError("There is no active target snapshot.")
    raw, rows = _decode_item(item)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".active.tmp")
    temporary.write_bytes(raw)
    try:
        loaded = sealed.load_archive(temporary)
        if (
            loaded["last_date"] != item["target_date"]
            or loaded["archive_sha256"] != item["archive_sha256"]
            or loaded["record_count"] != item["archive_record_count"]
        ):
            raise TargetQueueError("Materialized active snapshot failed archive identity checks.")
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "status": "ACTIVE_TARGET_SNAPSHOT_MATERIALIZED",
        "target_date": item["target_date"],
        "archive_sha256": item["archive_sha256"],
        "archive_record_count": len(rows),
        "target_values_rendered": False,
    }


def complete_front(
    queue_value: Any,
    *,
    target_date: str,
    archive_sha256: str,
    archive_record_count: int,
    outcome: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Explicitly close the active target after an EVALUATED ledger claim."""
    if outcome != "EVALUATED":
        raise TargetQueueError("Only an EVALUATED sealed claim may close a target.")
    return _record_terminal_front(
        queue_value,
        target_date=target_date,
        archive_sha256=archive_sha256,
        archive_record_count=archive_record_count,
        outcome=outcome,
        now=now,
    )


def retire_exhausted_front(
    queue_value: Any,
    *,
    target_date: str,
    archive_sha256: str,
    archive_record_count: int,
    failed_attempt_count: int | None = None,
    attempt_count: int | None = None,
    failed_event_count: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Manually close the active target only after exactly ten failed attempts."""
    failures = failed_attempt_count if failed_attempt_count is not None else failed_event_count
    if type(failures) is not int or failures != 10:
        raise TargetQueueError("Retry-budget retirement requires exactly ten failed attempts.")
    return _record_terminal_front(
        queue_value,
        target_date=target_date,
        archive_sha256=archive_sha256,
        archive_record_count=archive_record_count,
        outcome="EXHAUSTED_RETRY_BUDGET",
        now=now,
    )


def _record_terminal_front(
    queue_value: Any,
    *,
    target_date: str,
    archive_sha256: str,
    archive_record_count: int,
    outcome: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    queue = copy.deepcopy(validate_queue(queue_value))
    target = _canonical_date(target_date, "completed.target_date")
    if queue["schema_version"] == 1:
        if not queue["items"]:
            raise TargetQueueError("Cannot complete a target when the legacy FIFO is empty.")
        front = queue["items"][0]
    else:
        front = queue["active"]
        if front is None:
            raise TargetQueueError("Cannot complete a target when there is no active target.")
    if (
        front["target_date"] != target
        or front["archive_sha256"] != archive_sha256
        or front["archive_record_count"] != archive_record_count
    ):
        raise TargetQueueError("Terminal outcome does not match the active immutable snapshot.")
    if len(queue["completed"]) >= MAX_COMPLETED_TARGETS:
        raise TargetQueueError("Completed target history is full; the active target was not released.")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise TargetQueueError("Completion timestamp must include a timezone.")
    queue["completed"].append({
        "target_date": target,
        "archive_sha256": archive_sha256,
        "archive_record_count": archive_record_count,
        "completed_at_utc": current.astimezone(timezone.utc).isoformat(),
        "outcome": outcome,
    })
    if queue["schema_version"] == 1:
        queue["items"].pop(0)
        return _validate_queue_v1(queue)
    queue["active"] = None
    return validate_queue(queue)

