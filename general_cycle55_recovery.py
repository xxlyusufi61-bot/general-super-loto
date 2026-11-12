#!/usr/bin/env python3
"""Offline, provenance-pinned recovery preview for Cycle #55.

This module does not access GitHub, NVIDIA, Telegram, or the production state store.
It reads two operator-supplied immutable state documents and writes a new local
preview file. Publishing that preview is a separate, explicitly authorized step.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import general_autonomous_research as autonomous
import general_target_queue as target_queue

TARGET_DATE = "2026-11-10"
TARGET_ARCHIVE_SHA256 = "ae7b79e4922d4178a86c7d7880af3e4758d1a5e7f78d6cc3d2595fbb4fd66596"
TARGET_ARCHIVE_RECORD_COUNT = 983
TARGET_GITHUB_RUN_ID = "43288555182"
PROVIDER_FAILURE_AT_UTC = "2026-11-12T19:29:11.330937+00:00"

CURRENT_STATE_PROVENANCE = {
    "generation": 52,
    "commit_sha": "0c9e5bbbe0cbafea188f941b0408e068080c1e68",
    "blob_sha": "8abcb27ce7b7b92395646f886a2656cf55f9f99d",
    "content_sha256": "1d016b6ee7e7791ab0a72838b014aabc5de83d918bab2ae2536c063332d801c1",
}
SNAPSHOT_SOURCE_PROVENANCE = {
    "generation": 51,
    "commit_sha": "6197bfadb8af22633dcb22c5490d32c9ffbd52c7",
    "blob_sha": "2dbb6630ba82607001f03aded929f313480d41f7",
    "content_sha256": "6ed82ced53d314e2431987d63de53570562d8a92de910550848b1727ae66221d",
}

RECOVERY_TYPE = "CYCLE55_FALSE_TERMINAL_COMPLETION"
RECOVERY_REASON = "POST_PROVIDER_MEMORY_APPEND_FAILURE"


class Cycle55RecoveryError(RuntimeError):
    """Raised when the Cycle #55 recovery cannot be proven safe."""


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise Cycle55RecoveryError(label + " contains a non-standard JSON constant.")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise Cycle55RecoveryError(label + " contains a duplicate JSON key.")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError):
        raise Cycle55RecoveryError(label + " is not valid strict UTF-8 JSON.") from None
    if not isinstance(value, dict):
        raise Cycle55RecoveryError(label + " root must be a JSON object.")
    return value


def _git_blob_sha(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verify_provenance(
    actual: dict[str, Any],
    expected: dict[str, Any],
    label: str,
) -> None:
    if actual != expected:
        raise Cycle55RecoveryError(label + " provenance does not match the pinned Git history.")


def recover_cycle55_state(
    current_state: dict[str, Any],
    snapshot_source_state: dict[str, Any],
    *,
    recorded_at_utc: str,
    current_state_provenance: dict[str, Any] | None = None,
    snapshot_source_provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a v3 state preview without mutating either input document."""
    _verify_provenance(
        current_state_provenance or CURRENT_STATE_PROVENANCE,
        CURRENT_STATE_PROVENANCE,
        "Current state",
    )
    _verify_provenance(
        snapshot_source_provenance or SNAPSHOT_SOURCE_PROVENANCE,
        SNAPSHOT_SOURCE_PROVENANCE,
        "Generation 51 snapshot source",
    )
    if not isinstance(current_state, dict) or current_state.get("schema_version") != 2:
        raise Cycle55RecoveryError("Current production state must be the pinned schema v2 document.")
    if not isinstance(snapshot_source_state, dict) or snapshot_source_state.get("schema_version") != 2:
        raise Cycle55RecoveryError("Generation 51 source state must be schema v2.")
    if "recovery_history" in current_state or "recovery_history" in snapshot_source_state:
        raise Cycle55RecoveryError("A prior schema migration is present; this one-time recovery is not repeatable.")

    try:
        current = autonomous.validate_state(copy.deepcopy(current_state))
        source = autonomous.validate_state(copy.deepcopy(snapshot_source_state))
    except autonomous.AutonomousResearchError as exc:
        raise Cycle55RecoveryError("An input state failed scheduler validation: " + str(exc)) from None

    # The same attempt reservation, authorization and pre-recovery events must be
    # present in both immutable snapshots. Only the false terminal mutation differs.
    if (
        current["project"] != source["project"]
        or current["authorization"] != source["authorization"]
        or current["attempts"] != source["attempts"]
    ):
        raise Cycle55RecoveryError("Current state diverges from the Generation 51 reservation history.")

    attempts = current["attempts"]
    attempt_matches = [
        item for item in attempts
        if item["github_run_id"] == TARGET_GITHUB_RUN_ID
    ]
    if len(attempt_matches) != 1:
        raise Cycle55RecoveryError("Cycle #55 attempt identity is missing or ambiguous.")
    attempt = attempt_matches[0]
    if (
        attempt["target_date"] != TARGET_DATE
        or attempt["archive_sha256"] != TARGET_ARCHIVE_SHA256
        or attempt["archive_record_count"] != TARGET_ARCHIVE_RECORD_COUNT
        or attempts[-1]["attempt_id"] != attempt["attempt_id"]
    ):
        raise Cycle55RecoveryError("Cycle #55 attempt metadata does not match the pinned target.")

    current_events = current["events"]
    source_events = source["events"]
    if len(current_events) != len(source_events) + 1 or current_events[:-1] != source_events:
        raise Cycle55RecoveryError("Current event ledger is not exactly Generation 51 plus one terminal event.")
    wrong_event = current_events[-1]
    if (
        wrong_event["attempt_id"] != attempt["attempt_id"]
        or wrong_event["outcome"] != "NO_ELIGIBLE_CANDIDATE"
        or wrong_event["reason_code"] != "NO_FROZEN_DIRECTIONAL_CANDIDATE"
    ):
        raise Cycle55RecoveryError("Cycle #55 does not contain the specific false terminal event being recovered.")
    if any(item["attempt_id"] == attempt["attempt_id"] for item in source_events):
        raise Cycle55RecoveryError("Generation 51 already contains a terminal event for Cycle #55.")

    source_queue = source["target_queue"]
    current_queue = current["target_queue"]
    if source_queue["observed_through_date"] != current_queue["observed_through_date"]:
        raise Cycle55RecoveryError("FIFO observation cursor changed after the pinned source snapshot.")
    if not source_queue["items"] or source_queue["items"][0]["target_date"] != TARGET_DATE:
        raise Cycle55RecoveryError("Generation 51 does not contain Cycle #55 at the FIFO head.")
    restored_item = source_queue["items"][0]
    if (
        restored_item["archive_sha256"] != TARGET_ARCHIVE_SHA256
        or restored_item["archive_record_count"] != TARGET_ARCHIVE_RECORD_COUNT
    ):
        raise Cycle55RecoveryError("Generation 51 FIFO snapshot identity does not match Cycle #55.")
    try:
        target_queue._decode_item(restored_item)
    except target_queue.TargetQueueError as exc:
        raise Cycle55RecoveryError("Generation 51 FIFO snapshot failed integrity validation: " + str(exc)) from None

    # Require byte-for-byte queue metadata continuity. No later target may be
    # dropped, reordered, regenerated, or substituted during this repair.
    if source_queue["items"][1:] != current_queue["items"]:
        raise Cycle55RecoveryError("Current pending FIFO tail is not identical to Generation 51.")
    if current_queue["completed"][:-1] != source_queue["completed"]:
        raise Cycle55RecoveryError("Completed FIFO history differs from Generation 51.")
    if len(current_queue["completed"]) != len(source_queue["completed"]) + 1:
        raise Cycle55RecoveryError("The false completion record is missing or ambiguous.")
    wrong_completed = current_queue["completed"][-1]
    if (
        wrong_completed["target_date"] != TARGET_DATE
        or wrong_completed["archive_sha256"] != TARGET_ARCHIVE_SHA256
        or wrong_completed["archive_record_count"] != TARGET_ARCHIVE_RECORD_COUNT
        or wrong_completed["outcome"] != "NO_ELIGIBLE_CANDIDATE"
    ):
        raise Cycle55RecoveryError("Cycle #55 false FIFO completion record does not match the pinned target.")

    failure_time = autonomous._timestamp(PROVIDER_FAILURE_AT_UTC, "provider_failure_at_utc")
    requested_time = autonomous._timestamp(recorded_at_utc, "recorded_at_utc")
    if requested_time < failure_time:
        raise Cycle55RecoveryError("Recovery journal timestamp predates the actual workflow failure.")

    restored_item_sha = _canonical_sha256(restored_item)
    journal_without_id = {
        "schema_version": 1,
        "recovery_type": RECOVERY_TYPE,
        "recorded_at_utc": requested_time.isoformat(),
        "attempt_id": attempt["attempt_id"],
        "target_date": TARGET_DATE,
        "archive_sha256": TARGET_ARCHIVE_SHA256,
        "archive_record_count": TARGET_ARCHIVE_RECORD_COUNT,
        "failure_at_utc": failure_time.isoformat(),
        "reason_code": RECOVERY_REASON,
        "source_state": copy.deepcopy(CURRENT_STATE_PROVENANCE),
        "snapshot_source": copy.deepcopy(SNAPSHOT_SOURCE_PROVENANCE),
        "archived_terminal_event": copy.deepcopy(wrong_event),
        "archived_completed_record": copy.deepcopy(wrong_completed),
        "restored_snapshot_item_sha256": restored_item_sha,
    }
    recovery_entry = {
        **journal_without_id,
        "recovery_id": _canonical_sha256(journal_without_id),
    }

    recovered = copy.deepcopy(current)
    recovered["events"][-1] = {
        "attempt_id": attempt["attempt_id"],
        "outcome": "FAILED",
        "recorded_at_utc": failure_time.isoformat(),
        "reason_code": "WORKFLOW_FAILED",
    }
    recovered["target_queue"]["items"].insert(0, copy.deepcopy(restored_item))
    recovered["target_queue"]["completed"] = copy.deepcopy(source_queue["completed"])
    recovered["recovery_history"].append(recovery_entry)

    try:
        return autonomous.validate_state(recovered)
    except autonomous.AutonomousResearchError as exc:
        raise Cycle55RecoveryError("Recovered preview failed schema v3 validation: " + str(exc)) from None


def build_preview_files(
    current_state_path: str | Path,
    snapshot_source_path: str | Path,
    output_path: str | Path,
    *,
    recorded_at_utc: str,
) -> dict[str, Any]:
    """Verify pinned state blobs and write only a new local v3 preview file."""
    current_path = Path(current_state_path).resolve()
    source_path = Path(snapshot_source_path).resolve()
    output = Path(output_path).resolve()
    if output in {current_path, source_path}:
        raise Cycle55RecoveryError("Preview output must not overwrite either source state file.")
    if output.exists():
        raise Cycle55RecoveryError("Preview output already exists; refusing to overwrite it.")

    try:
        current_raw = current_path.read_bytes()
        source_raw = source_path.read_bytes()
    except OSError:
        raise Cycle55RecoveryError("A pinned source state file could not be read.") from None

    for raw, provenance, label in (
        (current_raw, CURRENT_STATE_PROVENANCE, "Current state"),
        (source_raw, SNAPSHOT_SOURCE_PROVENANCE, "Generation 51 snapshot source"),
    ):
        if hashlib.sha256(raw).hexdigest() != provenance["content_sha256"]:
            raise Cycle55RecoveryError(label + " content SHA-256 does not match its state-store manifest.")
        if _git_blob_sha(raw) != provenance["blob_sha"]:
            raise Cycle55RecoveryError(label + " Git blob SHA does not match its pinned commit.")

    current = _strict_json(current_raw, "Current state")
    source = _strict_json(source_raw, "Generation 51 snapshot source")
    recovered = recover_cycle55_state(
        current, source, recorded_at_utc=recorded_at_utc
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(recovered, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    ).encode("utf-8")
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=output.parent, prefix=output.name + ".", suffix=".tmp", delete=False
        ) as stream:
            temp_name = stream.name
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, output)
    except OSError:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
        raise Cycle55RecoveryError("Could not atomically write the local recovery preview.") from None

    return {
        "status": "LOCAL_RECOVERY_PREVIEW_CREATED",
        "schema_version": recovered["schema_version"],
        "target_date": TARGET_DATE,
        "attempt_id": attempt["attempt_id"] if (attempt := next(
            item for item in recovered["attempts"] if item["github_run_id"] == TARGET_GITHUB_RUN_ID
        )) else None,
        "fifo_head_date": recovered["target_queue"]["items"][0]["target_date"],
        "pending_target_count": len(recovered["target_queue"]["items"]),
        "recovery_history_count": len(recovered["recovery_history"]),
        "active_outcome": recovered["events"][-1]["outcome"],
        "active_reason_code": recovered["events"][-1]["reason_code"],
        "production_state_written": False,
        "nvidia_requests": 0,
        "research_cycles_run": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-state", type=Path, required=True)
    parser.add_argument("--snapshot-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--recorded-at-utc", required=True)
    args = parser.parse_args(argv)
    try:
        result = build_preview_files(
            args.current_state,
            args.snapshot_source,
            args.output,
            recorded_at_utc=args.recorded_at_utc,
        )
    except Cycle55RecoveryError as exc:
        print("Cycle #55 recovery preview refused: " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
