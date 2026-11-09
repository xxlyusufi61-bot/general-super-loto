#!/usr/bin/env python3
"""Durable FIFO snapshots for every newly observed canonical Super Loto draw.

The queue stores compressed, content-addressed archive prefixes inside the existing
autonomous scheduler state object. It never calls a hosted model or edits the
canonical archive/state store by itself.
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

QUEUE_SCHEMA_VERSION = 1
RESEARCH_SCOPE_START = date(2020, 8, 2)
MAX_PENDING_TARGETS = 128
MAX_COMPLETED_TARGETS = 10_000
MAX_SNAPSHOT_BYTES = 1_500_000
MAX_COMPRESSED_QUEUE_BYTES = 5_000_000


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
        "items": [],
        "completed": [],
    }


def validate_queue(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "observed_through_date", "items", "completed"
    }:
        raise TargetQueueError("Target queue has an invalid schema.")
    if type(value.get("schema_version")) is not int or value["schema_version"] != QUEUE_SCHEMA_VERSION:
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
        if item.get("outcome") not in {"SUCCEEDED", "NO_ELIGIBLE_CANDIDATE"}:
            raise TargetQueueError("Completed target outcome is not terminal-success metadata.")
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


def capture_new_targets(
    queue_value: Any,
    *,
    archive_path: str | Path,
    attempts: list[dict[str, Any]],
    events: list[dict[str, Any]],
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Append every unseen canonical draw, preserving a legacy active target safely."""
    queue = copy.deepcopy(validate_queue(queue_value))
    archive = sealed.load_archive(archive_path)
    rows = _copy_rows(archive["rows"])
    source_sha = archive["archive_sha256"]
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise TargetQueueError("Queue timestamp must include a timezone.")
    stamp = current.astimezone(timezone.utc).isoformat()
    original = json.dumps(queue, sort_keys=True, separators=(",", ":"))

    if queue["observed_through_date"] is None:
        anchor: str | None = None
        if attempts:
            last_attempt = attempts[-1]
            anchor = _canonical_date(last_attempt.get("target_date"), "last_attempt.target_date")
            last_event = next(
                (event for event in reversed(events) if event.get("attempt_id") == last_attempt.get("attempt_id")),
                None,
            )
            if last_event is None or last_event.get("outcome") == "FAILED":
                prefix = [row for row in rows if row["date"] <= anchor]
                if (
                    not prefix
                    or prefix[-1]["date"] != anchor
                    or archive["last_date"] != anchor
                    or source_sha != last_attempt.get("archive_sha256")
                    or archive["record_count"] != last_attempt.get("archive_record_count")
                ):
                    raise TargetQueueError(
                        "Legacy active target source archive identity cannot be proven; manual recovery is required."
                    )
                # The legacy attempt hash covers the original raw archive document
                # (including its formatting and metadata), while this FIFO snapshot
                # has a new canonical serialization. Prove legacy identity using
                # the exact raw source hash/count, then bind future FIFO processing
                # to the canonical snapshot's own independent digest.
                legacy_item = _make_item(
                    prefix, source_archive_sha256=source_sha,
                    enqueued_at_utc=last_attempt["claimed_at_utc"],
                )
                queue["items"].append(legacy_item)
        else:
            anchor = rows[-2]["date"] if len(rows) > 1 else None

        start_index = 0
        if anchor is not None:
            matching = [index for index, row in enumerate(rows) if row["date"] == anchor]
            if not matching:
                raise TargetQueueError("Queue bootstrap cursor is absent from the canonical archive.")
            start_index = matching[-1] + 1
        for index in range(start_index, len(rows)):
            if len(queue["items"]) >= MAX_PENDING_TARGETS:
                raise TargetQueueError("FIFO capacity reached; no cursor was advanced and no target was dropped.")
            queue["items"].append(
                _make_item(
                    rows[: index + 1],
                    source_archive_sha256=source_sha,
                    enqueued_at_utc=stamp,
                )
            )
        queue["observed_through_date"] = rows[-1]["date"]
    else:
        cursor = queue["observed_through_date"]
        matching = [index for index, row in enumerate(rows) if row["date"] == cursor]
        if not matching:
            raise TargetQueueError("Canonical archive no longer contains the persisted queue cursor.")
        cursor_index = matching[-1]
        if any(row["date"] < cursor for row in rows[cursor_index + 1:]):
            raise TargetQueueError("Canonical archive chronology regressed after the queue cursor.")
        for item in queue["items"]:
            prefix = [row for row in rows if row["date"] <= item["target_date"]]
            if not prefix or prefix[-1]["date"] != item["target_date"]:
                raise TargetQueueError("A queued target is absent from the canonical archive.")
            snapshot = _snapshot_bytes(prefix)
            if (
                hashlib.sha256(snapshot).hexdigest() != item["archive_sha256"]
                or len(prefix) != item["archive_record_count"]
            ):
                raise TargetQueueError("Canonical archive changed beneath a queued immutable snapshot.")
        for index in range(cursor_index + 1, len(rows)):
            if len(queue["items"]) >= MAX_PENDING_TARGETS:
                raise TargetQueueError("FIFO capacity reached; no cursor was advanced and no target was dropped.")
            queue["items"].append(
                _make_item(
                    rows[: index + 1],
                    source_archive_sha256=source_sha,
                    enqueued_at_utc=stamp,
                )
            )
        queue["observed_through_date"] = rows[-1]["date"]

    validate_queue(queue)
    changed = original != json.dumps(queue, sort_keys=True, separators=(",", ":"))
    return queue, {
        "changed": changed,
        "pending_target_count": len(queue["items"]),
        "completed_target_count": len(queue["completed"]),
        "has_pending": bool(queue["items"]),
        "front_target_date": queue["items"][0]["target_date"] if queue["items"] else None,
        "observed_through_date": queue["observed_through_date"],
        "source_archive_sha256": source_sha,
        "target_values_rendered": False,
    }


def materialize_front(queue_value: Any, output_path: str | Path) -> dict[str, Any]:
    """Write only the oldest sealed snapshot to the workspace archive path."""
    queue = validate_queue(queue_value)
    if not queue["items"]:
        raise TargetQueueError("FIFO target queue is empty.")
    item = queue["items"][0]
    raw, rows = _decode_item(item)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".fifo.tmp")
    temporary.write_bytes(raw)
    try:
        loaded = sealed.load_archive(temporary)
        if (
            loaded["last_date"] != item["target_date"]
            or loaded["archive_sha256"] != item["archive_sha256"]
            or loaded["record_count"] != item["archive_record_count"]
        ):
            raise TargetQueueError("Materialized FIFO snapshot failed archive identity checks.")
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "status": "FIFO_FRONT_SNAPSHOT_MATERIALIZED",
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
    """Remove the FIFO head only after a valid terminal scientific outcome."""
    queue = copy.deepcopy(validate_queue(queue_value))
    if outcome not in {"SUCCEEDED", "NO_ELIGIBLE_CANDIDATE"}:
        raise TargetQueueError("Only terminal successful outcomes may release a FIFO target.")
    target = _canonical_date(target_date, "completed.target_date")
    if not queue["items"]:
        raise TargetQueueError("Cannot complete a target when the FIFO is empty.")
    front = queue["items"][0]
    if (
        front["target_date"] != target
        or front["archive_sha256"] != archive_sha256
        or front["archive_record_count"] != archive_record_count
    ):
        raise TargetQueueError("Terminal outcome does not match the immutable FIFO head.")
    if len(queue["completed"]) >= MAX_COMPLETED_TARGETS:
        raise TargetQueueError("Completed target history is full; the FIFO head was not removed.")
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
    queue["items"].pop(0)
    return validate_queue(queue)
