"""One-shot, leakage-free orchestration for a sealed chronological evaluation.

The forward and reverse discovery requests each receive only their direction-specific discovery view. Two independently frozen DSL specifications
can claim disjoint R60 directional holdouts. The claim and immutable
archive snapshot must be persisted before any real-archive scoring starts.
This module never changes the method registry or emits draw-number sets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import general_data_guard as guard
import general_hypothesis_execution_v2 as execution_v2
import general_research_windows as research_windows
from general_bidirectional_evaluation import evaluate_bidirectional_full_history
from general_execution_spec_dispatch import (
    execution_spec_direction,
    execution_spec_version,
    run_synthetic_contract_tests as run_any_synthetic_contract_tests,
    validate_execution_spec as validate_any_execution_spec,
)
from general_hypothesis_execution import (
    VERSION as ENGINE_VERSION,
    MAX_HISTORY_DRAWS,
    MAX_VALIDATION_DRAWS,
    evaluate_holdout,
    rank_candidates,
    rank_candidates_retrospective,
    evaluate_reverse_retrospective_stress,
    evaluate_time_ordered_cv,
    run_synthetic_contract_tests,
    validate_execution_spec,
)

VERSION = "0.6.0"
PARTITION_VERSION = 2
LEGACY_PARTITION_VERSION = 1
UNSEEN_PARTITION_VERSION = 3
RESEARCH_SCOPE_START = date(2020, 8, 2)
DEFAULT_MIN_TRAIN_DRAWS = 40
DEFAULT_MAX_VALIDATION_DRAWS = 30
MIN_VALIDATION_DRAWS = 2
LEDGER_SCHEMA_VERSION = 1
SNAPSHOT_DIRECTORY = "general_evaluation_snapshots"
ALLOWED_CLAIM_STATES = {"CLAIMED", "EVALUATED"}


class SealedEvaluationError(RuntimeError):
    """Raised when a sealed evaluation cannot proceed safely."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path, description: str) -> tuple[Any, bytes]:
    try:
        raw = path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SealedEvaluationError(description + " could not be read as valid UTF-8 JSON.") from None
    return payload, raw


def load_archive(path: str | Path) -> dict[str, Any]:
    """Load and validate an immutable archive view without returning target values in summaries."""
    archive_path = Path(path)
    payload, raw = _read_json(archive_path, "Archive")
    if isinstance(payload, dict):
        if "draws" in payload and "records" in payload:
            raise SealedEvaluationError("Archive has ambiguous draws/records fields.")
        rows = payload.get("draws", payload.get("records"))
    else:
        rows = payload
    if not isinstance(rows, list):
        raise SealedEvaluationError("Archive must contain a list of draws.")
    try:
        summary = guard.validate_archive(rows)
    except Exception:
        raise SealedEvaluationError("Data Guard rejected the archive.") from None
    if summary.get("valid") is not True:
        raise SealedEvaluationError("Data Guard did not validate the archive.")
    for row in rows:
        if row.get("numbers") != sorted(row.get("numbers", [])):
            raise SealedEvaluationError("Archive number sets must be sorted; no silent normalization is allowed.")
    return {
        "rows": rows,
        "archive_sha256": _sha256_bytes(raw),
        "archive_byte_count": len(raw),
        "record_count": summary["record_count"],
        "first_date": summary["first_date"],
        "last_date": summary["last_date"],
    }


def _copy_row(row: dict[str, Any]) -> dict[str, Any]:
    return {"date": row["date"], "numbers": list(row["numbers"])}


def _offline_development_consumption(
    memory_path: str | Path = "general_research_memory.json",
) -> tuple[list[str], str]:
    """Load validated offline-development dates and their immutable snapshot digest."""
    path = Path(memory_path)
    if not path.exists():
        dates: list[str] = []
    else:
        try:
            # Lazy import avoids the sealed-evaluator/research-memory/offline-runner
            # module cycle. The loader independently validates the complete memory.
            from general_research_memory import load_memory
            memory = load_memory(path)
            offline = memory["offline_research"]
            windows = offline["consumed_development_windows"]
            dates = sorted({
                day
                for window in windows
                for day in window["dates"]
            })
        except Exception as exc:
            raise SealedEvaluationError(
                "Offline development-consumption memory is invalid; sealed research fails closed ("
                + type(exc).__name__ + ")."
            ) from None
    digest = _sha256_bytes(_canonical_json(dates))
    return dates, digest


def _split_archive_legacy(
    rows: list[dict[str, Any]],
    *,
    archive_sha256: str = "",
    used_validation_dates: set[str] | None = None,
    offline_memory_path: str | Path = "general_research_memory.json",
) -> dict[str, Any]:
    """Create fresh disjoint discovery and directional holdouts, per regime.

    Dates already consumed by either directional holdout are excluded from every
    later discovery/validation partition. New reverse and forward holdouts are
    selected from the oldest and newest remaining eligible rows respectively.
    """
    try:
        checked = guard.validate_archive(rows)
    except Exception:
        raise SealedEvaluationError("Data Guard rejected the archive before splitting.") from None
    for row in rows:
        if row.get("numbers") != sorted(row.get("numbers", [])):
            raise SealedEvaluationError("Archive number sets must be sorted; no silent normalization is allowed.")
    target_date = checked.get("last_date")
    if not isinstance(target_date, str):
        raise SealedEvaluationError("Data Guard did not provide a target date.")
    try:
        target_day = date.fromisoformat(target_date)
        isolation = guard.isolate_target(rows, target_date)
    except Exception:
        raise SealedEvaluationError("Latest target isolation failed.") from None
    if isolation.get("isolation_pass") is not True:
        raise SealedEvaluationError("Latest target isolation failed.")
    if isolation.get("target_present_in_archive") is not True:
        raise SealedEvaluationError("Latest target is missing from the archive.")
    pre_target = isolation.get("train")
    excluded_dates = isolation.get("excluded_dates")
    if not isinstance(pre_target, list) or not isinstance(excluded_dates, list):
        raise SealedEvaluationError("Data Guard returned malformed isolation partitions.")
    expected_pre_target = [row["date"] for row in rows if row["date"] < target_date]
    if [row["date"] for row in pre_target] != expected_pre_target:
        raise SealedEvaluationError("Pre-target partition does not exactly match the archive chronology.")
    if any(date.fromisoformat(row["date"]) >= target_day for row in pre_target):
        raise SealedEvaluationError("Target/future row leaked into the pre-target partition.")

    if used_validation_dates is None:
        sealed_used_dates: set[str] = set()
    elif isinstance(used_validation_dates, (set, list, tuple)):
        sealed_used_dates = set(used_validation_dates)
    else:
        raise SealedEvaluationError("Previously consumed validation dates must be a set/list/tuple.")
    offline_used_list, offline_memory_sha256 = _offline_development_consumption(
        offline_memory_path
    )
    offline_used_dates = set(offline_used_list)
    if sealed_used_dates.intersection(offline_used_dates):
        raise SealedEvaluationError(
            "Offline development dates overlap sealed holdout dates; research fails closed."
        )
    used_dates = sealed_used_dates | offline_used_dates
    for value in used_dates:
        if not isinstance(value, str):
            raise SealedEvaluationError("Previously consumed validation date is not text.")
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise SealedEvaluationError("Previously consumed validation date is invalid.") from None
        if parsed.isoformat() != value or parsed >= target_day:
            raise SealedEvaluationError("Previously consumed validation date is not a canonical pre-target date.")
    archive_dates = {row["date"] for row in rows}
    if not used_dates.issubset(archive_dates):
        raise SealedEvaluationError("Archive is missing one or more previously consumed validation dates.")

    by_regime: dict[str, list[dict[str, Any]]] = {"R60": []}
    for row in pre_target:
        day = date.fromisoformat(row["date"])
        if day < RESEARCH_SCOPE_START:
            raise SealedEvaluationError("Pre-target history contains a record before the active research scope.")
        if any(type(number) is not int or not 1 <= number <= 60 for number in row["numbers"]):
            raise SealedEvaluationError("Active-scope history contains a number outside the 1..60 pool.")
        by_regime["R60"].append(_copy_row(row))

    regimes: dict[str, dict[str, Any]] = {}
    all_discovery: list[dict[str, Any]] = []
    for regime, history in by_regime.items():
        pool_size = 60
        available_history = [row for row in history if row["date"] not in used_dates]
        previously_consumed_count = len(history) - len(available_history)
        regimes[regime] = {}
        if len(available_history) < DEFAULT_MIN_TRAIN_DRAWS + 2 * MIN_VALIDATION_DRAWS:
            discovery = list(available_history)
            validation = []
            reverse_validation = []
            status = "NOT_EVALUABLE_INSUFFICIENT_HISTORY"
            reason = (
                "The active-scope history does not have enough unconsumed rows for default training history plus "
                "two disjoint directional holdouts; the minimum was not lowered."
            )
        else:
            validation_count = min(
                DEFAULT_MAX_VALIDATION_DRAWS,
                (len(available_history) - DEFAULT_MIN_TRAIN_DRAWS) // 2,
            )
            if validation_count < MIN_VALIDATION_DRAWS:
                discovery = list(available_history)
                validation = []
                reverse_validation = []
                status = "NOT_EVALUABLE_INSUFFICIENT_HISTORY"
                reason = "Insufficient unconsumed rows remain after preserving the default training minimum and two disjoint holdouts."
            else:
                reverse_validation = available_history[:validation_count]
                discovery = available_history[validation_count:-validation_count]
                validation = available_history[-validation_count:]
                status = "READY"
                reason = None
        if discovery and reverse_validation and discovery[0]["date"] <= reverse_validation[-1]["date"]:
            raise SealedEvaluationError(regime + " discovery and reverse validation windows overlap.")
        if discovery and validation and discovery[-1]["date"] >= validation[0]["date"]:
            raise SealedEvaluationError(regime + " discovery and forward validation windows overlap.")
        if reverse_validation and validation and reverse_validation[-1]["date"] >= validation[0]["date"]:
            raise SealedEvaluationError(regime + " directional validation windows overlap.")
        if any(row["date"] >= target_date for row in discovery + validation + reverse_validation):
            raise SealedEvaluationError(regime + " includes the isolated target or a later date.")
        if any(row["date"] in used_dates for row in discovery + validation + reverse_validation):
            raise SealedEvaluationError(regime + " reused a previously consumed holdout date.")
        regimes[regime] = {
            "regime": regime,
            "number_pool_size": pool_size,
            "status": status,
            "reason": reason,
            "pre_target_draw_count": len(history),
            "available_pre_target_draw_count": len(available_history),
            "previously_consumed_draw_count": previously_consumed_count,
            "discovery_rows": discovery,
            "validation_rows": validation,
            "reverse_validation_rows": reverse_validation,
            "discovery_count": len(discovery),
            "validation_count": len(validation),
            "reverse_validation_count": len(reverse_validation),
            "discovery_start_date": discovery[0]["date"] if discovery else None,
            "discovery_end_date": discovery[-1]["date"] if discovery else None,
            "validation_start_date": validation[0]["date"] if validation else None,
            "validation_end_date": validation[-1]["date"] if validation else None,
            "reverse_validation_start_date": reverse_validation[0]["date"] if reverse_validation else None,
            "reverse_validation_end_date": reverse_validation[-1]["date"] if reverse_validation else None,
        }
        all_discovery.extend(discovery)

    all_discovery.sort(key=lambda row: row["date"])
    if any(row["date"] >= target_date for row in all_discovery):
        raise SealedEvaluationError("Discovery rows include the target or a later record.")
    if any(row["date"] in used_dates for row in all_discovery):
        raise SealedEvaluationError("Discovery rows include a previously consumed holdout date.")
    return {
        "schema_version": 1,
        "orchestrator_version": VERSION,
        "engine_version": ENGINE_VERSION,
        "target_date": target_date,
        "archive_sha256": archive_sha256,
        "archive_record_count": len(rows),
        "pre_target_draw_count": len(pre_target),
        "previously_consumed_validation_draw_count": len(used_dates),
        "previously_consumed_offline_development_draw_count": len(offline_used_dates),
        "offline_development_memory_sha256": offline_memory_sha256,
        "target_numbers_exposed": False,
        "target_in_discovery": False,
        "target_in_validation": False,
        "target_in_reverse_validation": False,
        "directional_validation_windows_disjoint": True,
        "discovery_rows": all_discovery,
        "discovery_count": len(all_discovery),
        "regimes": regimes,
    }


def split_archive(
    rows: list[dict[str, Any]],
    *,
    archive_sha256: str = "",
    used_validation_dates: set[str] | None = None,
    offline_memory_path: str | Path = "general_research_memory.json",
    partition_version: int = PARTITION_VERSION,
) -> dict[str, Any]:
    """Build a fresh adjacency-safe split or replay an immutable historical split."""
    if type(partition_version) is not int or partition_version not in {
        LEGACY_PARTITION_VERSION, PARTITION_VERSION
    }:
        raise SealedEvaluationError("Unsupported sealed partition version.")

    base = _split_archive_legacy(
        rows,
        archive_sha256=archive_sha256,
        used_validation_dates=used_validation_dates,
        offline_memory_path=offline_memory_path,
    )
    if partition_version == LEGACY_PARTITION_VERSION:
        base["partition_version"] = LEGACY_PARTITION_VERSION
        return base

    offline_used_list, offline_digest = _offline_development_consumption(
        offline_memory_path
    )
    if offline_digest != base["offline_development_memory_sha256"]:
        raise SealedEvaluationError(
            "Offline development memory changed while constructing the sealed partition."
        )
    used_dates = set(used_validation_dates or ()) | set(offline_used_list)
    target_date = base["target_date"]
    history = [row for row in rows if row["date"] < target_date]
    available = [_copy_row(row) for row in history if row["date"] not in used_dates]

    # Consumed rows split the canonical chronology into contiguous eligible blocks.
    # Never compact rows across a consumed date to manufacture a false neighbour.
    first_available = next(
        (index for index, row in enumerate(history) if row["date"] not in used_dates),
        None,
    )
    reverse_block = []
    if first_available is not None:
        for row in history[first_available:]:
            if row["date"] in used_dates:
                break
            reverse_block.append(_copy_row(row))

    last_available = next(
        (index for index in range(len(history) - 1, -1, -1)
         if history[index]["date"] not in used_dates),
        None,
    )
    forward_block = []
    if last_available is not None:
        for row in reversed(history[:last_available + 1]):
            if row["date"] in used_dates:
                break
            forward_block.append(_copy_row(row))
        forward_block.reverse()

    # Keep two exact canonical context rows outside each holdout. When both
    # directions share one contiguous block, the balanced budget prevents overlap.
    validation_budget = max(0, len(available) - DEFAULT_MIN_TRAIN_DRAWS)
    balanced_limit = min(DEFAULT_MAX_VALIDATION_DRAWS, validation_budget // 2)
    reverse_count = min(balanced_limit, max(0, len(reverse_block) - 2))
    forward_count = min(balanced_limit, max(0, len(forward_block) - 2))
    reverse_validation = reverse_block[:reverse_count]
    validation = forward_block[-forward_count:] if forward_count else []
    reverse_dates = {row["date"] for row in reverse_validation}
    forward_dates = {row["date"] for row in validation}
    if reverse_dates.intersection(forward_dates):
        raise SealedEvaluationError("Adjacency-safe directional validation windows overlap.")

    discovery = sorted(
        (row for row in available
         if row["date"] not in reverse_dates and row["date"] not in forward_dates),
        key=lambda row: row["date"],
    )
    ready = (
        len(reverse_validation) >= MIN_VALIDATION_DRAWS
        and len(validation) >= MIN_VALIDATION_DRAWS
        and len(discovery) >= DEFAULT_MIN_TRAIN_DRAWS
    )
    reason = None if ready else (
        "No adjacency-safe bidirectional partition can preserve 40 discovery rows, "
        "two independent validation targets per direction, and both exact canonical "
        "neighbours without reusing consumed dates."
    )
    all_roles = discovery + validation + reverse_validation
    role_dates = [row["date"] for row in all_roles]
    if len(role_dates) != len(set(role_dates)):
        raise SealedEvaluationError("Adjacency-safe partition assigned a canonical row more than once.")
    if any(row["date"] in used_dates for row in all_roles):
        raise SealedEvaluationError("Adjacency-safe partition reused a consumed date.")
    if any(row["date"] >= target_date for row in all_roles):
        raise SealedEvaluationError("Adjacency-safe partition includes the isolated target or a later date.")
    if reverse_validation and discovery and reverse_validation[-1]["date"] >= discovery[0]["date"]:
        raise SealedEvaluationError("Reverse validation and discovery chronology overlap.")
    if discovery and validation and discovery[-1]["date"] >= validation[0]["date"]:
        raise SealedEvaluationError("Discovery and forward validation chronology overlap.")

    regime = {
        "regime": "R60",
        "number_pool_size": 60,
        "status": "READY" if ready else "NOT_EVALUABLE_INSUFFICIENT_HISTORY",
        "reason": reason,
        "pre_target_draw_count": len(history),
        "available_pre_target_draw_count": len(available),
        "previously_consumed_draw_count": len(used_dates),
        "discovery_rows": discovery,
        "validation_rows": validation,
        "reverse_validation_rows": reverse_validation,
        "discovery_count": len(discovery),
        "validation_count": len(validation),
        "reverse_validation_count": len(reverse_validation),
        "discovery_start_date": discovery[0]["date"] if discovery else None,
        "discovery_end_date": discovery[-1]["date"] if discovery else None,
        "validation_start_date": validation[0]["date"] if validation else None,
        "validation_end_date": validation[-1]["date"] if validation else None,
        "reverse_validation_start_date": reverse_validation[0]["date"] if reverse_validation else None,
        "reverse_validation_end_date": reverse_validation[-1]["date"] if reverse_validation else None,
    }
    base.update({
        "partition_version": PARTITION_VERSION,
        "orchestrator_version": VERSION,
        "pre_target_draw_count": len(history),
        "previously_consumed_validation_draw_count": len(used_dates),
        "target_numbers_exposed": False,
        "target_in_discovery": False,
        "target_in_validation": False,
        "target_in_reverse_validation": False,
        "directional_validation_windows_disjoint": True,
        "discovery_rows": discovery,
        "discovery_count": len(discovery),
        "regimes": {"R60": regime},
    })
    return base


def _validate_dsl_v2_directional_context(
    candidate: dict[str, Any],
    split: dict[str, Any],
    canonical_pre_target_rows: list[dict[str, Any]],
    used_dates: set[str],
) -> None:
    """Reject a v2 claim before ledger mutation if its state neighbours are not canonical."""
    r60 = split["regimes"]["R60"]
    discovery = r60["discovery_rows"]
    forward_validation = r60["validation_rows"]
    reverse_validation = r60["reverse_validation_rows"]
    available_by_date = {
        row["date"]: row
        for row in discovery + forward_validation + reverse_validation
    }

    if candidate.get("execution_spec_dsl_version") == 2:
        for index, target in enumerate(forward_validation):
            expected_previous = [
                row for row in canonical_pre_target_rows if row["date"] < target["date"]
            ][-2:]
            allowed_training = discovery + forward_validation[:index]
            training_by_date = {row["date"]: row for row in allowed_training}
            if (
                len(expected_previous) != 2
                or any(row["date"] in used_dates for row in expected_previous)
                or any(training_by_date.get(row["date"]) != row for row in expected_previous)
            ):
                raise SealedEvaluationError(
                    "Forward DSL v2 target lacks its exact two immediately preceding unconsumed canonical states."
                )

    if candidate.get("reverse_execution_spec_dsl_version") == 2:
        for target in reverse_validation:
            expected_later = [
                row for row in canonical_pre_target_rows if row["date"] > target["date"]
            ][:2]
            if (
                len(expected_later) != 2
                or any(row["date"] in used_dates for row in expected_later)
                or any(available_by_date.get(row["date"]) != row for row in expected_later)
            ):
                raise SealedEvaluationError(
                    "Reverse DSL v2 target lacks its exact two immediately following unconsumed canonical states."
                )


def _ledger_empty() -> dict[str, Any]:
    return {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "purpose": "Append-only one-shot claims for non-overlapping sealed validation dates. No draw-number sets are stored.",
        "entries": [],
    }


def load_ledger(path: str | Path) -> dict[str, Any]:
    ledger_path = Path(path)
    if not ledger_path.exists():
        return _ledger_empty()
    payload, _ = _read_json(ledger_path, "Evaluation ledger")
    if (
        not isinstance(payload, dict)
        or set(payload) not in ({"schema_version", "purpose", "entries"}, {"schema_version", "purpose", "entries", "discovery_exposures"})
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != LEDGER_SCHEMA_VERSION
        or not isinstance(payload.get("purpose"), str)
        or not isinstance(payload.get("entries"), list)
        or len(payload["entries"]) > 10000
        or ("discovery_exposures" in payload and not isinstance(payload["discovery_exposures"], list))
    ):
        raise SealedEvaluationError("Evaluation ledger has an invalid schema; failing closed.")
    seen_claims: set[str] = set()
    used_dates: set[str] = set()
    for entry in payload["entries"]:
        if not isinstance(entry, dict):
            raise SealedEvaluationError("Evaluation ledger contains an invalid entry.")
        claim_id = entry.get("claim_id")
        dates = entry.get("validation_dates")
        state = entry.get("state")
        if (
            not isinstance(claim_id, str)
            or len(claim_id) != 64
            or any(ch not in "0123456789abcdef" for ch in claim_id)
            or claim_id in seen_claims
            or not isinstance(dates, list)
            or not dates
            or not isinstance(state, str)
            or state not in ALLOWED_CLAIM_STATES
        ):
            raise SealedEvaluationError("Evaluation ledger entry is malformed; failing closed.")
        partition_version = entry.get("partition_version", LEGACY_PARTITION_VERSION)
        if type(partition_version) is not int or partition_version not in {
            LEGACY_PARTITION_VERSION, PARTITION_VERSION, UNSEEN_PARTITION_VERSION
        }:
            raise SealedEvaluationError("Evaluation ledger partition version is invalid.")
        reverse_dates = entry.get("reverse_validation_dates", [])
        if not isinstance(reverse_dates, list):
            raise SealedEvaluationError("Evaluation ledger reverse validation dates are malformed.")
        parsed_dates = []
        for value in dates + reverse_dates:
            if not isinstance(value, str):
                raise SealedEvaluationError("Evaluation ledger contains an invalid validation date.")
            try:
                day = date.fromisoformat(value)
            except ValueError:
                raise SealedEvaluationError("Evaluation ledger contains an invalid validation date.") from None
            if day.isoformat() != value:
                raise SealedEvaluationError("Evaluation ledger contains a non-canonical date.")
            parsed_dates.append(value)
        parsed_forward = dates
        parsed_reverse = reverse_dates
        if parsed_forward != sorted(set(parsed_forward)) or parsed_reverse != sorted(set(parsed_reverse)):
            raise SealedEvaluationError("Evaluation ledger validation dates are not sorted and unique.")
        recorded_prior = entry.get("previously_consumed_validation_dates")
        if recorded_prior is not None:
            if (
                not isinstance(recorded_prior, list)
                or recorded_prior != sorted(set(recorded_prior))
                or set(recorded_prior) != used_dates
            ):
                raise SealedEvaluationError(
                    "Evaluation ledger prior-consumption snapshot does not match preceding claims."
                )
        if set(parsed_forward).intersection(parsed_reverse):
            raise SealedEvaluationError("Forward and reverse validation dates overlap within a ledger entry.")
        overlap = used_dates.intersection(parsed_dates)
        if overlap:
            raise SealedEvaluationError("Evaluation ledger already contains overlapping directional validation dates.")
        seen_claims.add(claim_id)
        used_dates.update(parsed_dates)
    exposures = payload.get("discovery_exposures", [])
    if not isinstance(exposures, list) or len(exposures) > 100000:
        raise SealedEvaluationError("Discovery exposure ledger exceeds its bounded schema.")
    seen_ids: set[str] = set()
    seen_package_ids: set[str] = set()
    seen_package_attempts: set[tuple[str, str]] = set()
    base_by_id: dict[str, dict[str, Any]] = {}
    states: dict[str, str] = {}
    transitions = {
        "RESERVED": {"EXPOSED", "CLAIMED", "RELEASED"},
        "EXPOSED": {"CLAIMED", "EVALUATED"},
        "CLAIMED": {"EVALUATED"},
        "EVALUATED": set(),
        "RELEASED": set(),
    }
    for item in exposures:
        if not isinstance(item, dict):
            raise SealedEvaluationError("Discovery exposure entry is malformed.")
        kind = item.get("kind")
        if kind == "RESEARCH_PACKAGE_STATUS":
            if set(item) != {
                "kind", "schema_version", "exposure_id", "package_id", "attempt_id",
                "prior_state", "state", "recorded_at_utc", "reason", "event_id",
            }:
                raise SealedEvaluationError("Research package status event keys are invalid.")
            base = base_by_id.get(item.get("exposure_id"))
            event = dict(item)
            event_id = event.pop("event_id", None)
            current = states.get(item.get("exposure_id"))
            recorded_at = item.get("recorded_at_utc")
            if not isinstance(recorded_at, str) or not recorded_at.endswith("+00:00"):
                raise SealedEvaluationError("Research package lifecycle timestamp must be UTC.")
            try:
                datetime.fromisoformat(recorded_at)
            except ValueError:
                raise SealedEvaluationError("Research package lifecycle timestamp is invalid.") from None
            reason = item.get("reason")
            if not isinstance(reason, str) or not reason or len(reason) > 120:
                raise SealedEvaluationError("Research package lifecycle reason is invalid.")
            if (base is None or item.get("schema_version") != 1
                or item.get("package_id") != base.get("package_id")
                or item.get("attempt_id") != base.get("attempt_id")
                or item.get("prior_state") != current
                or item.get("state") not in transitions.get(current, set())
                or not isinstance(event_id, str) or event_id != research_windows.digest(event)):
                raise SealedEvaluationError("Research package lifecycle event failed validation.")
            states[item["exposure_id"]] = item["state"]
            continue
        if kind not in {"LEGACY_DISCOVERY_RANGES", "RESEARCH_PACKAGE"}:
            raise SealedEvaluationError("Unknown discovery exposure record type.")
        exposure_id = item.get("exposure_id")
        hashed = dict(item); hashed["exposure_id"] = ""
        if (not isinstance(exposure_id, str) or len(exposure_id) != 64
            or exposure_id != research_windows.digest(hashed) or exposure_id in seen_ids):
            raise SealedEvaluationError("Discovery exposure identity/hash is invalid or duplicated.")
        seen_ids.add(exposure_id)
        if item.get("schema_version") != 1:
            raise SealedEvaluationError("Discovery exposure schema version is invalid.")
        if kind == "LEGACY_DISCOVERY_RANGES":
            if set(item) != {
                "kind", "schema_version", "exposure_id", "memory_sha256", "dates", "created_at_utc"
            }:
                raise SealedEvaluationError("Legacy discovery exposure keys are invalid.")
            dates = item.get("dates")
            if not isinstance(dates, list) or dates != sorted(set(dates)):
                raise SealedEvaluationError("Legacy exposure date list is malformed.")
            memory_sha = item.get("memory_sha256")
            if (
                not isinstance(memory_sha, str)
                or len(memory_sha) != 64
                or any(ch not in "0123456789abcdef" for ch in memory_sha)
            ):
                raise SealedEvaluationError("Legacy discovery exposure memory hash is invalid.")
            created_at = item.get("created_at_utc")
            if not isinstance(created_at, str) or not created_at.endswith("+00:00"):
                raise SealedEvaluationError("Legacy discovery exposure timestamp must be UTC.")
            try:
                datetime.fromisoformat(created_at)
            except ValueError:
                raise SealedEvaluationError("Legacy discovery exposure timestamp is invalid.") from None
            for value in dates:
                try: parsed = date.fromisoformat(value)
                except (TypeError, ValueError): raise SealedEvaluationError("Legacy exposure date is invalid.") from None
                if parsed.isoformat() != value: raise SealedEvaluationError("Legacy exposure date is non-canonical.")
        else:
            if set(item) != {
                "kind", "schema_version", "exposure_id", "attempt_id", "package_id",
                "package", "target_date", "archive_sha256", "archive_record_count",
                "partition_version", "package_sequence", "package_start_date",
                "package_end_date", "discovery_dates", "validation_dates",
                "reverse_validation_dates", "dates", "previously_unavailable_dates_sha256",
                "created_at_utc", "target_numbers_exposed", "state",
            }:
                raise SealedEvaluationError("Research package exposure keys are invalid.")
            package = item.get("package")
            package_attempt = item.get("attempt_id")
            created_at = item.get("created_at_utc")
            if (
                not isinstance(package_attempt, str) or len(package_attempt) != 64
                or any(ch not in "0123456789abcdef" for ch in package_attempt)
                or not isinstance(created_at, str) or not created_at.endswith("+00:00")
                or not isinstance(package, dict)
                or package.get("partition_version") != UNSEEN_PARTITION_VERSION
                or package.get("package_id") != item.get("package_id")
                or package.get("package_id") != research_windows.digest(
                    {k: v for k, v in package.items() if k != "package_id"}
                )
                or item.get("state") != "RESERVED"
                or item.get("target_numbers_exposed") is not False
                or item.get("dates") != package.get("dates")
                or item.get("discovery_dates") != package.get("discovery_dates")
                or item.get("validation_dates") != package.get("validation_dates")
                or item.get("reverse_validation_dates") != package.get("reverse_validation_dates")
                or item.get("previously_unavailable_dates_sha256")
                   != package.get("previously_unavailable_dates_sha256")
                or item.get("target_date") != package.get("target_date")
                or item.get("archive_sha256") != package.get("archive_sha256")
                or item.get("archive_record_count") != package.get("archive_record_count")
                or item.get("package_sequence") != package.get("package_sequence")
                or item.get("package_start_date") != package.get("package_start_date")
                or item.get("package_end_date") != package.get("package_end_date")
                or item.get("partition_version") != UNSEEN_PARTITION_VERSION
            ):
                raise SealedEvaluationError("Research package exposure record is malformed.")
            try:
                datetime.fromisoformat(created_at)
            except ValueError:
                raise SealedEvaluationError("Research package exposure timestamp is invalid.") from None
            if item["package_id"] in seen_package_ids:
                raise SealedEvaluationError("Duplicate V3 package ID in discovery exposure ledger.")
            seen_package_ids.add(item["package_id"])
            package_attempt_key = (item["package_id"], package_attempt)
            if package_attempt_key in seen_package_attempts:
                raise SealedEvaluationError("Duplicate V3 package reservation for one scheduler attempt.")
            seen_package_attempts.add(package_attempt_key)
            base_by_id[exposure_id] = item
            states[exposure_id] = "RESERVED"
    if len(exposures) > 100000:
        raise SealedEvaluationError("Discovery exposure ledger exceeds its bounded append-only size.")
    for entry in payload["entries"]:
        if entry.get("partition_version", LEGACY_PARTITION_VERSION) != UNSEEN_PARTITION_VERSION:
            continue
        package_id = entry.get("research_package_id")
        attempt_id = entry.get("scheduler_attempt_id")
        matches = [
            (exposure_id, exposure)
            for exposure_id, exposure in base_by_id.items()
            if exposure.get("package_id") == package_id
            and exposure.get("attempt_id") == attempt_id
        ]
        if len(matches) != 1:
            raise SealedEvaluationError("V3 claim does not have one exact package exposure reservation.")
        exposure_id, exposure = matches[0]
        if (
            entry.get("previously_unavailable_dates_sha256")
            != exposure.get("previously_unavailable_dates_sha256")
            or states.get(exposure_id) != entry.get("state")
            or exposure.get("archive_sha256") != entry.get("archive_sha256")
            or exposure.get("target_date") != entry.get("target_date")
        ):
            raise SealedEvaluationError("V3 claim and package exposure lifecycle are inconsistent.")
    for exposure_id, exposure in base_by_id.items():
        state = states.get(exposure_id)
        if state in {"CLAIMED", "EVALUATED"}:
            matching_claims = [
                entry for entry in payload["entries"]
                if entry.get("partition_version") == UNSEEN_PARTITION_VERSION
                and entry.get("research_package_id") == exposure.get("package_id")
                and entry.get("scheduler_attempt_id") == exposure.get("attempt_id")
                and entry.get("state") == state
            ]
            if len(matching_claims) != 1:
                raise SealedEvaluationError("Claimed/evaluated package exposure lacks its exact sealed-ledger entry.")
    return payload


def public_plan(split: dict[str, Any]) -> dict[str, Any]:
    """Return partition metadata only; never include draw-number sets."""
    regimes = {}
    for name, item in split["regimes"].items():
        regimes[name] = {
            key: item[key]
            for key in (
                "regime", "number_pool_size", "status", "reason",
                "pre_target_draw_count", "available_pre_target_draw_count",
                "previously_consumed_draw_count", "discovery_count", "validation_count",
                "reverse_validation_count",
                "discovery_start_date", "discovery_end_date",
                "validation_start_date", "validation_end_date",
                "reverse_validation_start_date", "reverse_validation_end_date",
            )
        }
    return {
        key: split[key]
        for key in (
            "schema_version", "partition_version", "orchestrator_version", "engine_version",
            "target_date", "archive_sha256", "archive_record_count",
            "pre_target_draw_count", "previously_consumed_validation_draw_count",
            "previously_consumed_offline_development_draw_count",
            "offline_development_memory_sha256",
            "target_numbers_exposed", "target_in_discovery", "target_in_validation",
            "target_in_reverse_validation", "directional_validation_windows_disjoint",
            "discovery_count",
        )
    } | {"regimes": regimes} | (
        {"research_package": split["research_package"]}
        if isinstance(split.get("research_package"), dict) else {}
    ) | (
        {"scheduler_attempt_id": split["scheduler_attempt_id"]}
        if isinstance(split.get("scheduler_attempt_id"), str) else {}
    )


def _used_validation_dates(ledger: dict[str, Any]) -> set[str]:
    return {
        day
        for entry in ledger["entries"]
        for day in entry["validation_dates"] + entry.get("reverse_validation_dates", [])
    }


def get_used_validation_dates(ledger: dict[str, Any]) -> set[str]:
    """Return all dates reserved or permanently consumed by directional holdouts."""
    return set(_used_validation_dates(ledger))


def _build_v3_package_split(archive, ledger, *, offline_memory_path, package_id=None, attempt_id=None, allow_evaluated_recovery=False):
    """Build or replay one exact package from dates not previously exposed."""
    try:
        from general_research_memory import load_memory
        memory = load_memory(offline_memory_path)
        offline_dates, offline_digest = _offline_development_consumption(offline_memory_path)
        reservation_state = None
        if package_id is not None:
            if not isinstance(attempt_id, str) or len(attempt_id) != 64 or any(
                ch not in "0123456789abcdef" for ch in attempt_id
            ):
                raise research_windows.ResearchWindowError(
                    "A V3 package replay requires its exact original exposure-attempt ID."
                )
            exposure = research_windows.package_exposure_for_attempt(
                ledger, package_id=package_id, attempt_id=attempt_id
            )
            if exposure is None:
                raise research_windows.ResearchWindowError("Exact package reservation not found.")
            package = research_windows.package_from_exposure(exposure)
            reservation_state = research_windows.package_exposure_status(ledger, exposure)
            research_windows.validate_package(
                package, archive["rows"], archive_sha256=archive["archive_sha256"],
                unavailable=set(package.get("previously_unavailable_dates", [])),
            )
            current_unavailable = research_windows.unavailable_dates(
                archive["rows"], ledger=ledger, memory=memory, attempts=[]
            )
            package_dates = set(package["dates"])
            if reservation_state == "RELEASED":
                # No provider ever saw this package. Treat the old reservation
                # as released, then mint a fresh package ID from the current ledger.
                exposure = None
                package_id = None
            elif reservation_state == "RESERVED":
                current_without_own = current_unavailable - package_dates
                if current_without_own != set(package["previously_unavailable_dates"]):
                    raise research_windows.ResearchWindowError(
                        "Reserved package prior-unavailable snapshot changed before discovery."
                    )
            elif reservation_state == "EXPOSED":
                own_discovery = set(exposure["discovery_dates"])
                if package_dates.intersection(current_unavailable - own_discovery):
                    raise research_windows.ResearchWindowError(
                        "An exposed package now overlaps another consumed date or claim."
                    )
            elif reservation_state == "EVALUATED":
                if allow_evaluated_recovery is not True:
                    raise research_windows.ResearchWindowError(
                        "An EVALUATED package can only be reopened for exact local-target-score recovery."
                    )
                current_without_own = current_unavailable - package_dates
                if current_without_own != set(package.get("previously_unavailable_dates", [])):
                    raise research_windows.ResearchWindowError(
                        "Evaluated package prior-unavailable snapshot changed before local-score recovery."
                    )
                matching_claims = [
                    entry for entry in ledger.get("entries", [])
                    if entry.get("partition_version") == UNSEEN_PARTITION_VERSION
                    and entry.get("research_package_id") == package_id
                    and entry.get("scheduler_attempt_id") == attempt_id
                    and entry.get("state") == "EVALUATED"
                    and entry.get("target_date") == package.get("target_date")
                    and entry.get("archive_sha256") == package.get("archive_sha256")
                    and entry.get("archive_record_count") == package.get("archive_record_count")
                    and entry.get("validation_dates") == package.get("validation_dates")
                    and entry.get("reverse_validation_dates") == package.get("reverse_validation_dates")
                    and entry.get("r60_discovery_count") == package.get("discovery_count")
                    and entry.get("discovery_start_date") == package.get("discovery_start_date")
                    and entry.get("discovery_end_date") == package.get("discovery_end_date")
                ]
                if len(matching_claims) != 1:
                    raise research_windows.ResearchWindowError(
                        "Evaluated package does not have one exact completed sealed claim."
                    )
            else:
                raise research_windows.ResearchWindowError(
                    "A V3 package can only be resumed while reserved, discovery-exposed, or explicitly recovered after evaluation."
                )
        if package_id is None:
            unavailable = research_windows.unavailable_dates(
                archive["rows"], ledger=ledger, memory=memory, attempts=[]
            )
            sequences = [
                e.get("package_sequence", 0) for e in ledger.get("discovery_exposures", [])
                if isinstance(e, dict) and e.get("kind") == "RESEARCH_PACKAGE"
                and e.get("target_date") == archive["last_date"]
                and e.get("archive_sha256") == archive["archive_sha256"]
            ]
            packages = research_windows.build_research_packages(
                archive["rows"], archive_sha256=archive["archive_sha256"],
                unavailable=unavailable, sequence_start=max(sequences, default=0) + 1,
                offline_memory_sha256=offline_digest,
            )
            if not packages:
                raise research_windows.ResearchWindowError("No genuinely unseen contiguous package remains.")
            package, exposure = packages[0], None
        if package.get("offline_development_memory_sha256") != offline_digest:
            raise research_windows.ResearchWindowError("Offline development memory digest changed.")
        split = research_windows.split_from_package(
            archive["rows"], package, archive_sha256=archive["archive_sha256"]
        )
        split["evaluated_package_recovery"] = reservation_state == "EVALUATED"
        split["scheduler_attempt_id"] = (
            attempt_id if exposure is not None else None
        )
        split["previously_consumed_validation_draw_count"] = len(_used_validation_dates(ledger))
        split["previously_consumed_offline_development_draw_count"] = len(offline_dates)
        return split, exposure
    except Exception as exc:
        raise SealedEvaluationError(
            "V3 unseen-package preflight failed closed (" + type(exc).__name__ + "): " + str(exc)
        ) from None


def _pending_claim_with_split(
    archive: dict[str, Any],
    ledger: dict[str, Any],
    *,
    offline_memory_path: str | Path = "general_research_memory.json",
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return the exact active claim and its original partition for safe resumption."""
    matches = [
        (index, entry)
        for index, entry in enumerate(ledger["entries"])
        if entry.get("state") == "CLAIMED"
        and entry.get("target_date") == archive.get("last_date")
        and entry.get("archive_sha256") == archive.get("archive_sha256")
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise SealedEvaluationError("More than one pending sealed claim matches the active archive.")
    entry_index, entry = matches[0]
    if entry_index != len(ledger["entries"]) - 1:
        raise SealedEvaluationError("A pending sealed claim is not the final evaluation-ledger entry.")

    expected_prior_dates = sorted({
        day
        for prior in ledger["entries"][:entry_index]
        for day in prior["validation_dates"] + prior.get("reverse_validation_dates", [])
    })
    if entry.get("previously_consumed_validation_dates") != expected_prior_dates:
        raise SealedEvaluationError("Pending claim prior-consumption snapshot does not match the append-only ledger.")
    if (
        not isinstance(entry.get("claim_id"), str)
        or len(entry["claim_id"]) != 64
        or any(ch not in "0123456789abcdef" for ch in entry["claim_id"])
        or not isinstance(entry.get("archive_snapshot_path"), str)
        or not entry["archive_snapshot_path"]
        or entry.get("archive_record_count") != archive.get("record_count")
    ):
        raise SealedEvaluationError("Pending sealed claim identity or archive binding is invalid.")

    snapshot = load_archive(Path(entry["archive_snapshot_path"]))
    if (
        snapshot["archive_sha256"] != archive["archive_sha256"]
        or snapshot["last_date"] != archive["last_date"]
        or snapshot["record_count"] != archive["record_count"]
        or snapshot["rows"] != archive["rows"]
    ):
        raise SealedEvaluationError("Pending claim immutable snapshot does not match the active FIFO archive.")

    partition_version = entry.get("partition_version", LEGACY_PARTITION_VERSION)
    if partition_version == UNSEEN_PARTITION_VERSION:
        exposure = research_windows.package_exposure_for_attempt(
            ledger, package_id=entry.get("research_package_id"),
            attempt_id=entry.get("scheduler_attempt_id"),
        )
        if exposure is None:
            raise SealedEvaluationError("Pending V3 claim has no exact package reservation.")
        package = research_windows.package_from_exposure(exposure)
        split = research_windows.split_from_package(
            archive["rows"], package, archive_sha256=archive["archive_sha256"]
        )
        split["scheduler_attempt_id"] = entry.get("scheduler_attempt_id")
        split["previously_consumed_validation_draw_count"] = len(expected_prior_dates)
        _, offline_digest = _offline_development_consumption(offline_memory_path)
        if package.get("offline_development_memory_sha256") != offline_digest:
            raise SealedEvaluationError("Offline development memory changed after pending V3 claim.")
        split["previously_consumed_offline_development_draw_count"] = entry.get(
            "previously_consumed_offline_development_draw_count", 0
        )
    else:
        split = split_archive(
            archive["rows"], archive_sha256=archive["archive_sha256"],
            used_validation_dates=set(expected_prior_dates), offline_memory_path=offline_memory_path,
            partition_version=partition_version,
        )
    if split["target_date"] != entry.get("target_date"):
        raise SealedEvaluationError("Pending claim target does not match its original chronological partition.")
    r60 = split["regimes"]["R60"]
    forward_dates = [row["date"] for row in r60["validation_rows"]]
    reverse_dates = [row["date"] for row in r60["reverse_validation_rows"]]
    if (
        forward_dates != entry.get("validation_dates")
        or reverse_dates != entry.get("reverse_validation_dates")
        or len(forward_dates) != entry.get("validation_count")
        or len(reverse_dates) != entry.get("reverse_validation_count")
        or (forward_dates and forward_dates[0] != entry.get("validation_start_date"))
        or (forward_dates and forward_dates[-1] != entry.get("validation_end_date"))
        or (reverse_dates and reverse_dates[0] != entry.get("reverse_validation_start_date"))
        or (reverse_dates and reverse_dates[-1] != entry.get("reverse_validation_end_date"))
        or r60["discovery_count"] != entry.get("r60_discovery_count")
    ):
        raise SealedEvaluationError("Pending claim holdout or discovery partition changed; resumption is forbidden.")

    if (
        "offline_development_memory_sha256" not in entry
        or "previously_consumed_offline_development_draw_count" not in entry
    ):
        empty_digest = _sha256_bytes(_canonical_json([]))
        if (
            split["previously_consumed_offline_development_draw_count"] != 0
            or split["offline_development_memory_sha256"] != empty_digest
        ):
            raise SealedEvaluationError("Legacy pending claim has no matching offline-consumption snapshot.")
    elif (
        entry["offline_development_memory_sha256"] != split["offline_development_memory_sha256"]
        or entry["previously_consumed_offline_development_draw_count"]
        != split["previously_consumed_offline_development_draw_count"]
    ):
        raise SealedEvaluationError("Offline development memory changed after the pending sealed claim.")

    forward_dsl_version = entry.get("execution_spec_dsl_version", 1)
    reverse_dsl_version = entry.get("reverse_execution_spec_dsl_version", 1)
    if forward_dsl_version == 2 or reverse_dsl_version == 2:
        offline_dates, offline_digest = _offline_development_consumption(offline_memory_path)
        if offline_digest != split["offline_development_memory_sha256"]:
            raise SealedEvaluationError("Offline development memory changed during pending-claim replay.")
        canonical_pre_target = [
            row for row in archive["rows"] if row["date"] < split["target_date"]
        ]
        _validate_dsl_v2_directional_context(
            {
                "execution_spec_dsl_version": forward_dsl_version,
                "reverse_execution_spec_dsl_version": reverse_dsl_version,
            },
            split,
            canonical_pre_target,
            (set(split.get("research_package", {}).get("previously_unavailable_dates", []))
             if partition_version == UNSEEN_PARTITION_VERSION
             else set(expected_prior_dates) | set(offline_dates)),
        )

    for field in ("hypothesis_id", "spec_sha256", "reverse_hypothesis_id", "reverse_spec_sha256"):
        value = entry.get(field)
        if not isinstance(value, str) or not value:
            raise SealedEvaluationError("Pending claim frozen candidate identity is invalid.")
        if field.endswith("sha256") and (
            len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value)
        ):
            raise SealedEvaluationError("Pending claim frozen specification hash is invalid.")
    return entry, split


def pending_claim_for_archive(
    archive_path: str | Path,
    ledger_path: str | Path,
    *,
    offline_memory_path: str | Path = "general_research_memory.json",
) -> dict[str, Any] | None:
    """Return a validated pending claim for the exact active archive."""
    archive = load_archive(archive_path)
    ledger = load_ledger(ledger_path)
    pending = _pending_claim_with_split(
        archive, ledger, offline_memory_path=offline_memory_path
    )
    return None if pending is None else dict(pending[0])


def split_for_research(
    archive_path: str | Path,
    ledger_path: str | Path,
    *,
    offline_memory_path: str | Path = "general_research_memory.json",
    package_id: str | None = None,
    attempt_id: str | None = None,
) -> dict[str, Any]:
    """Use fresh dates normally, or the original split for an exact pending claim."""
    archive = load_archive(archive_path)
    ledger = load_ledger(ledger_path)
    pending = _pending_claim_with_split(
        archive, ledger, offline_memory_path=offline_memory_path
    )
    if pending is not None:
        if package_id is not None and pending[0].get("research_package_id") != package_id:
            raise SealedEvaluationError("Requested package differs from pending claim.")
        return pending[1]
    if package_id is not None:
        split, _ = _build_v3_package_split(
            archive, ledger, offline_memory_path=offline_memory_path,
            package_id=package_id, attempt_id=attempt_id,
        )
        return split
    return split_archive(
        archive["rows"], archive_sha256=archive["archive_sha256"],
        used_validation_dates=_used_validation_dates(ledger), offline_memory_path=offline_memory_path,
    )


def pending_claim_matches_candidates(
    entry: dict[str, Any],
    forward_candidate: dict[str, Any] | None,
    reverse_candidate: dict[str, Any] | None,
) -> bool:
    """Require the exact two directional definitions frozen by a pending claim."""
    if not isinstance(entry, dict) or not isinstance(forward_candidate, dict) or not isinstance(reverse_candidate, dict):
        return False
    forward_proposal = forward_candidate.get("proposal")
    reverse_proposal = reverse_candidate.get("proposal")
    forward_spec = forward_candidate.get("execution_spec")
    reverse_spec = reverse_candidate.get("execution_spec")
    if not all(isinstance(item, dict) for item in (
        forward_proposal, reverse_proposal, forward_spec, reverse_spec
    )):
        return False
    return (
        forward_proposal.get("hypothesis_id") == entry.get("hypothesis_id")
        and reverse_proposal.get("hypothesis_id") == entry.get("reverse_hypothesis_id")
        and _sha256_bytes(_canonical_json(forward_spec)) == entry.get("spec_sha256")
        and _sha256_bytes(_canonical_json(reverse_spec)) == entry.get("reverse_spec_sha256")
    )


def preflight(
    archive_path: str | Path,
    ledger_path: str | Path,
    *,
    offline_memory_path: str | Path = "general_research_memory.json",
    partition_version: int = PARTITION_VERSION,
    package_id: str | None = None,
    attempt_id: str | None = None,
    allow_evaluated_package_recovery: bool = False,
) -> dict[str, Any]:
    """Fail before any hosted-model request if no fresh validation interval exists."""
    if type(allow_evaluated_package_recovery) is not bool:
        raise SealedEvaluationError("Evaluated-package recovery selector must be a boolean.")
    archive = load_archive(archive_path)
    ledger = load_ledger(ledger_path)
    pending = _pending_claim_with_split(
        archive, ledger, offline_memory_path=offline_memory_path
    )
    if pending is not None:
        pending_entry, split = pending
        if package_id is not None and pending_entry.get("research_package_id") != package_id:
            raise SealedEvaluationError("Requested package differs from pending claim.")
        used_dates = set(pending_entry["previously_consumed_validation_dates"])
    else:
        pending_entry = None
        used_dates = _used_validation_dates(ledger)
        if partition_version == UNSEEN_PARTITION_VERSION:
            split, _ = _build_v3_package_split(
                archive, ledger, offline_memory_path=offline_memory_path,
                package_id=package_id, attempt_id=attempt_id,
                allow_evaluated_recovery=allow_evaluated_package_recovery,
            )
        else:
            split = split_archive(
                archive["rows"], archive_sha256=archive["archive_sha256"],
                used_validation_dates=used_dates, offline_memory_path=offline_memory_path,
                partition_version=partition_version,
            )
    if split.get("partition_version") in {PARTITION_VERSION, UNSEEN_PARTITION_VERSION}:
        offline_dates, offline_digest = _offline_development_consumption(offline_memory_path)
        if offline_digest != split["offline_development_memory_sha256"]:
            raise SealedEvaluationError("Offline development memory changed during sealed preflight.")
        canonical_pre_target = [
            row for row in archive["rows"] if row["date"] < split["target_date"]
        ]
        _validate_dsl_v2_directional_context(
            {"execution_spec_dsl_version": 2, "reverse_execution_spec_dsl_version": 2},
            split,
            canonical_pre_target,
            (set(split.get("research_package", {}).get("previously_unavailable_dates", []))
             if split.get("partition_version") == UNSEEN_PARTITION_VERSION
             else set(used_dates) | set(offline_dates)),
        )
    r60 = split["regimes"]["R60"]
    validation_dates = [row["date"] for row in r60["validation_rows"]]
    reverse_validation_dates = [row["date"] for row in r60["reverse_validation_rows"]]
    if len(validation_dates) < MIN_VALIDATION_DRAWS:
        raise SealedEvaluationError(
            "R60 has no sufficiently sized fresh forward sealed validation interval; "
            "preserve the minimum history and wait for more unconsumed draws."
        )
    if len(reverse_validation_dates) < MIN_VALIDATION_DRAWS:
        raise SealedEvaluationError(
            "R60 has no sufficiently sized fresh reverse sealed validation interval; "
            "preserve the minimum history and wait for more unconsumed draws."
        )
    if set(validation_dates).intersection(reverse_validation_dates):
        raise SealedEvaluationError("Forward and reverse validation intervals overlap.")
    overlap_guard_dates = set(used_dates)
    evaluated_recovery = split.get("evaluated_package_recovery") is True
    if evaluated_recovery:
        # This exact package's holdouts were already scored by its one EVALUATED
        # claim. Local target-score recovery must reuse the frozen claim without
        # treating its own dates as a second holdout reservation. No other
        # consumed dates are exempted from the normal overlap guard.
        own_evaluated_holdouts = set(validation_dates) | set(reverse_validation_dates)
        if not own_evaluated_holdouts or not own_evaluated_holdouts.issubset(used_dates):
            raise SealedEvaluationError(
                "Evaluated-package recovery does not match the holdouts consumed by its exact claim."
            )
        overlap_guard_dates.difference_update(own_evaluated_holdouts)
    overlap = sorted(
        (set(validation_dates) | set(reverse_validation_dates)).intersection(overlap_guard_dates)
    )
    if overlap:
        raise SealedEvaluationError(
            "Current sealed validation window overlaps already-consumed dates; stop before any model request."
        )
    if len(r60["discovery_rows"]) < DEFAULT_MIN_TRAIN_DRAWS:
        raise SealedEvaluationError("R60 discovery history is shorter than the default minimum training window.")
    result = public_plan(split)
    result.update({
        "status": "READY",
        "partition_version": split.get("partition_version", LEGACY_PARTITION_VERSION),
        "ledger_entry_count": len(ledger["entries"]),
        "validation_dates_previously_consumed": evaluated_recovery,
        "previously_consumed_sealed_validation_draw_count": len(used_dates),
        "previously_consumed_validation_draw_count": split["previously_consumed_validation_draw_count"],
        "previously_consumed_offline_development_draw_count": split["previously_consumed_offline_development_draw_count"],
        "offline_development_memory_sha256": split["offline_development_memory_sha256"],
        "forward_validation_dates": validation_dates,
        "reverse_validation_dates": reverse_validation_dates,
        "hosted_model_request_performed": False,
        "real_archive_evaluation_performed": False,
        "pending_claim_resume_available": pending_entry is not None,
        "evaluated_package_recovery": split.get("evaluated_package_recovery") is True,
        "pending_claim_id": pending_entry["claim_id"] if pending_entry is not None else None,
        "research_package_id": split.get("research_package", {}).get("package_id"),
        "research_package": split.get("research_package"),
        "scheduler_attempt_id": split.get("scheduler_attempt_id", attempt_id),
    })
    return result


def _select_one_frozen_candidate(
    proposals_path: str | Path,
    specs_path: str | Path,
) -> dict[str, Any] | None:
    """Freeze the first eligible discovery proposal independently in each direction."""
    proposals, _ = _read_json(Path(proposals_path), "Proposal document")
    specs_doc, _ = _read_json(Path(specs_path), "Execution specification document")
    if (
        not isinstance(proposals, dict)
        or proposals.get("schema_version") != 2
        or proposals.get("status") != "VALID_PROPOSALS"
        or not isinstance(proposals.get("proposals"), list)
        or not proposals["proposals"]
    ):
        return None
    if (
        not isinstance(specs_doc, dict)
        or specs_doc.get("status") != "VALIDATED_SPECS"
        or specs_doc.get("dsl_validated") is not True
        or specs_doc.get("synthetic_tests_passed") is not True
        or not isinstance(specs_doc.get("specifications"), list)
        or not 1 <= len(specs_doc["specifications"]) <= 2
    ):
        return None

    items = proposals["proposals"]
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("hypothesis_id"), str)
        or item.get("discovery_direction") not in {"forward", "reverse"}
        for item in items
    ):
        return None
    forward_proposal = next(
        (item for item in items if item["discovery_direction"] == "forward"),
        None,
    )
    reverse_proposal = next(
        (item for item in items if item["discovery_direction"] == "reverse"),
        None,
    )
    if forward_proposal is None or reverse_proposal is None:
        return None

    specs_by_id: dict[str, dict[str, Any]] = {}
    for item in specs_doc["specifications"]:
        if not isinstance(item, dict) or not isinstance(item.get("hypothesis_id"), str):
            return None
        hypothesis_id = item["hypothesis_id"]
        if hypothesis_id in specs_by_id:
            return None
        specs_by_id[hypothesis_id] = item

    forward_id = forward_proposal["hypothesis_id"]
    reverse_id = reverse_proposal["hypothesis_id"]
    required_ids = {forward_id, reverse_id}
    if set(specs_by_id) != required_ids:
        return None

    def freeze(item: dict[str, Any], expected_direction: str) -> dict[str, Any]:
        if item.get("dsl_validated") is not True or item.get("synthetic_tests_passed") is not True:
            raise SealedEvaluationError("A directional specification did not pass DSL/synthetic validation.")
        spec = validate_any_execution_spec(item.get("execution_spec"))
        spec_direction = execution_spec_direction(spec)
        if spec_direction is not None and spec_direction != expected_direction:
            raise SealedEvaluationError("DSL v2 direction does not match its isolated discovery direction.")
        synthetic = run_any_synthetic_contract_tests(spec)
        if synthetic.get("status") != "PASS" or synthetic.get("real_archive_used") is not False:
            raise SealedEvaluationError("A directional specification failed its synthetic-only contract test.")
        return {
            "execution_spec": spec,
            "dsl_version": execution_spec_version(spec),
            "spec_sha256": _sha256_bytes(_canonical_json(spec)),
            "synthetic_test_summary": synthetic,
        }

    forward = freeze(specs_by_id[forward_id], "forward")
    reverse = freeze(specs_by_id[reverse_id], "reverse")
    return {
        "hypothesis_id": forward_id,
        "discovery_direction": forward_proposal["discovery_direction"],
        **forward,
        "reverse_hypothesis_id": reverse_id,
        "reverse_discovery_direction": reverse_proposal["discovery_direction"],
        "reverse_execution_spec": reverse["execution_spec"],
        "reverse_spec_sha256": reverse["spec_sha256"],
        "execution_spec_dsl_version": forward["dsl_version"],
        "reverse_execution_spec_dsl_version": reverse["dsl_version"],
        "reverse_synthetic_test_summary": reverse["synthetic_test_summary"],
    }

def _write_json_atomic(path: Path, payload: Any) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_bytes(raw)
        os.replace(temp, path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass
    return raw


def create_claim(
    archive_path: str | Path,
    ledger_path: str | Path,
    proposals_path: str | Path,
    specs_path: str | Path,
    *,
    snapshot_directory: str | Path = SNAPSHOT_DIRECTORY,
    offline_memory_path: str | Path = "general_research_memory.json",
    sealed_plan_path: str | Path | None = None,
    research_package_id: str | None = None,
    scheduler_attempt_id: str | None = None,
) -> dict[str, Any]:
    """Claim a fresh interval or resume the exact existing claim for this frozen candidate."""
    candidate = _select_one_frozen_candidate(proposals_path, specs_path)
    archive = load_archive(archive_path)
    ledger = load_ledger(ledger_path)
    pending = _pending_claim_with_split(
        archive, ledger, offline_memory_path=offline_memory_path
    )
    if pending is not None:
        entry, split = pending
        if research_package_id is not None and entry.get("research_package_id") != research_package_id:
            raise SealedEvaluationError("Requested package differs from exact pending claim.")
        if candidate is None:
            raise SealedEvaluationError("A pending sealed claim exists but its frozen candidate pair is unavailable.")
        if (
            candidate["hypothesis_id"] != entry["hypothesis_id"]
            or candidate["spec_sha256"] != entry["spec_sha256"]
            or candidate["reverse_hypothesis_id"] != entry["reverse_hypothesis_id"]
            or candidate["reverse_spec_sha256"] != entry["reverse_spec_sha256"]
        ):
            raise SealedEvaluationError("A pending sealed claim can only resume with its exact original directional candidates.")
        if sealed_plan_path is not None:
            _write_json_atomic(Path(sealed_plan_path), public_plan(split))
        return {
            "status": "RESUMED_CLAIM",
            "claim_created": True,
            "claim_resumed": True,
            "claim_id": entry["claim_id"],
            "research_package_id": entry.get("research_package_id"),
            "scheduler_attempt_id": entry.get("scheduler_attempt_id"),
            "target_date": entry["target_date"],
            "archive_sha256": entry["archive_sha256"],
            "hypothesis_id": entry["hypothesis_id"],
            "spec_sha256": entry["spec_sha256"],
            "reverse_hypothesis_id": entry["reverse_hypothesis_id"],
            "reverse_spec_sha256": entry["reverse_spec_sha256"],
            "validation_start_date": entry["validation_start_date"],
            "validation_end_date": entry["validation_end_date"],
            "validation_draw_count": entry["validation_count"],
            "reverse_validation_start_date": entry["reverse_validation_start_date"],
            "reverse_validation_end_date": entry["reverse_validation_end_date"],
            "reverse_validation_draw_count": entry["reverse_validation_count"],
            "archive_snapshot_path": entry["archive_snapshot_path"],
            "ledger_entry_must_be_persisted_before_evaluation": True,
            "current_target_included": False,
            "current_target_numbers_exposed": False,
        }
    if candidate is None:
        return {
            "status": "SKIPPED_NO_SINGLE_FROZEN_CANDIDATE",
            "claim_created": False,
            "claim_resumed": False,
            "real_archive_evaluation_performed": False,
            "reason": "Need one DSL-valid specification for the first eligible proposal in each direction, with no alternatives.",
        }
    completed_match = next((
        item for item in ledger["entries"]
        if item.get("state") == "EVALUATED"
        and item.get("target_date") == archive["last_date"]
        and item.get("archive_sha256") == archive["archive_sha256"]
        and item.get("hypothesis_id") == candidate["hypothesis_id"]
        and item.get("spec_sha256") == candidate["spec_sha256"]
        and item.get("reverse_hypothesis_id") == candidate["reverse_hypothesis_id"]
        and item.get("reverse_spec_sha256") == candidate["reverse_spec_sha256"]
        and (
            (research_package_id is None and item.get("partition_version") != UNSEEN_PARTITION_VERSION)
            or (
                research_package_id is not None
                and item.get("research_package_id") == research_package_id
            )
        )
    ), None)
    if completed_match is not None:
        # Recovery path: the sealed historical evaluation is already complete,
        # but a later local hidden-target scoring step may have been interrupted.
        # Reuse the exact EVALUATED claim, never score its holdouts a second time.
        return {
            "status": "ALREADY_EVALUATED_CLAIM",
            "claim_created": False,
            "claim_resumed": False,
            "claim_reused": True,
            "claim_id": completed_match["claim_id"],
            "target_date": completed_match["target_date"],
            "archive_sha256": completed_match["archive_sha256"],
            "archive_record_count": completed_match["archive_record_count"],
            "hypothesis_id": completed_match["hypothesis_id"],
            "spec_sha256": completed_match["spec_sha256"],
            "execution_spec_dsl_version": completed_match["execution_spec_dsl_version"],
            "reverse_hypothesis_id": completed_match["reverse_hypothesis_id"],
            "reverse_spec_sha256": completed_match["reverse_spec_sha256"],
            "validation_start_date": completed_match["validation_start_date"],
            "validation_end_date": completed_match["validation_end_date"],
            "validation_draw_count": completed_match["validation_count"],
            "reverse_validation_start_date": completed_match["reverse_validation_start_date"],
            "reverse_validation_end_date": completed_match["reverse_validation_end_date"],
            "reverse_validation_draw_count": completed_match["reverse_validation_count"],
            "current_target_included": False,
            "current_target_numbers_exposed": False,
            "duplicate_holdout_scoring_performed": False,
        }
    used_dates = _used_validation_dates(ledger)
    package_exposure = None
    if research_package_id is not None:
        split, package_exposure = _build_v3_package_split(
            archive, ledger, offline_memory_path=offline_memory_path,
            package_id=research_package_id, attempt_id=scheduler_attempt_id,
        )
        if package_exposure is None:
            raise SealedEvaluationError("V3 claim requires a persisted pre-request package reservation.")
    else:
        split = split_archive(
            archive["rows"], archive_sha256=archive["archive_sha256"],
            used_validation_dates=used_dates, offline_memory_path=offline_memory_path,
        )
    canonical_pre_target_rows = [
        row for row in archive["rows"] if row["date"] < split["target_date"]
    ]
    offline_dates, offline_digest = _offline_development_consumption(offline_memory_path)
    if offline_digest != split["offline_development_memory_sha256"]:
        raise SealedEvaluationError("Offline development memory changed during claim creation.")
    package_unavailable = set(split.get("research_package", {}).get("previously_unavailable_dates", []))
    _validate_dsl_v2_directional_context(
        candidate, split, canonical_pre_target_rows,
        (package_unavailable if split.get("partition_version") == UNSEEN_PARTITION_VERSION else used_dates | set(offline_dates)),
    )
    r60 = split["regimes"]["R60"]
    validation = r60["validation_rows"]
    reverse_validation = r60["reverse_validation_rows"]
    validation_dates = [row["date"] for row in validation]
    reverse_validation_dates = [row["date"] for row in reverse_validation]
    if len(validation_dates) < MIN_VALIDATION_DRAWS:
        raise SealedEvaluationError("No sufficiently sized forward R60 validation interval is available.")
    if len(reverse_validation_dates) < MIN_VALIDATION_DRAWS:
        raise SealedEvaluationError("No sufficiently sized reverse R60 validation interval is available.")
    if set(validation_dates).intersection(reverse_validation_dates):
        raise SealedEvaluationError("Forward and reverse validation intervals overlap.")
    overlap = (set(validation_dates) | set(reverse_validation_dates)).intersection(_used_validation_dates(ledger))
    if overlap:
        raise SealedEvaluationError("Directional validation interval overlaps already-consumed dates; claim denied.")
    if len(r60["discovery_rows"]) < DEFAULT_MIN_TRAIN_DRAWS:
        raise SealedEvaluationError("R60 discovery history is shorter than the default minimum.")
    if candidate["synthetic_test_summary"].get("real_archive_used") is not False:
        raise SealedEvaluationError("Forward synthetic preflight did not certify its non-real-archive status.")
    if candidate["reverse_synthetic_test_summary"].get("real_archive_used") is not False:
        raise SealedEvaluationError("Reverse synthetic preflight did not certify its non-real-archive status.")

    claim_material = {
        "archive_sha256": archive["archive_sha256"],
        "target_date": split["target_date"],
        "partition_version": split["partition_version"],
        "research_scope_start_date": RESEARCH_SCOPE_START.isoformat(),
        "validation_dates": validation_dates,
        "reverse_validation_dates": reverse_validation_dates,
        "previously_consumed_validation_dates": sorted(used_dates),
        "previously_consumed_offline_development_draw_count": split["previously_consumed_offline_development_draw_count"],
        "offline_development_memory_sha256": split["offline_development_memory_sha256"],
        "hypothesis_id": candidate["hypothesis_id"],
        "spec_sha256": candidate["spec_sha256"],
        "reverse_hypothesis_id": candidate["reverse_hypothesis_id"],
        "reverse_spec_sha256": candidate["reverse_spec_sha256"],
        "execution_spec_dsl_version": candidate["execution_spec_dsl_version"],
        "reverse_execution_spec_dsl_version": candidate["reverse_execution_spec_dsl_version"],
        "engine_version": ENGINE_VERSION,
        **({
            "research_package_id": split["research_package"]["package_id"],
            "scheduler_attempt_id": scheduler_attempt_id,
            "previously_unavailable_dates_sha256": split["research_package"]["previously_unavailable_dates_sha256"],
        } if split.get("partition_version") == UNSEEN_PARTITION_VERSION else {}),
    }
    claim_id = _sha256_bytes(_canonical_json(claim_material))
    snapshot_path = Path(snapshot_directory) / (archive["archive_sha256"] + ".json")
    if snapshot_path.exists():
        existing = snapshot_path.read_bytes()
        if _sha256_bytes(existing) != archive["archive_sha256"]:
            raise SealedEvaluationError("Existing archive snapshot does not match its content address.")
    else:
        archive_raw = Path(archive_path).read_bytes()
        if _sha256_bytes(archive_raw) != archive["archive_sha256"]:
            raise SealedEvaluationError("Archive changed while creating the sealed claim.")
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_bytes(archive_raw)

    entry = {
        "claim_id": claim_id,
        "partition_version": split["partition_version"],
        **({
            "research_package_id": split["research_package"]["package_id"],
            "scheduler_attempt_id": scheduler_attempt_id,
            "previously_unavailable_dates_sha256": split["research_package"]["previously_unavailable_dates_sha256"],
        } if split.get("partition_version") == UNSEEN_PARTITION_VERSION else {}),
        "state": "CLAIMED",
        "claimed_at_utc": datetime.now(timezone.utc).isoformat(),
        "orchestrator_version": VERSION,
        "engine_version": ENGINE_VERSION,
        "target_date": split["target_date"],
        "archive_sha256": archive["archive_sha256"],
        "archive_record_count": archive["record_count"],
        "previously_consumed_validation_dates": sorted(used_dates),
        "previously_consumed_offline_development_draw_count": split["previously_consumed_offline_development_draw_count"],
        "offline_development_memory_sha256": split["offline_development_memory_sha256"],
        "archive_snapshot_path": str(snapshot_path).replace("\\", "/"),
        "hypothesis_id": candidate["hypothesis_id"],
        "spec_sha256": candidate["spec_sha256"],
        "execution_spec_dsl_version": candidate["execution_spec_dsl_version"],
        "reverse_hypothesis_id": candidate["reverse_hypothesis_id"],
        "reverse_spec_sha256": candidate["reverse_spec_sha256"],
        "reverse_execution_spec_dsl_version": candidate["reverse_execution_spec_dsl_version"],
        "regime": "R60",
        "number_pool_size": 60,
        "discovery_count": split["discovery_count"],
        "r60_discovery_count": r60["discovery_count"],
        "discovery_start_date": r60["discovery_start_date"],
        "discovery_end_date": r60["discovery_end_date"],
        "validation_dates": validation_dates,
        "validation_start_date": validation[0]["date"],
        "validation_end_date": validation[-1]["date"],
        "validation_count": len(validation),
        "reverse_validation_dates": reverse_validation_dates,
        "reverse_validation_start_date": reverse_validation[0]["date"],
        "reverse_validation_end_date": reverse_validation[-1]["date"],
        "reverse_validation_count": len(reverse_validation),
        "minimum_training_draws": DEFAULT_MIN_TRAIN_DRAWS,
        "maximum_validation_draws": DEFAULT_MAX_VALIDATION_DRAWS,
        "candidate_selection_rule": "Freeze exactly the first eligible proposal independently in each direction; require one matching safe DSL specification per direction. Do not select alternatives after validation.",
        "numbers_stored_in_ledger": False,
    }
    ledger["entries"].append(entry)
    if package_exposure is not None:
        research_windows.append_package_status(
            ledger, package_exposure, state="CLAIMED",
            recorded_at_utc=datetime.now(timezone.utc).isoformat(),
            reason="Frozen directional candidates claimed for sealed evaluation",
        )
    _write_json_atomic(Path(ledger_path), ledger)
    return {
        "status": "CLAIMED",
        "claim_created": True,
        "claim_resumed": False,
        "claim_id": claim_id,
        "research_package_id": split.get("research_package", {}).get("package_id"),
        "scheduler_attempt_id": scheduler_attempt_id,
        "target_date": split["target_date"],
        "archive_sha256": archive["archive_sha256"],
        "hypothesis_id": candidate["hypothesis_id"],
        "spec_sha256": candidate["spec_sha256"],
        "execution_spec_dsl_version": candidate["execution_spec_dsl_version"],
        "reverse_execution_spec_dsl_version": candidate["reverse_execution_spec_dsl_version"],
        "validation_start_date": validation[0]["date"],
        "validation_end_date": validation[-1]["date"],
        "validation_draw_count": len(validation),
        "previously_consumed_offline_development_draw_count": split["previously_consumed_offline_development_draw_count"],
        "offline_development_memory_sha256": split["offline_development_memory_sha256"],
        "reverse_validation_start_date": reverse_validation[0]["date"],
        "reverse_validation_end_date": reverse_validation[-1]["date"],
        "reverse_validation_draw_count": len(reverse_validation),
        "forward_hypothesis_id": candidate["hypothesis_id"],
        "reverse_hypothesis_id": candidate["reverse_hypothesis_id"],
        "archive_snapshot_path": str(snapshot_path).replace("\\", "/"),
        "ledger_entry_must_be_persisted_before_evaluation": True,
        "current_target_included": False,
        "current_target_numbers_exposed": False,
    }


def _find_claim(ledger: dict[str, Any], claim_id: str) -> dict[str, Any]:
    found = [entry for entry in ledger["entries"] if entry.get("claim_id") == claim_id]
    if len(found) != 1:
        raise SealedEvaluationError("The requested claim is missing or duplicated in the persisted ledger.")
    if found[0].get("state") != "CLAIMED":
        raise SealedEvaluationError("This claim has already been consumed; re-evaluation is forbidden.")
    return found[0]


def _evaluate_full_reverse_history_v2(
    available_history: list[dict[str, Any]],
    canonical_history: list[dict[str, Any]],
    excluded_dates: set[str],
    *,
    cutoff_date: str,
    number_pool_size: int,
    execution_spec: dict[str, Any],
    min_train_draws: int = DEFAULT_MIN_TRAIN_DRAWS,
) -> dict[str, Any]:
    """Batch reverse diagnostics without bridging previously consumed archive rows."""
    eligible_count = execution_v2.reverse_eligible_target_count(
        available_history,
        canonical_history=canonical_history,
        excluded_dates=excluded_dates,
        min_train_draws=min_train_draws,
    )
    if eligible_count < 1:
        return {
            "engine_version": execution_v2.VERSION,
            "execution_dsl_version": 2,
            "status": "SKIPPED_NO_ELIGIBLE_REVERSE_TARGETS",
            "protocol": "reverse retrospective test; exact canonical B/C adjacency required; consumed context excluded",
            "predictive_evidence": False,
            "number_pool_size": number_pool_size,
            "evaluated_draw_count": 0,
            "eligible_draw_count": 0,
            "ineligible_target_count": len(available_history),
            "mean_hits_out_of_6": None,
            "standard_error_of_mean_hits": None,
            "uniform_random_expected_hits_out_of_6": 36 / number_pool_size,
            "hit_distribution": {str(hits): 0 for hits in range(7)},
            "per_draw": [],
            "first_test_date": None,
            "last_test_date": None,
            "skipped_context_draw_count": 0,
            "previously_consumed_rows_used_as_context": False,
            "canonical_adjacency_enforced": True,
        }

    hit_distribution = {str(hits): 0 for hits in range(7)}
    per_draw: list[dict[str, Any]] = []
    offset = 0
    skipped_context = skipped_consumed = skipped_missing = skipped_insufficient = 0
    first_test_date = last_test_date = previous_batch_last_date = None

    while offset < eligible_count:
        batch_size = min(MAX_VALIDATION_DRAWS, eligible_count - offset)
        batch = execution_v2.evaluate_reverse_retrospective_stress(
            available_history,
            cutoff_date=cutoff_date,
            number_pool_size=number_pool_size,
            execution_spec=execution_spec,
            test_draw_count=batch_size,
            min_train_draws=min_train_draws,
            skip_recent_test_draws=offset,
            canonical_history=canonical_history,
            excluded_dates=excluded_dates,
        )
        if (
            batch.get("status") != "EVALUATED_RETROSPECTIVE_ONLY"
            or batch.get("eligible_draw_count") != eligible_count
            or batch.get("evaluated_draw_count") != batch_size
        ):
            raise SealedEvaluationError("Reverse DSL v2 batch did not preserve its canonical eligible target set.")
        batch_distribution = batch.get("hit_distribution")
        if not isinstance(batch_distribution, dict) or set(batch_distribution) != set(hit_distribution):
            raise SealedEvaluationError("Reverse DSL v2 batch returned a malformed aggregate histogram.")
        if sum(batch_distribution.values()) != batch_size:
            raise SealedEvaluationError("Reverse DSL v2 batch histogram count does not match its draw count.")
        batch_details = batch.get("per_draw")
        if not isinstance(batch_details, list) or len(batch_details) != batch_size:
            raise SealedEvaluationError("Reverse DSL v2 batch per-draw details are incomplete.")
        for detail in batch_details:
            if (
                not isinstance(detail, dict)
                or not isinstance(detail.get("test_date"), str)
                or detail.get("direction") != "reverse_retrospective"
                or type(detail.get("hit_count")) is not int
                or not 0 <= detail["hit_count"] <= 6
                or not isinstance(detail.get("hit_column_positions_1_to_6"), list)
                or len(detail["hit_column_positions_1_to_6"]) != detail["hit_count"]
                or any(type(pos) is not int or not 1 <= pos <= 6 for pos in detail["hit_column_positions_1_to_6"])
                or "numbers" in detail
            ):
                raise SealedEvaluationError("Reverse DSL v2 batch contains invalid per-draw detail.")
        if previous_batch_last_date is not None and batch["first_test_date"] >= previous_batch_last_date:
            raise SealedEvaluationError("Reverse DSL v2 batches overlap or are not strictly newest-to-oldest.")
        if first_test_date is None:
            first_test_date = batch["first_test_date"]
        last_test_date = batch["last_test_date"]
        previous_batch_last_date = last_test_date
        per_draw.extend(batch_details)
        for hits in range(7):
            count = batch_distribution[str(hits)]
            if type(count) is not int or count < 0:
                raise SealedEvaluationError("Reverse DSL v2 histogram contains an invalid count.")
            hit_distribution[str(hits)] += count
        skipped_context = batch["skipped_context_draw_count"]
        skipped_consumed = batch["skipped_consumed_context_draw_count"]
        skipped_missing = batch["skipped_missing_context_draw_count"]
        skipped_insufficient = batch["skipped_insufficient_later_history_draw_count"]
        offset += batch_size

    total_draws = sum(hit_distribution.values())
    if total_draws != eligible_count:
        raise SealedEvaluationError("Reverse DSL v2 diagnostic did not cover every eligible target.")
    total_hits = sum(int(hits) * count for hits, count in hit_distribution.items())
    sum_squares = sum((int(hits) ** 2) * count for hits, count in hit_distribution.items())
    mean_hits = total_hits / total_draws
    if total_draws > 1:
        sample_variance = max(0.0, (sum_squares - (total_hits * total_hits / total_draws)) / (total_draws - 1))
        standard_error = math.sqrt(sample_variance / total_draws)
    else:
        standard_error = None
    return {
        "engine_version": execution_v2.VERSION,
        "execution_dsl_version": 2,
        "status": "EVALUATED_RETROSPECTIVE_ONLY",
        "protocol": "full available reverse-time retrospective diagnostic; exact canonical B/C neighbours; consumed rows excluded",
        "predictive_evidence": False,
        "number_pool_size": number_pool_size,
        "evaluated_draw_count": total_draws,
        "eligible_draw_count": eligible_count,
        "ineligible_target_count": len(available_history) - eligible_count,
        "evaluation_order": "newest_to_oldest",
        "first_test_date": first_test_date,
        "last_test_date": last_test_date,
        "minimum_later_training_draws": min_train_draws,
        "mean_hits_out_of_6": round(mean_hits, 6),
        "standard_error_of_mean_hits": round(standard_error, 6) if standard_error is not None else None,
        "uniform_random_expected_hits_out_of_6": round(36 / number_pool_size, 6),
        "hit_distribution": hit_distribution,
        "per_draw": per_draw,
        "per_draw_detail_present": True,
        "target_number_sets_reported": False,
        "predicted_number_values_reported": False,
        "current_isolated_target_included": False,
        "current_target_numbers_exposed": False,
        "skipped_context_draw_count": skipped_context,
        "skipped_consumed_context_draw_count": skipped_consumed,
        "skipped_missing_context_draw_count": skipped_missing,
        "skipped_insufficient_later_history_draw_count": skipped_insufficient,
        "previously_consumed_rows_used_as_context": False,
        "canonical_adjacency_enforced": True,
        "automatic_candidate_selection": False,
        "method_registry_modified": False,
    }


def _evaluate_full_reverse_history(
    pre_target_history: list[dict[str, Any]],
    *,
    cutoff_date: str,
    number_pool_size: int,
    execution_spec: dict[str, Any],
    min_train_draws: int = DEFAULT_MIN_TRAIN_DRAWS,
    canonical_history: list[dict[str, Any]] | None = None,
    excluded_dates: set[str] | list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Evaluate every reverse-eligible row in bounded batches; return aggregates only."""
    if execution_spec_version(execution_spec) == 2:
        if canonical_history is None:
            raise SealedEvaluationError("DSL v2 full reverse evaluation requires canonical archive history.")
        return _evaluate_full_reverse_history_v2(
            pre_target_history,
            canonical_history,
            set(excluded_dates),
            cutoff_date=cutoff_date,
            number_pool_size=number_pool_size,
            execution_spec=execution_spec,
            min_train_draws=min_train_draws,
        )
    eligible_count = len(pre_target_history) - min_train_draws
    if eligible_count < 1:
        raise SealedEvaluationError("Not enough later historical rows for reverse stress testing.")

    hit_distribution = {str(hits): 0 for hits in range(7)}
    per_draw: list[dict[str, Any]] = []
    offset = 0
    first_test_date = None
    last_test_date = None
    previous_batch_last_date = None

    while offset < eligible_count:
        batch_size = min(MAX_VALIDATION_DRAWS, eligible_count - offset)
        if execution_spec_version(execution_spec) == 2:
            batch = execution_v2.evaluate_reverse_retrospective_stress(
                pre_target_history,
                cutoff_date=cutoff_date,
                number_pool_size=number_pool_size,
                execution_spec=execution_spec,
                test_draw_count=batch_size,
                min_train_draws=min_train_draws,
                skip_recent_test_draws=offset,
                canonical_history=canonical_history,
                excluded_dates=excluded_dates,
            )
        else:
            batch = evaluate_reverse_retrospective_stress(
                pre_target_history,
                cutoff_date=cutoff_date,
                number_pool_size=number_pool_size,
                execution_spec=execution_spec,
                test_draw_count=batch_size,
                min_train_draws=min_train_draws,
                skip_recent_test_draws=offset,
            )
        if batch.get("status") != "EVALUATED_RETROSPECTIVE_ONLY":
            raise SealedEvaluationError("Reverse-time batch did not return its expected diagnostic status.")
        if batch.get("evaluated_draw_count") != batch_size:
            raise SealedEvaluationError("Reverse-time batch returned an incomplete draw count.")
        batch_distribution = batch.get("hit_distribution")
        if not isinstance(batch_distribution, dict) or set(batch_distribution) != set(hit_distribution):
            raise SealedEvaluationError("Reverse-time batch returned a malformed aggregate histogram.")
        if sum(batch_distribution.values()) != batch_size:
            raise SealedEvaluationError("Reverse-time batch histogram count does not match its draw count.")
        batch_details = batch.get("per_draw")
        if not isinstance(batch_details, list) or len(batch_details) != batch_size:
            raise SealedEvaluationError("Reverse-time batch per-draw details are incomplete.")
        for detail in batch_details:
            if (
                not isinstance(detail, dict)
                or not isinstance(detail.get("test_date"), str)
                or detail.get("direction") != "reverse_retrospective"
                or type(detail.get("hit_count")) is not int
                or not 0 <= detail["hit_count"] <= 6
                or not isinstance(detail.get("hit_column_positions_1_to_6"), list)
                or len(detail["hit_column_positions_1_to_6"]) != detail["hit_count"]
                or any(type(pos) is not int or not 1 <= pos <= 6 for pos in detail["hit_column_positions_1_to_6"])
                or "numbers" in detail
            ):
                raise SealedEvaluationError("Reverse-time batch contains invalid per-draw detail.")
        per_draw.extend(batch_details)
        if previous_batch_last_date is not None and batch["first_test_date"] >= previous_batch_last_date:
            raise SealedEvaluationError("Reverse-time batches overlap or are not strictly newest-to-oldest.")
        if first_test_date is None:
            first_test_date = batch["first_test_date"]
        last_test_date = batch["last_test_date"]
        previous_batch_last_date = last_test_date

        for hits in range(7):
            count = batch_distribution[str(hits)]
            if type(count) is not int or count < 0:
                raise SealedEvaluationError("Reverse-time batch histogram contains an invalid count.")
            hit_distribution[str(hits)] += count
        offset += batch_size

    total_draws = sum(hit_distribution.values())
    if total_draws != eligible_count:
        raise SealedEvaluationError("Full reverse-time evaluation did not cover every eligible historical row.")
    total_hits = sum(int(hits) * count for hits, count in hit_distribution.items())
    sum_squares = sum((int(hits) ** 2) * count for hits, count in hit_distribution.items())
    mean_hits = total_hits / total_draws
    if total_draws > 1:
        sample_variance = max(0.0, (sum_squares - (total_hits * total_hits / total_draws)) / (total_draws - 1))
        standard_error = math.sqrt(sample_variance / total_draws)
    else:
        standard_error = None

    return {
        "engine_version": ENGINE_VERSION,
        "status": "EVALUATED_RETROSPECTIVE_ONLY",
        "protocol": "full available reverse-time retrospective stress test; bounded batches; later draws are used",
        "predictive_evidence": False,
        "number_pool_size": number_pool_size,
        "evaluated_draw_count": total_draws,
        "evaluation_order": "newest_to_oldest",
        "first_test_date": first_test_date,
        "last_test_date": last_test_date,
        "minimum_later_training_draws": min_train_draws,
        "maximum_later_history_window_draws": MAX_HISTORY_DRAWS,
        "mean_hits_out_of_6": round(mean_hits, 6),
        "standard_error_of_mean_hits": round(standard_error, 6) if standard_error is not None else None,
        "uniform_random_expected_hits_out_of_6": round(36 / number_pool_size, 6),
        "hit_distribution": hit_distribution,
        "per_draw": per_draw,
        "per_draw_detail_present": True,
        "target_number_sets_reported": False,
        "predicted_number_values_reported": False,
        "current_isolated_target_included": False,
        "current_target_numbers_exposed": False,
        "per_draw_target_numbers_reported": False,
        "candidate_rankings_reported": False,
        "automatic_candidate_selection": False,
        "method_registry_modified": False,
        "interpretation": (
            "Every historical row with at least the configured later-training history is included, "
            "from newest to oldest. Later historical draws are intentionally used. This is retrospective "
            "stress testing only, not forward predictive evidence or a basis for claiming future advantage."
        ),
    }


def _evaluate_reverse_validation_with_frozen_spec(
    reverse_validation: list[dict[str, Any]],
    full_pre_target_history: list[dict[str, Any]],
    *,
    isolated_target_date: str,
    hypothesis_id: str,
    spec_sha256: str,
    execution_spec: dict[str, Any],
    canonical_history: list[dict[str, Any]] | None = None,
    excluded_dates: set[str] | list[str] | tuple[str, ...] = (),
    min_train_draws: int = DEFAULT_MIN_TRAIN_DRAWS,
) -> dict[str, Any]:
    """Score reverse holdout targets using exact canonical B/C neighbours for DSL v2."""
    if execution_spec_version(execution_spec) == 2 and canonical_history is None:
        raise SealedEvaluationError("DSL v2 reverse holdout requires canonical archive history.")
    if len(reverse_validation) < MIN_VALIDATION_DRAWS:
        raise SealedEvaluationError("Reverse holdout is too short.")
    target_day = date.fromisoformat(isolated_target_date)
    canonical = sorted(
        canonical_history if canonical_history is not None else full_pre_target_history,
        key=lambda row: row["date"],
    )
    history_by_date = {row["date"]: row for row in full_pre_target_history}
    canonical_by_date = {row["date"]: row for row in canonical}
    blocked = set(excluded_dates)
    if len(history_by_date) != len(full_pre_target_history):
        raise SealedEvaluationError("Reverse evaluation history has duplicate dates.")
    if any(date.fromisoformat(row["date"]) >= target_day for row in canonical):
        raise SealedEvaluationError("Reverse canonical history includes the current target or a later draw.")
    if not blocked.issubset(canonical_by_date):
        raise SealedEvaluationError("Previously consumed reverse context dates are absent from canonical history.")
    if set(history_by_date).intersection(blocked):
        raise SealedEvaluationError("Previously consumed rows entered reverse evaluation history.")

    details = []
    skipped_consumed_context = skipped_missing_context = skipped_insufficient_history = 0
    for target in sorted(reverse_validation, key=lambda row: row["date"], reverse=True):
        target_date = target["date"]
        later_available = [row for row in full_pre_target_history if row["date"] > target_date]
        if execution_spec_version(execution_spec) == 2:
            if len(later_available) < min_train_draws:
                skipped_insufficient_history += 1
                continue
            later_canonical = [row for row in canonical if row["date"] > target_date]
            if len(later_canonical) < 2:
                skipped_missing_context += 1
                continue
            pair = later_canonical[:2]
            if any(row["date"] in blocked for row in pair):
                skipped_consumed_context += 1
                continue
            if any(history_by_date.get(row["date"]) != row for row in pair):
                skipped_consumed_context += 1
                continue
            if later_available[:2] != pair:
                skipped_missing_context += 1
                continue
            ranking = execution_v2.rank_candidates_for_pair(
                pair,
                target_date=target_date,
                number_pool_size=60,
                execution_spec=execution_spec,
                canonical_history=later_canonical,
                excluded_dates=blocked,
            )
        else:
            # Preserve the legacy DSL v1 evaluator's original bounded history window.
            # v2 uses only the exact adjacent B/C pair above.
            ranking = rank_candidates_retrospective(
                later_available[:MAX_HISTORY_DRAWS],
                target_date=target_date,
                number_pool_size=60,
                execution_spec=execution_spec,
                min_train_draws=min_train_draws,
            )

        target_numbers = set(target["numbers"])
        matched_positions = [
            position
            for position, item in enumerate(ranking[:6], start=1)
            if item["number"] in target_numbers
        ]
        details.append({
            "test_date": target_date,
            "direction": "reverse_retrospective",
            "hypothesis_id": hypothesis_id,
            "spec_sha256": spec_sha256,
            "later_training_draw_count": len(later_available),
            "hit_column_positions_1_to_6": matched_positions,
            "hit_count": len(matched_positions),
            "target_numbers_reported": False,
            "predicted_number_values_reported": False,
        })

    hit_distribution = {
        str(hits): sum(row["hit_count"] == hits for row in details)
        for hits in range(7)
    }
    evaluated_count = len(details)
    mean_hits = (
        round(sum(row["hit_count"] for row in details) / evaluated_count, 6)
        if evaluated_count else None
    )
    return {
        "status": "EVALUATED_RETROSPECTIVE_ONLY" if evaluated_count else "SKIPPED_NO_ELIGIBLE_REVERSE_TARGETS",
        "direction": "reverse_retrospective",
        "predictive_evidence": False,
        "evaluation_order": "newest_to_oldest",
        "hypothesis_id": hypothesis_id,
        "spec_sha256": spec_sha256,
        "test_draw_count": evaluated_count,
        "eligible_draw_count": evaluated_count,
        "skipped_draw_count": len(reverse_validation) - evaluated_count,
        "skipped_consumed_context_draw_count": skipped_consumed_context,
        "skipped_missing_context_draw_count": skipped_missing_context,
        "skipped_insufficient_later_history_draw_count": skipped_insufficient_history,
        "first_test_date": details[0]["test_date"] if details else None,
        "last_test_date": details[-1]["test_date"] if details else None,
        "mean_hits_out_of_6": mean_hits,
        "uniform_random_expected_hits_out_of_6": 0.6,
        "hit_distribution": hit_distribution,
        "per_draw": details,
        "target_number_sets_reported": False,
        "predicted_number_values_reported": False,
        "previously_consumed_rows_used_as_context": False,
        "canonical_adjacency_enforced": execution_spec_version(execution_spec) == 2,
        "interpretation": (
            "DSL v2 reverse targets use only the two immediately following canonical archive rows as B/C. "
            "Targets whose adjacent context includes a previously consumed holdout are skipped, never bridged. "
            "Reverse results remain retrospective-only."
        ),
    }



def _evaluate_forward_validation_with_details(
    discovery: list[dict[str, Any]],
    validation: list[dict[str, Any]],
    *,
    hypothesis_id: str,
    spec_sha256: str,
    execution_spec: dict[str, Any],
    canonical_history: list[dict[str, Any]] | None = None,
    excluded_dates: set[str] | list[str] | tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Return per-draw column hits while reusing only history before each forward target."""
    if execution_spec_version(execution_spec) == 2 and canonical_history is None:
        raise SealedEvaluationError("DSL v2 forward detail evaluation requires canonical archive history.")
    details = []
    for index, target in enumerate(validation):
        training = (discovery + validation[:index])[-MAX_HISTORY_DRAWS:]
        if len(training) < DEFAULT_MIN_TRAIN_DRAWS:
            raise SealedEvaluationError("Forward per-draw report fell below the training minimum.")
        if execution_spec_version(execution_spec) == 2:
            canonical_before = [
                row for row in canonical_history if row["date"] < target["date"]
            ]
            ranking = execution_v2.rank_candidates_for_pair(
                training[-2:],
                target_date=target["date"],
                number_pool_size=60,
                execution_spec=execution_spec,
                canonical_history=canonical_before,
                excluded_dates=excluded_dates,
            )
        else:
            ranking = rank_candidates(
                training,
                cutoff_date=target["date"],
                number_pool_size=60,
                execution_spec=execution_spec,
            )
        actual = set(target["numbers"])
        matched_positions = [
            position
            for position, item in enumerate(ranking[:6], start=1)
            if item["number"] in actual
        ]
        details.append({
            "test_date": target["date"],
            "direction": "forward",
            "hypothesis_id": hypothesis_id,
            "spec_sha256": spec_sha256,
            "training_draw_count": len(training),
            "hit_column_positions_1_to_6": matched_positions,
            "hit_count": len(matched_positions),
            "target_numbers_reported": False,
            "predicted_number_values_reported": False,
        })
    return details


def _summarize_blocked_reverse_cv(
    reverse_holdout: dict[str, Any],
    *,
    folds: int = 5,
) -> dict[str, Any]:
    """Summarize contiguous newest-to-oldest blocks of the same reverse sealed interval."""
    rows = reverse_holdout.get("per_draw")
    if not isinstance(rows, list) or len(rows) < MIN_VALIDATION_DRAWS:
        raise SealedEvaluationError("Reverse CV requires a valid reverse holdout detail list.")
    fold_count = min(folds, len(rows))
    base_size, extra = divmod(len(rows), fold_count)
    fold_reports = []
    start = 0
    for fold_index in range(fold_count):
        size = base_size + (1 if fold_index < extra else 0)
        block = rows[start:start + size]
        fold_reports.append({
            "fold": fold_index + 1,
            "validation_order": "newest_to_oldest",
            "validation_start_date": block[0]["test_date"],
            "validation_end_date": block[-1]["test_date"],
            "validation_draw_count": len(block),
            "mean_hits_out_of_6": round(
                sum(row["hit_count"] for row in block) / len(block), 6
            ),
            "hit_distribution": {
                str(hits): sum(row["hit_count"] == hits for row in block)
                for hits in range(7)
            },
        })
        start += size
    total_draws = sum(item["validation_draw_count"] for item in fold_reports)
    weighted = sum(
        item["mean_hits_out_of_6"] * item["validation_draw_count"]
        for item in fold_reports
    )
    return {
        "status": "EVALUATED_RETROSPECTIVE_ONLY",
        "protocol": "blocked reverse-time folds over one frozen specification; later historical draws are used",
        "direction": "reverse_retrospective",
        "predictive_evidence": False,
        "folds": fold_count,
        "validation_draw_count": total_draws,
        "mean_hits_out_of_6": round(weighted / total_draws, 6),
        "uniform_random_expected_hits_out_of_6": 0.6,
        "fold_reports": fold_reports,
        "same_sealed_reverse_interval_as_candidate_holdout": True,
        "independent_confirmation": False,
        "automatic_candidate_selection": False,
        "interpretation": (
            "These contiguous blocks reuse the exact reverse holdout already reported above. "
            "They are correlated descriptive diagnostics, not independent confirmation. The reverse "
            "direction uses later dates and is never predictive evidence."
        ),
    }


def evaluate_claim(
    claim_id: str,
    ledger_path: str | Path,
    proposals_path: str | Path,
    specs_path: str | Path,
    *,
    output_path: str | Path,
    offline_memory_path: str | Path = "general_research_memory.json",
) -> dict[str, Any]:
    """Evaluate one already-persisted claim against its immutable archive snapshot."""
    ledger = load_ledger(ledger_path)
    entry = _find_claim(ledger, claim_id)
    entry_index = next(
        index for index, item in enumerate(ledger["entries"])
        if item.get("claim_id") == claim_id
    )
    expected_prior_dates = sorted({
        day
        for prior in ledger["entries"][:entry_index]
        for day in prior["validation_dates"] + prior.get("reverse_validation_dates", [])
    })
    recorded_prior_dates = entry.get("previously_consumed_validation_dates", expected_prior_dates)
    if recorded_prior_dates != expected_prior_dates:
        raise SealedEvaluationError("Claim prior-consumption snapshot does not match the append-only ledger.")
    candidate = _select_one_frozen_candidate(proposals_path, specs_path)
    if candidate is None:
        raise SealedEvaluationError("Frozen candidate disappeared or no longer matches the parsed proposal.")
    if candidate["hypothesis_id"] != entry["hypothesis_id"] or candidate["spec_sha256"] != entry["spec_sha256"]:
        raise SealedEvaluationError("Candidate changed after the one-shot validation claim.")
    snapshot_path = Path(entry["archive_snapshot_path"])
    archive = load_archive(snapshot_path)
    if archive["archive_sha256"] != entry["archive_sha256"]:
        raise SealedEvaluationError("Immutable archive snapshot hash does not match the ledger.")
    claim_partition_version = entry.get("partition_version", LEGACY_PARTITION_VERSION)
    package_exposure = None
    if claim_partition_version == UNSEEN_PARTITION_VERSION:
        package_exposure = research_windows.package_exposure_for_attempt(
            ledger, package_id=entry.get("research_package_id"),
            attempt_id=entry.get("scheduler_attempt_id"),
        )
        if package_exposure is None:
            raise SealedEvaluationError("V3 sealed claim lost its exact package reservation.")
        package = research_windows.package_from_exposure(package_exposure)
        split = research_windows.split_from_package(
            archive["rows"], package, archive_sha256=archive["archive_sha256"]
        )
        split["previously_consumed_validation_draw_count"] = len(recorded_prior_dates)
        split["previously_consumed_offline_development_draw_count"] = entry.get(
            "previously_consumed_offline_development_draw_count", 0
        )
    else:
        split = split_archive(
            archive["rows"], archive_sha256=archive["archive_sha256"],
            used_validation_dates=set(expected_prior_dates), offline_memory_path=offline_memory_path,
            partition_version=claim_partition_version,
        )
    if (
        "offline_development_memory_sha256" not in entry
        or "previously_consumed_offline_development_draw_count" not in entry
    ):
        empty_offline_sha256 = _sha256_bytes(_canonical_json([]))
        if (
            split["previously_consumed_offline_development_draw_count"] != 0
            or split["offline_development_memory_sha256"] != empty_offline_sha256
        ):
            raise SealedEvaluationError(
                "Legacy claim has no offline-consumption snapshot; current offline memory is not empty."
            )
    elif (
        entry["offline_development_memory_sha256"] != split["offline_development_memory_sha256"]
        or entry["previously_consumed_offline_development_draw_count"]
        != split["previously_consumed_offline_development_draw_count"]
    ):
        raise SealedEvaluationError(
            "Offline development-consumption memory changed after the sealed claim."
        )
    replay_canonical_pre_target = [
        row for row in archive["rows"] if row["date"] < split["target_date"]
    ]
    current_offline_dates, current_offline_digest = _offline_development_consumption(offline_memory_path)
    if current_offline_digest != split["offline_development_memory_sha256"]:
        raise SealedEvaluationError("Offline development memory changed during evaluation replay.")
    evaluation_excluded_dates = (
        set(split.get("research_package", {}).get("previously_unavailable_dates", []))
        if claim_partition_version == UNSEEN_PARTITION_VERSION
        else set(expected_prior_dates) | set(current_offline_dates)
    )
    _validate_dsl_v2_directional_context(
        candidate, split, replay_canonical_pre_target, evaluation_excluded_dates,
    )
    if split["target_date"] != entry["target_date"]:
        raise SealedEvaluationError("Snapshot target cutoff does not match the ledger.")
    r60 = split["regimes"]["R60"]
    validation_dates = [row["date"] for row in r60["validation_rows"]]
    reverse_validation_dates = [row["date"] for row in r60["reverse_validation_rows"]]
    if validation_dates != entry["validation_dates"]:
        raise SealedEvaluationError("Snapshot forward validation dates do not match the sealed claim.")
    if reverse_validation_dates != entry.get("reverse_validation_dates"):
        raise SealedEvaluationError("Snapshot reverse validation dates do not match the sealed claim.")
    if candidate["reverse_hypothesis_id"] != entry.get("reverse_hypothesis_id"):
        raise SealedEvaluationError("Frozen reverse hypothesis changed after the one-shot claim.")
    if candidate["reverse_spec_sha256"] != entry.get("reverse_spec_sha256"):
        raise SealedEvaluationError("Frozen reverse execution specification changed after the claim.")
    if entry.get("execution_spec_dsl_version", 1) != candidate["execution_spec_dsl_version"]:
        raise SealedEvaluationError("Forward execution DSL version changed after the one-shot claim.")
    if entry.get("reverse_execution_spec_dsl_version", 1) != candidate["reverse_execution_spec_dsl_version"]:
        raise SealedEvaluationError("Reverse execution DSL version changed after the one-shot claim.")
    discovery = r60["discovery_rows"]
    validation = r60["validation_rows"]
    reverse_validation = r60["reverse_validation_rows"]
    canonical_pre_target_history = [
        row for row in archive["rows"] if row["date"] < split["target_date"]
    ]
    spec = candidate["execution_spec"]
    reverse_spec = candidate["reverse_execution_spec"]
    if (
        len(discovery) < DEFAULT_MIN_TRAIN_DRAWS
        or len(validation) < MIN_VALIDATION_DRAWS
        or len(reverse_validation) < MIN_VALIDATION_DRAWS
    ):
        raise SealedEvaluationError("Claim no longer satisfies both directional evaluation minimums.")

    if execution_spec_version(spec) == 2:
        forward = execution_v2.evaluate_holdout(
            discovery,
            validation,
            cutoff_date=split["target_date"],
            number_pool_size=60,
            execution_spec=spec,
            min_train_draws=DEFAULT_MIN_TRAIN_DRAWS,
            canonical_history=canonical_pre_target_history,
            excluded_dates=evaluation_excluded_dates,
        )
    else:
        forward = evaluate_holdout(
            discovery,
            validation,
            cutoff_date=split["target_date"],
            number_pool_size=60,
            execution_spec=spec,
            min_train_draws=DEFAULT_MIN_TRAIN_DRAWS,
        )
    forward_details = _evaluate_forward_validation_with_details(
        discovery,
        validation,
        hypothesis_id=candidate["hypothesis_id"],
        spec_sha256=candidate["spec_sha256"],
        execution_spec=spec,
        canonical_history=canonical_pre_target_history,
        excluded_dates=evaluation_excluded_dates,
    )
    detail_mean = round(
        sum(item["hit_count"] for item in forward_details) / len(forward_details), 6
    )
    if detail_mean != forward["mean_hits_out_of_6"]:
        raise SealedEvaluationError("Forward per-column detail does not reconcile to aggregate walk-forward score.")
    forward["per_draw"] = forward_details
    forward["per_draw_detail_present"] = True
    forward["per_draw_target_numbers_reported"] = False
    forward["predicted_number_values_reported"] = False
    folds = min(5, len(validation))
    if execution_spec_version(spec) == 2:
        cv = execution_v2.evaluate_time_ordered_cv(
            discovery,
            validation,
            cutoff_date=split["target_date"],
            number_pool_size=60,
            execution_spec=spec,
            folds=folds,
            min_train_draws=DEFAULT_MIN_TRAIN_DRAWS,
            canonical_history=canonical_pre_target_history,
            excluded_dates=evaluation_excluded_dates,
        )
    else:
        cv = evaluate_time_ordered_cv(
            discovery,
            validation,
            cutoff_date=split["target_date"],
            number_pool_size=60,
            execution_spec=spec,
            folds=folds,
            min_train_draws=DEFAULT_MIN_TRAIN_DRAWS,
        )
    full_r60_history = sorted(
        discovery + validation + reverse_validation,
        key=lambda row: row["date"],
    )
    reverse_candidate_holdout = _evaluate_reverse_validation_with_frozen_spec(
        reverse_validation,
        full_r60_history,
        isolated_target_date=split["target_date"],
        hypothesis_id=candidate["reverse_hypothesis_id"],
        spec_sha256=candidate["reverse_spec_sha256"],
        execution_spec=reverse_spec,
        canonical_history=canonical_pre_target_history,
        excluded_dates=evaluation_excluded_dates,
        min_train_draws=DEFAULT_MIN_TRAIN_DRAWS,
    )
    if len(reverse_candidate_holdout["per_draw"]) >= MIN_VALIDATION_DRAWS:
        reverse_cv = _summarize_blocked_reverse_cv(reverse_candidate_holdout)
    else:
        reverse_cv = {
            "status": "SKIPPED_INSUFFICIENT_ADJACENT_CONTEXT",
            "direction": "reverse_retrospective",
            "predictive_evidence": False,
            "folds": 0,
            "validation_draw_count": len(reverse_candidate_holdout["per_draw"]),
            "mean_hits_out_of_6": None,
            "independent_confirmation": False,
            "automatic_candidate_selection": False,
            "reason": "Fewer than two reverse holdout targets retained their exact, unconsumed canonical B/C neighbours.",
        }
    reverse_diagnostic_spec = spec if execution_spec_version(spec) == 1 else reverse_spec
    reverse_diagnostic_id = candidate["hypothesis_id"] if execution_spec_version(spec) == 1 else candidate["reverse_hypothesis_id"]
    reverse_diagnostic_sha = candidate["spec_sha256"] if execution_spec_version(spec) == 1 else candidate["reverse_spec_sha256"]
    reverse = _evaluate_full_reverse_history(
        full_r60_history,
        cutoff_date=split["target_date"],
        number_pool_size=60,
        execution_spec=reverse_diagnostic_spec,
        min_train_draws=DEFAULT_MIN_TRAIN_DRAWS,
        canonical_history=canonical_pre_target_history,
        excluded_dates=evaluation_excluded_dates,
    )
    reverse["hypothesis_id"] = reverse_diagnostic_id
    reverse["spec_sha256"] = reverse_diagnostic_sha
    reverse["diagnostic_spec_direction"] = execution_spec_direction(reverse_diagnostic_spec) or "legacy_direction_agnostic"
    for reverse_row in reverse.get("per_draw", []):
        reverse_row["hypothesis_id"] = reverse_diagnostic_id
        reverse_row["spec_sha256"] = reverse_diagnostic_sha
        reverse_row["target_numbers_reported"] = False
        reverse_row["predicted_number_values_reported"] = False
    bidirectional = evaluate_bidirectional_full_history(
        {"R60": full_r60_history},
        isolated_target_date=split["target_date"],
        min_train_draws=DEFAULT_MIN_TRAIN_DRAWS,
    )
    bidirectional_r60 = bidirectional["regimes"]["R60"]
    report = {
        "schema_version": 1,
        "status": "EVALUATED_EXPLORATORY_FIXED_SPEC",
        "orchestrator_version": VERSION,
        "engine_version": ENGINE_VERSION,
        "execution_dsl_versions": {
            "forward": candidate["execution_spec_dsl_version"],
            "reverse": candidate["reverse_execution_spec_dsl_version"],
        },
        "claim_id": claim_id,
        "hypothesis_id": candidate["hypothesis_id"],
        "spec_sha256": candidate["spec_sha256"],
        "archive_sha256": archive["archive_sha256"],
        "archive_snapshot_path": entry["archive_snapshot_path"],
        "target_date": split["target_date"],
        "current_isolated_target_included": False,
        "current_target_numbers_exposed": False,
        "target_or_future_rows_used": False,
        "discovery_rows_supplied_to_model": entry["discovery_count"],
        "validation_regime": "R60",
        "validation_start_date": validation[0]["date"],
        "validation_end_date": validation[-1]["date"],
        "validation_draw_count": len(validation),
        "forward_walk_forward": forward,
        "time_ordered_cross_validation": cv,
        **(
            {"reverse_retrospective_stress_test_of_forward_candidate": reverse}
            if execution_spec_version(spec) == 1
            else {"reverse_retrospective_full_history_diagnostic": reverse}
        ),
        "reverse_discovered_candidate_holdout": reverse_candidate_holdout,
        "reverse_time_ordered_cross_validation": reverse_cv,
        "bidirectional_full_history_adaptive_diagnostic": bidirectional,
        "candidate_selection": {
            "rule": "Freeze the first eligible forward/both proposal and the first eligible reverse/both proposal independently; one exact matching DSL spec for each direction.",
            "forward_hypothesis_id": candidate["hypothesis_id"],
            "forward_spec_sha256": candidate["spec_sha256"],
            "forward_execution_spec_dsl_version": candidate["execution_spec_dsl_version"],
            "reverse_hypothesis_id": candidate["reverse_hypothesis_id"],
            "reverse_spec_sha256": candidate["reverse_spec_sha256"],
            "reverse_execution_spec_dsl_version": candidate["reverse_execution_spec_dsl_version"],
            "evaluated_directional_specification_count": 2 if candidate["hypothesis_id"] != candidate["reverse_hypothesis_id"] else 1,
            "selection_used_validation_scores": False,
            "automatic_candidate_selection": False,
            "registry_promotion": False,
        },
        "interpretation_limits": [
            "This is one exploratory fixed-specification evaluation, not proof of a predictive mechanism.",
            "The blocked CV folds reuse the same sealed validation interval and are correlated summaries, not independent confirmation.",
            "The full-history reverse diagnostic uses later historical rows and is retrospective only. For direction-sealed DSL v2 it uses the reverse-discovered B/C specification; a forward v2 specification is never reinterpreted against reverse slots.",
            "The separately reverse-discovered candidate is evaluated only on the disjoint oldest R60 holdout, using strictly later history; this is retrospective only and not predictive evidence.",
            "The reverse blocked-CV folds reuse that same sealed reverse interval and are correlated descriptive summaries, not independent confirmation.",
            "The uniform-random expected overlap is 36/60 = 0.6 hits per six-number set; report uncertainty and do not infer an advantage from mean hits alone.",
            "Only one candidate is evaluated; no model-selected alternatives are scored on this interval.",
            "The full-history bidirectional adaptive diagnostic is a separate pre-registered safe-DSL baseline; it does not implement or approve A->B->C, A->T, or MOD5->T.",
            "For that diagnostic, each forward target is selected/scored using only earlier draws; each reverse target uses only later draws and is strictly retrospective.",
            "Its per-draw report records the target date, selected safe-catalog ID, hit count, and matched top-six column positions 1-6, but no target or predicted number values.",
            "The full-history adaptive diagnostic uses repeated overlapping selection windows; results are exploratory and are not independent confirmations.",
            "The reverse-discovered candidate report includes per-date hit count and matched top-six column positions, but excludes actual target numbers and predicted number values.",
            "The DSL v2 full-history reverse diagnostic skips targets whose exact next two canonical states include previously consumed holdout dates; it never bridges the chronology gap.",
            "No number-level target outcomes or complete candidate rankings are included in this report.",
        ],
        "method_registry_modified": False,
        "canonical_method_promoted": False,
        "per_draw_target_numbers_reported": False,
        "candidate_rankings_reported": False,
        "live_hosted_model_request_made_by_evaluator": False,
    }
    output = Path(output_path)
    report_raw = _write_json_atomic(output, report)
    entry["state"] = "EVALUATED"
    entry["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    entry["evaluation_report_sha256"] = _sha256_bytes(report_raw)
    entry["aggregate_result"] = {
        "forward_mean_hits_out_of_6": forward["mean_hits_out_of_6"],
        "forward_standard_error": forward["standard_error_of_mean_hits"],
        "forward_random_expected_hits_out_of_6": forward["uniform_random_expected_hits_out_of_6"],
        "cv_mean_hits_out_of_6": cv["mean_hits_out_of_6"],
        "reverse_mean_hits_out_of_6": reverse["mean_hits_out_of_6"],
        "reverse_predictive_evidence": reverse["predictive_evidence"],
        "reverse_candidate_holdout_mean_hits_out_of_6": reverse_candidate_holdout["mean_hits_out_of_6"],
        "reverse_candidate_holdout_draw_count": reverse_candidate_holdout["test_draw_count"],
        "reverse_time_ordered_cv_mean_hits_out_of_6": reverse_cv["mean_hits_out_of_6"],
        "reverse_time_ordered_cv_fold_count": reverse_cv["folds"],
        "reverse_candidate_holdout_predictive_evidence": False,
        "reverse_candidate_holdout_spec_sha256": reverse_candidate_holdout["spec_sha256"],
        "bidirectional_catalog_forward_mean_hits_out_of_6": bidirectional_r60["forward"]["mean_hits_out_of_6"],
        "bidirectional_catalog_forward_draw_count": bidirectional_r60["forward"]["evaluated_draw_count"],
        "bidirectional_catalog_reverse_mean_hits_out_of_6": bidirectional_r60["reverse_retrospective"]["mean_hits_out_of_6"],
        "bidirectional_catalog_reverse_draw_count": bidirectional_r60["reverse_retrospective"]["evaluated_draw_count"],
        "bidirectional_catalog_reverse_predictive_evidence": False,
    }
    entry["per_draw_values_stored"] = False
    if package_exposure is not None:
        research_windows.append_package_status(
            ledger, package_exposure, state="EVALUATED",
            recorded_at_utc=datetime.now(timezone.utc).isoformat(),
            reason="Frozen V3 sealed evaluation completed",
        )
    _write_json_atomic(Path(ledger_path), ledger)
    return {
        "status": report["status"],
        "claim_id": claim_id,
        "hypothesis_id": candidate["hypothesis_id"],
        "report_sha256": _sha256_bytes(report_raw),
        "forward_mean_hits_out_of_6": forward["mean_hits_out_of_6"],
        "forward_standard_error": forward["standard_error_of_mean_hits"],
        "random_expected_hits_out_of_6": forward["uniform_random_expected_hits_out_of_6"],
        "cv_mean_hits_out_of_6": cv["mean_hits_out_of_6"],
        "reverse_mean_hits_out_of_6": reverse["mean_hits_out_of_6"],
        "reverse_is_predictive_evidence": False,
        "method_registry_modified": False,
        "draw_numbers_reported": False,
    }


def _write_github_output(path: str | None, result: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        stream.write("claim_created=" + ("true" if result.get("claim_created") is True else "false") + "\n")
        stream.write("claim_resumed=" + ("true" if result.get("claim_resumed") is True else "false") + "\n")
        stream.write("claim_reused=" + ("true" if result.get("claim_reused") is True else "false") + "\n")
        if isinstance(result.get("claim_id"), str):
            stream.write("claim_id=" + result["claim_id"] + "\n")
        if isinstance(result.get("research_package_id"), str):
            stream.write("research_package_id=" + result["research_package_id"] + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    pre = sub.add_parser("preflight", help="Check that a fresh sealed interval exists before a model request.")
    pre.add_argument("--archive", type=Path, required=True)
    pre.add_argument("--ledger", type=Path, default=Path("general_evaluation_ledger.json"))
    claim = sub.add_parser("claim", help="Persist a one-shot claim before real-archive evaluation.")
    claim.add_argument("--archive", type=Path, required=True)
    claim.add_argument("--ledger", type=Path, default=Path("general_evaluation_ledger.json"))
    claim.add_argument("--proposals", type=Path, required=True)
    claim.add_argument("--specs", type=Path, required=True)
    claim.add_argument("--output", type=Path, default=Path("open_research_evaluation_claim.json"))
    claim.add_argument("--snapshot-directory", type=Path, default=Path(SNAPSHOT_DIRECTORY))
    claim.add_argument("--sealed-plan", type=Path, default=None)
    claim.add_argument("--package-id", default=None)
    claim.add_argument("--attempt-id", default=None)
    claim.add_argument("--github-output", type=str, default=None)
    pending = sub.add_parser("pending-claim", help="Check whether the exact active archive has a resumable sealed claim.")
    pending.add_argument("--archive", type=Path, required=True)
    pending.add_argument("--ledger", type=Path, default=Path("general_evaluation_ledger.json"))
    pending.add_argument("--github-output", type=str, default=None)
    run = sub.add_parser("evaluate", help="Run only a previously persisted one-shot claim.")
    run.add_argument("--claim-id", required=True)
    run.add_argument("--ledger", type=Path, default=Path("general_evaluation_ledger.json"))
    run.add_argument("--proposals", type=Path, required=True)
    run.add_argument("--specs", type=Path, required=True)
    run.add_argument("--output", type=Path, default=Path("open_research_evaluation_report.json"))

    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            result = preflight(args.archive, args.ledger)
        elif args.command == "pending-claim":
            pending_entry = pending_claim_for_archive(args.archive, args.ledger)
            result = {
                "status": "PENDING_CLAIM" if pending_entry is not None else "NO_PENDING_CLAIM",
                "pending_claim": pending_entry is not None,
                "claim_id": pending_entry["claim_id"] if pending_entry is not None else None,
                "target_date": pending_entry["target_date"] if pending_entry is not None else None,
                "numbers_stored": False,
            }
            if args.github_output:
                with Path(args.github_output).open("a", encoding="utf-8") as stream:
                    stream.write("pending_claim=" + ("true" if pending_entry is not None else "false") + "\n")
                    if pending_entry is not None:
                        stream.write("pending_claim_id=" + pending_entry["claim_id"] + "\n")
        elif args.command == "claim":
            result = create_claim(
                args.archive, args.ledger, args.proposals, args.specs,
                snapshot_directory=args.snapshot_directory,
                sealed_plan_path=args.sealed_plan,
                research_package_id=args.package_id,
                scheduler_attempt_id=args.attempt_id,
            )
            _write_json_atomic(args.output, result)
            _write_github_output(args.github_output, result)
        else:
            result = evaluate_claim(
                args.claim_id, args.ledger, args.proposals, args.specs,
                output_path=args.output,
            )
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        if args.command == "claim" and result.get("claim_created") is not True:
            print("REAL-ARCHIVE EVALUATION: SKIPPED; no one-shot claim was created.")
        return 0
    except Exception as exc:
        print("SEALED EVALUATION STOPPED: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
