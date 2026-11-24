#!/usr/bin/env python3
"""Preflight immutable target snapshots before FIFO admission or terminal closure.

This module is offline-only. It never calls NVIDIA, Telegram, or GitHub. The
workflow persists the returned scheduler state through the dedicated
automation-state writer action.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import general_autonomous_research as auto
import general_sealed_evaluation as sealed
import general_target_queue as target_queue


INSUFFICIENT_WINDOW_ERRORS = {
    "R60 has no sufficiently sized fresh forward sealed validation interval; "
    "preserve the minimum history and wait for more unconsumed draws.":
        "FORWARD_VALIDATION_WINDOW_BELOW_MINIMUM",
    "R60 has no sufficiently sized fresh reverse sealed validation interval; "
    "preserve the minimum history and wait for more unconsumed draws.":
        "REVERSE_VALIDATION_WINDOW_BELOW_MINIMUM",
    "R60 discovery history is shorter than the default minimum training window.":
        "DISCOVERY_HISTORY_BELOW_MINIMUM",
}
INELIGIBLE_REASON_CODE = "INSUFFICIENT_FRESH_SEALED_WINDOWS"


class TargetAdmissionError(RuntimeError):
    """Raised when target admission or retirement cannot be proven safe."""


def _canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    )


def _preflight_snapshot(
    archive_path: str | Path,
    ledger_path: str | Path,
    memory_path: str | Path,
    *,
    expected_item: dict[str, Any],
) -> dict[str, Any]:
    """Return number-free eligibility metadata; classify only exact known insufficiency failures."""
    try:
        plan = sealed.preflight(
            archive_path,
            ledger_path,
            offline_memory_path=memory_path,
        )
    except sealed.SealedEvaluationError as exc:
        reason_code = INSUFFICIENT_WINDOW_ERRORS.get(str(exc))
        if reason_code is None:
            raise TargetAdmissionError(
                "Sealed preflight failed for a reason that is not an approved insufficient-window condition."
            ) from None
        return {
            "eligible": False,
            "reason_code": reason_code,
            "status": "WAITING_FOR_FRESH_SEALED_WINDOWS",
            "target_date": expected_item["target_date"],
            "archive_sha256": expected_item["archive_sha256"],
            "archive_record_count": expected_item["archive_record_count"],
            "target_numbers_exposed": False,
        }

    if (
        not isinstance(plan, dict)
        or plan.get("status") != "READY"
        or plan.get("partition_version") != sealed.PARTITION_VERSION
    ):
        raise TargetAdmissionError("Sealed preflight returned an unknown, outdated, or non-ready result.")
    if (
        plan.get("directional_validation_windows_disjoint") is not True
        or plan.get("target_date") != expected_item["target_date"]
        or plan.get("archive_sha256") != expected_item["archive_sha256"]
        or plan.get("archive_record_count") != expected_item["archive_record_count"]
    ):
        raise TargetAdmissionError("Sealed preflight result does not match the immutable staged snapshot.")
    if (
        plan.get("target_numbers_exposed") is not False
        or plan.get("target_in_discovery") is not False
        or plan.get("target_in_validation") is not False
        or plan.get("target_in_reverse_validation") is not False
    ):
        raise TargetAdmissionError("Sealed preflight did not preserve hidden-target isolation.")
    regimes = plan.get("regimes")
    r60 = regimes.get("R60") if isinstance(regimes, dict) else None
    forward = plan.get("forward_validation_dates")
    reverse = plan.get("reverse_validation_dates")
    if (
        not isinstance(r60, dict)
        or r60.get("status") != "READY"
        or type(r60.get("discovery_count")) is not int
        or r60["discovery_count"] < sealed.DEFAULT_MIN_TRAIN_DRAWS
        or not isinstance(forward, list)
        or len(forward) < sealed.MIN_VALIDATION_DRAWS
        or not isinstance(reverse, list)
        or len(reverse) < sealed.MIN_VALIDATION_DRAWS
        or set(forward).intersection(reverse)
        or plan.get("validation_dates_previously_consumed") is not False
    ):
        raise TargetAdmissionError("Sealed preflight metadata did not meet the fixed directional minimums.")
    return {
        "eligible": True,
        "status": "READY",
        "target_date": expected_item["target_date"],
        "archive_sha256": expected_item["archive_sha256"],
        "archive_record_count": expected_item["archive_record_count"],
        "discovery_count": r60["discovery_count"],
        "forward_validation_count": len(forward),
        "reverse_validation_count": len(reverse),
        "target_numbers_exposed": False,
    }


def _attempts_for_target(state: dict[str, Any], item: dict[str, Any]) -> list[dict[str, Any]]:
    same_date = [
        attempt for attempt in state["attempts"]
        if attempt["target_date"] == item["target_date"]
    ]
    if any(
        attempt["archive_sha256"] != item["archive_sha256"]
        or attempt["archive_record_count"] != item["archive_record_count"]
        for attempt in same_date
    ):
        raise TargetAdmissionError("Target attempt history uses a conflicting immutable archive identity.")
    return same_date


def _ledger_entries_for_target(
    ledger: dict[str, Any], item: dict[str, Any]
) -> list[dict[str, Any]]:
    same_date = [
        entry for entry in ledger["entries"]
        if entry.get("target_date") == item["target_date"]
    ]
    if any(
        entry.get("archive_sha256") != item["archive_sha256"]
        or entry.get("archive_record_count") != item["archive_record_count"]
        for entry in same_date
    ):
        raise TargetAdmissionError("Sealed ledger contains a conflicting archive identity for this target date.")
    return same_date


def _target_evaluations_for_target(
    state: dict[str, Any], item: dict[str, Any]
) -> list[dict[str, Any]]:
    same_date = [
        entry for entry in state["target_evaluations"]
        if entry.get("target_date") == item["target_date"]
    ]
    if any(
        entry.get("archive_sha256") != item["archive_sha256"]
        or entry.get("archive_record_count") != item["archive_record_count"]
        for entry in same_date
    ):
        raise TargetAdmissionError("Local target-evaluation history uses a conflicting archive identity.")
    return same_date


def maintain_targets(
    state: dict[str, Any],
    *,
    archive_path: str | Path,
    ledger_path: str | Path,
    memory_path: str | Path,
    workspace: str | Path | None = None,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Stage the latest archive, close only untouched ineligible targets, and admit only READY snapshots."""
    original = copy.deepcopy(auto.validate_state(copy.deepcopy(state)))
    updated = copy.deepcopy(original)
    queue, capture = target_queue.capture_new_targets(
        updated["target_queue"],
        archive_path=archive_path,
        attempts=updated["attempts"],
        events=updated["events"],
        now=now,
        admit_latest=False,
    )
    updated["target_queue"] = queue
    changed = _canonical(original) != _canonical(updated)
    retired_target_date = None
    admitted_target_date = None
    active = target_queue.active_item(queue)
    latest = target_queue.latest_snapshot_item(queue)

    with tempfile.TemporaryDirectory(dir=str(workspace) if workspace else None) as temporary:
        scratch = Path(temporary)

        if active is not None:
            active_archive = scratch / "active-target.json"
            target_queue.materialize_front(queue, active_archive)
            active_check = _preflight_snapshot(
                active_archive, ledger_path, memory_path, expected_item=active
            )
            if active_check["eligible"]:
                report = {
                    "status": "ACTIVE_TARGET_READY",
                    "active_available": True,
                    "state_updated": changed,
                    "active_target_date": active["target_date"],
                    "latest_candidate_date": latest["target_date"] if latest else None,
                    "retired_target_date": None,
                    "admitted_target_date": None,
                    "preflight": active_check,
                    "target_values_rendered": False,
                    "hosted_model_request_performed": False,
                }
                auto.validate_state(updated)
                return updated, report

            ledger = sealed.load_ledger(ledger_path)
            attempts = _attempts_for_target(updated, active)
            claims = _ledger_entries_for_target(ledger, active)
            target_evaluations = _target_evaluations_for_target(updated, active)
            if attempts or claims or target_evaluations:
                report = {
                    "status": "ACTIVE_TARGET_REQUIRES_RECOVERY_OR_REVIEW",
                    "active_available": False,
                    "state_updated": changed,
                    "active_target_date": active["target_date"],
                    "latest_candidate_date": latest["target_date"] if latest else None,
                    "retired_target_date": None,
                    "admitted_target_date": None,
                    "reason_code": active_check["reason_code"],
                    "attempt_count": len(attempts),
                    "sealed_claim_count": len(claims),
                    "local_target_evaluation_count": len(target_evaluations),
                    "target_values_rendered": False,
                    "hosted_model_request_performed": False,
                }
                auto.validate_state(updated)
                return updated, report

            try:
                queue = target_queue.retire_ineligible_front(
                    queue,
                    target_date=active["target_date"],
                    archive_sha256=active["archive_sha256"],
                    archive_record_count=active["archive_record_count"],
                    reason_code=INELIGIBLE_REASON_CODE,
                    now=now,
                )
            except target_queue.TargetQueueError as exc:
                raise TargetAdmissionError("Preflight-proven ineligible target could not be closed: " + str(exc)) from None
            updated["target_queue"] = queue
            changed = True
            retired_target_date = active["target_date"]
            active = None
            latest = target_queue.latest_snapshot_item(queue)

        if active is None and latest is not None:
            completed = queue["completed"]
            last_completed = completed[-1]["target_date"] if completed else None
            if last_completed is None or latest["target_date"] > last_completed:
                candidate_archive = scratch / "latest-candidate.json"
                target_queue.materialize_latest_candidate(queue, candidate_archive)
                candidate_check = _preflight_snapshot(
                    candidate_archive, ledger_path, memory_path, expected_item=latest
                )
                if candidate_check["eligible"]:
                    try:
                        queue = target_queue.admit_latest_snapshot(queue)
                    except target_queue.TargetQueueError as exc:
                        raise TargetAdmissionError("Eligible latest target could not be admitted: " + str(exc)) from None
                    updated["target_queue"] = queue
                    changed = True
                    admitted_target_date = latest["target_date"]
                    report = {
                        "status": "LATEST_TARGET_ADMITTED",
                        "active_available": True,
                        "state_updated": changed,
                        "active_target_date": latest["target_date"],
                        "latest_candidate_date": latest["target_date"],
                        "retired_target_date": retired_target_date,
                        "admitted_target_date": admitted_target_date,
                        "preflight": candidate_check,
                        "target_values_rendered": False,
                        "hosted_model_request_performed": False,
                    }
                    auto.validate_state(updated)
                    return updated, report

                report = {
                    "status": "WAITING_FOR_FRESH_SEALED_WINDOWS",
                    "active_available": False,
                    "state_updated": changed,
                    "active_target_date": None,
                    "latest_candidate_date": latest["target_date"],
                    "retired_target_date": retired_target_date,
                    "admitted_target_date": None,
                    "reason_code": candidate_check["reason_code"],
                    "preflight": candidate_check,
                    "target_values_rendered": False,
                    "hosted_model_request_performed": False,
                }
                auto.validate_state(updated)
                return updated, report

        report = {
            "status": "WAITING_FOR_NEW_LATEST_DRAW",
            "active_available": False,
            "state_updated": changed,
            "active_target_date": None,
            "latest_candidate_date": latest["target_date"] if latest else None,
            "retired_target_date": retired_target_date,
            "admitted_target_date": None,
            "target_values_rendered": False,
            "hosted_model_request_performed": False,
        }
    auto.validate_state(updated)
    return updated, report


def _write_github_output(path: str | None, result: dict[str, Any]) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        for key in (
            "status", "active_available", "state_updated",
            "active_target_date", "latest_candidate_date",
            "retired_target_date", "admitted_target_date",
        ):
            value = result.get(key)
            if value is None:
                continue
            rendered = "true" if value is True else "false" if value is False else str(value)
            if "\n" in rendered or "\r" in rendered:
                raise TargetAdmissionError("GitHub output values must be single-line.")
            stream.write(key + "=" + rendered + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path("general_autonomous_research_state.json"))
    parser.add_argument("--archive", type=Path, default=Path("super_loto_archive.json"))
    parser.add_argument("--ledger", type=Path, default=Path("general_evaluation_ledger.json"))
    parser.add_argument("--memory", type=Path, default=Path("general_research_memory.json"))
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--github-output")
    args = parser.parse_args(argv)
    try:
        state = auto.read_state(args.state)
        updated, report = maintain_targets(
            state,
            archive_path=args.archive,
            ledger_path=args.ledger,
            memory_path=args.memory,
            workspace=args.workspace,
        )
        state_updated = _canonical(updated) != _canonical(state)
        if state_updated:
            auto.write_state(args.state, updated)
        report["state_updated"] = state_updated
        _write_github_output(args.github_output, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False))
        return 0
    except Exception as exc:
        print("TARGET ADMISSION BLOCKED:", type(exc).__name__, str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
