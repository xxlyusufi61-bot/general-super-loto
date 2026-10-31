"""One-shot, leakage-free orchestration for a sealed chronological evaluation.

The live research model receives discovery rows only. A fixed DSL specification
can claim one non-overlapping R60 validation interval. The claim and immutable
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
from general_bidirectional_evaluation import evaluate_bidirectional_full_history
from general_hypothesis_execution import (
    VERSION as ENGINE_VERSION,
    MAX_HISTORY_DRAWS,
    MAX_VALIDATION_DRAWS,
    evaluate_holdout,
    rank_candidates_retrospective,
    evaluate_reverse_retrospective_stress,
    evaluate_time_ordered_cv,
    run_synthetic_contract_tests,
    validate_execution_spec,
)

VERSION = "0.2.0"
REGIME_BOUNDARY = date(2020, 8, 2)
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


def split_archive(rows: list[dict[str, Any]], *, archive_sha256: str = "") -> dict[str, Any]:
    """Create disjoint discovery and directional holdouts, per regime.

    The latest row is isolated as the current target. For R60, the oldest
    eligible pre-target rows are reserved for reverse-retrospective testing,
    the newest are reserved for forward walk-forward testing, and only the
    non-overlapping middle rows go to discovery. R54 remains discovery-only.
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

    by_regime: dict[str, list[dict[str, Any]]] = {"R54": [], "R60": []}
    for row in pre_target:
        day = date.fromisoformat(row["date"])
        regime = "R54" if day < REGIME_BOUNDARY else "R60"
        pool_size = 54 if regime == "R54" else 60
        if any(type(number) is not int or not 1 <= number <= pool_size for number in row["numbers"]):
            raise SealedEvaluationError(regime + " contains a number outside its allowed pool.")
        by_regime[regime].append(_copy_row(row))

    regimes: dict[str, dict[str, Any]] = {}
    all_discovery: list[dict[str, Any]] = []
    for regime, history in by_regime.items():
        pool_size = 54 if regime == "R54" else 60
        if regime == "R54":
            # This version deliberately does not run R54 real-archive evaluation.
            # Keep it as discovery context, even if a future archive grows longer.
            discovery = list(history)
            validation: list[dict[str, Any]] = []
            reverse_validation: list[dict[str, Any]] = []
            if len(history) < DEFAULT_MIN_TRAIN_DRAWS + 2 * MIN_VALIDATION_DRAWS:
                status = "NOT_EVALUABLE_INSUFFICIENT_HISTORY"
                reason = (
                    "R54 has fewer rows than the default minimum training history plus "
                    "two separate directional holdouts; the minimum was not lowered."
                )
            else:
                status = "DISCOVERY_ONLY_NOT_EVALUATED"
                reason = (
                    "R54 real-archive evaluation is not enabled in this version; "
                    "its rows remain discovery context only."
                )
        elif len(history) < DEFAULT_MIN_TRAIN_DRAWS + 2 * MIN_VALIDATION_DRAWS:
            discovery = list(history)
            validation = []
            reverse_validation = []
            status = "NOT_EVALUABLE_INSUFFICIENT_HISTORY"
            reason = (
                "The regime does not have enough rows for default training history plus "
                "two disjoint directional holdouts; the minimum was not lowered."
            )
        else:
            validation_count = min(
                DEFAULT_MAX_VALIDATION_DRAWS,
                (len(history) - DEFAULT_MIN_TRAIN_DRAWS) // 2,
            )
            if validation_count < MIN_VALIDATION_DRAWS:
                discovery = list(history)
                validation = []
                reverse_validation = []
                status = "NOT_EVALUABLE_INSUFFICIENT_HISTORY"
                reason = "Insufficient rows remain after preserving the default training minimum and two disjoint holdouts."
            else:
                reverse_validation = history[:validation_count]
                discovery = history[validation_count:-validation_count]
                validation = history[-validation_count:]
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
        regimes[regime] = {
            "regime": regime,
            "number_pool_size": pool_size,
            "status": status,
            "reason": reason,
            "pre_target_draw_count": len(history),
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
    return {
        "schema_version": 1,
        "orchestrator_version": VERSION,
        "engine_version": ENGINE_VERSION,
        "target_date": target_date,
        "archive_sha256": archive_sha256,
        "archive_record_count": len(rows),
        "pre_target_draw_count": len(pre_target),
        "target_numbers_exposed": False,
        "target_in_discovery": False,
        "target_in_validation": False,
        "target_in_reverse_validation": False,
        "directional_validation_windows_disjoint": True,
        "discovery_rows": all_discovery,
        "discovery_count": len(all_discovery),
        "regimes": regimes,
    }


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
        or set(payload) != {"schema_version", "purpose", "entries"}
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != LEDGER_SCHEMA_VERSION
        or not isinstance(payload.get("purpose"), str)
        or not isinstance(payload.get("entries"), list)
        or len(payload["entries"]) > 10000
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
        if set(parsed_forward).intersection(parsed_reverse):
            raise SealedEvaluationError("Forward and reverse validation dates overlap within a ledger entry.")
        overlap = used_dates.intersection(parsed_dates)
        if overlap:
            raise SealedEvaluationError("Evaluation ledger already contains overlapping directional validation dates.")
        seen_claims.add(claim_id)
        used_dates.update(parsed_dates)
    return payload


def public_plan(split: dict[str, Any]) -> dict[str, Any]:
    """Return partition metadata only; never include draw-number sets."""
    regimes = {}
    for name, item in split["regimes"].items():
        regimes[name] = {
            key: item[key]
            for key in (
                "regime", "number_pool_size", "status", "reason",
                "pre_target_draw_count", "discovery_count", "validation_count",
                "reverse_validation_count",
                "discovery_start_date", "discovery_end_date",
                "validation_start_date", "validation_end_date",
                "reverse_validation_start_date", "reverse_validation_end_date",
            )
        }
    return {
        key: split[key]
        for key in (
            "schema_version", "orchestrator_version", "engine_version",
            "target_date", "archive_sha256", "archive_record_count",
            "pre_target_draw_count", "target_numbers_exposed",
            "target_in_discovery", "target_in_validation",
            "discovery_count",
        )
    } | {"regimes": regimes}


def _used_validation_dates(ledger: dict[str, Any]) -> set[str]:
    return {
        day
        for entry in ledger["entries"]
        for day in entry["validation_dates"] + entry.get("reverse_validation_dates", [])
    }


def preflight(
    archive_path: str | Path,
    ledger_path: str | Path,
) -> dict[str, Any]:
    """Fail before any hosted-model request if no fresh validation interval exists."""
    archive = load_archive(archive_path)
    split = split_archive(archive["rows"], archive_sha256=archive["archive_sha256"])
    ledger = load_ledger(ledger_path)
    r60 = split["regimes"]["R60"]
    validation_dates = [row["date"] for row in r60["validation_rows"]]
    reverse_validation_dates = [row["date"] for row in r60["reverse_validation_rows"]]
    if len(validation_dates) < MIN_VALIDATION_DRAWS:
        raise SealedEvaluationError("R60 has no sufficiently sized forward sealed validation interval.")
    if len(reverse_validation_dates) < MIN_VALIDATION_DRAWS:
        raise SealedEvaluationError("R60 has no sufficiently sized reverse sealed validation interval.")
    if set(validation_dates).intersection(reverse_validation_dates):
        raise SealedEvaluationError("Forward and reverse validation intervals overlap.")
    overlap = sorted((set(validation_dates) | set(reverse_validation_dates)).intersection(_used_validation_dates(ledger)))
    if overlap:
        raise SealedEvaluationError(
            "Current sealed validation window overlaps already-consumed dates; stop before any model request."
        )
    if len(r60["discovery_rows"]) < DEFAULT_MIN_TRAIN_DRAWS:
        raise SealedEvaluationError("R60 discovery history is shorter than the default minimum training window.")
    result = public_plan(split)
    result.update({
        "status": "READY",
        "ledger_entry_count": len(ledger["entries"]),
        "validation_dates_previously_consumed": False,
        "forward_validation_dates": validation_dates,
        "reverse_validation_dates": reverse_validation_dates,
        "hosted_model_request_performed": False,
        "real_archive_evaluation_performed": False,
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
        or item.get("discovery_direction") not in {"forward", "reverse", "both"}
        for item in items
    ):
        return None
    forward_proposal = next(
        (item for item in items if item["discovery_direction"] in {"forward", "both"}),
        None,
    )
    reverse_proposal = next(
        (item for item in items if item["discovery_direction"] in {"reverse", "both"}),
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

    def freeze(item: dict[str, Any]) -> dict[str, Any]:
        if item.get("dsl_validated") is not True or item.get("synthetic_tests_passed") is not True:
            raise SealedEvaluationError("A directional specification did not pass DSL/synthetic validation.")
        spec = validate_execution_spec(item.get("execution_spec"))
        synthetic = run_synthetic_contract_tests(spec)
        if synthetic.get("status") != "PASS" or synthetic.get("real_archive_used") is not False:
            raise SealedEvaluationError("A directional specification failed its synthetic-only contract test.")
        return {
            "execution_spec": spec,
            "spec_sha256": _sha256_bytes(_canonical_json(spec)),
            "synthetic_test_summary": synthetic,
        }

    forward = freeze(specs_by_id[forward_id])
    reverse = freeze(specs_by_id[reverse_id])
    return {
        "hypothesis_id": forward_id,
        "discovery_direction": forward_proposal["discovery_direction"],
        **forward,
        "reverse_hypothesis_id": reverse_id,
        "reverse_discovery_direction": reverse_proposal["discovery_direction"],
        "reverse_execution_spec": reverse["execution_spec"],
        "reverse_spec_sha256": reverse["spec_sha256"],
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
) -> dict[str, Any]:
    """Claim one fresh interval; call this before running a real-archive score."""
    candidate = _select_one_frozen_candidate(proposals_path, specs_path)
    if candidate is None:
        return {
            "status": "SKIPPED_NO_SINGLE_FROZEN_CANDIDATE",
            "claim_created": False,
            "real_archive_evaluation_performed": False,
            "reason": "Need exactly one DSL-valid specification matching the first discovery-prioritized proposal.",
        }
    archive = load_archive(archive_path)
    split = split_archive(archive["rows"], archive_sha256=archive["archive_sha256"])
    ledger = load_ledger(ledger_path)
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
        "validation_dates": validation_dates,
        "reverse_validation_dates": reverse_validation_dates,
        "hypothesis_id": candidate["hypothesis_id"],
        "spec_sha256": candidate["spec_sha256"],
        "reverse_hypothesis_id": candidate["reverse_hypothesis_id"],
        "reverse_spec_sha256": candidate["reverse_spec_sha256"],
        "engine_version": ENGINE_VERSION,
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
        "state": "CLAIMED",
        "claimed_at_utc": datetime.now(timezone.utc).isoformat(),
        "orchestrator_version": VERSION,
        "engine_version": ENGINE_VERSION,
        "target_date": split["target_date"],
        "archive_sha256": archive["archive_sha256"],
        "archive_record_count": archive["record_count"],
        "archive_snapshot_path": str(snapshot_path).replace("\\", "/"),
        "hypothesis_id": candidate["hypothesis_id"],
        "spec_sha256": candidate["spec_sha256"],
        "reverse_hypothesis_id": candidate["reverse_hypothesis_id"],
        "reverse_spec_sha256": candidate["reverse_spec_sha256"],
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
    _write_json_atomic(Path(ledger_path), ledger)
    return {
        "status": "CLAIMED",
        "claim_created": True,
        "claim_id": claim_id,
        "target_date": split["target_date"],
        "archive_sha256": archive["archive_sha256"],
        "hypothesis_id": candidate["hypothesis_id"],
        "spec_sha256": candidate["spec_sha256"],
        "validation_start_date": validation[0]["date"],
        "validation_end_date": validation[-1]["date"],
        "validation_draw_count": len(validation),
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


def _evaluate_full_reverse_history(
    pre_target_history: list[dict[str, Any]],
    *,
    cutoff_date: str,
    number_pool_size: int,
    execution_spec: dict[str, Any],
    min_train_draws: int = DEFAULT_MIN_TRAIN_DRAWS,
) -> dict[str, Any]:
    """Evaluate every reverse-eligible row in bounded batches; return aggregates only."""
    eligible_count = len(pre_target_history) - min_train_draws
    if eligible_count < 1:
        raise SealedEvaluationError("Not enough later historical rows for reverse stress testing.")

    hit_distribution = {str(hits): 0 for hits in range(7)}
    offset = 0
    first_test_date = None
    last_test_date = None
    previous_batch_last_date = None

    while offset < eligible_count:
        batch_size = min(MAX_VALIDATION_DRAWS, eligible_count - offset)
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
    min_train_draws: int = DEFAULT_MIN_TRAIN_DRAWS,
) -> dict[str, Any]:
    """Score the oldest sealed interval against strictly later historical rows only."""
    if len(reverse_validation) < MIN_VALIDATION_DRAWS:
        raise SealedEvaluationError("Reverse holdout is too short.")
    target_day = date.fromisoformat(isolated_target_date)
    history_by_date = {row["date"]: row for row in full_pre_target_history}
    if len(history_by_date) != len(full_pre_target_history):
        raise SealedEvaluationError("Reverse evaluation history has duplicate dates.")
    if any(
        date.fromisoformat(row["date"]) >= target_day
        for row in full_pre_target_history + reverse_validation
    ):
        raise SealedEvaluationError("Reverse evaluation includes the current target or a later draw.")
    details = []
    for target in sorted(reverse_validation, key=lambda row: row["date"], reverse=True):
        target_date = target["date"]
        later_history = [
            row for day, row in sorted(history_by_date.items())
            if day > target_date
        ][-MAX_HISTORY_DRAWS:]
        ranking = rank_candidates_retrospective(
            later_history,
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
            "later_training_draw_count": len(later_history),
            "hit_column_positions_1_to_6": matched_positions,
            "hit_count": len(matched_positions),
            "target_numbers_reported": False,
            "predicted_number_values_reported": False,
        })
    hit_distribution = {
        str(hits): sum(row["hit_count"] == hits for row in details)
        for hits in range(7)
    }
    return {
        "status": "EVALUATED_RETROSPECTIVE_ONLY",
        "direction": "reverse_retrospective",
        "predictive_evidence": False,
        "evaluation_order": "newest_to_oldest",
        "hypothesis_id": hypothesis_id,
        "spec_sha256": spec_sha256,
        "test_draw_count": len(details),
        "first_test_date": details[0]["test_date"],
        "last_test_date": details[-1]["test_date"],
        "mean_hits_out_of_6": round(
            sum(row["hit_count"] for row in details) / len(details), 6
        ),
        "uniform_random_expected_hits_out_of_6": 0.6,
        "hit_distribution": hit_distribution,
        "per_draw": details,
        "target_number_sets_reported": False,
        "predicted_number_values_reported": False,
        "interpretation": (
            "This direction-specific fixed-specification test uses only draws later than each test date. "
            "Because it uses later historical information, it is retrospective stress testing only and "
            "must never be interpreted as evidence of forward prediction."
        ),
    }


def evaluate_claim(
    claim_id: str,
    ledger_path: str | Path,
    proposals_path: str | Path,
    specs_path: str | Path,
    *,
    output_path: str | Path,
) -> dict[str, Any]:
    """Evaluate one already-persisted claim against its immutable archive snapshot."""
    ledger = load_ledger(ledger_path)
    entry = _find_claim(ledger, claim_id)
    candidate = _select_one_frozen_candidate(proposals_path, specs_path)
    if candidate is None:
        raise SealedEvaluationError("Frozen candidate disappeared or no longer matches the parsed proposal.")
    if candidate["hypothesis_id"] != entry["hypothesis_id"] or candidate["spec_sha256"] != entry["spec_sha256"]:
        raise SealedEvaluationError("Candidate changed after the one-shot validation claim.")
    snapshot_path = Path(entry["archive_snapshot_path"])
    archive = load_archive(snapshot_path)
    if archive["archive_sha256"] != entry["archive_sha256"]:
        raise SealedEvaluationError("Immutable archive snapshot hash does not match the ledger.")
    split = split_archive(archive["rows"], archive_sha256=archive["archive_sha256"])
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
    discovery = r60["discovery_rows"]
    validation = r60["validation_rows"]
    reverse_validation = r60["reverse_validation_rows"]
    spec = candidate["execution_spec"]
    reverse_spec = candidate["reverse_execution_spec"]
    if (
        len(discovery) < DEFAULT_MIN_TRAIN_DRAWS
        or len(validation) < MIN_VALIDATION_DRAWS
        or len(reverse_validation) < MIN_VALIDATION_DRAWS
    ):
        raise SealedEvaluationError("Claim no longer satisfies both directional evaluation minimums.")

    forward = evaluate_holdout(
        discovery,
        validation,
        cutoff_date=split["target_date"],
        number_pool_size=60,
        execution_spec=spec,
        min_train_draws=DEFAULT_MIN_TRAIN_DRAWS,
    )
    folds = min(5, len(validation))
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
        min_train_draws=DEFAULT_MIN_TRAIN_DRAWS,
    )
    reverse = _evaluate_full_reverse_history(
        full_r60_history,
        cutoff_date=split["target_date"],
        number_pool_size=60,
        execution_spec=spec,
        min_train_draws=DEFAULT_MIN_TRAIN_DRAWS,
    )
    r54 = split["regimes"]["R54"]
    full_r54_history = sorted(
        r54["discovery_rows"] + r54["validation_rows"] + r54["reverse_validation_rows"],
        key=lambda row: row["date"],
    )
    bidirectional = evaluate_bidirectional_full_history(
        {"R54": full_r54_history, "R60": full_r60_history},
        isolated_target_date=split["target_date"],
        min_train_draws=DEFAULT_MIN_TRAIN_DRAWS,
    )
    bidirectional_r60 = bidirectional["regimes"]["R60"]
    report = {
        "schema_version": 1,
        "status": "EVALUATED_EXPLORATORY_FIXED_SPEC",
        "orchestrator_version": VERSION,
        "engine_version": ENGINE_VERSION,
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
        "r54_status": r54["status"],
        "r54_reason": r54["reason"],
        "forward_walk_forward": forward,
        "time_ordered_cross_validation": cv,
        "reverse_retrospective_stress_test_of_forward_candidate": reverse,
        "reverse_discovered_candidate_holdout": reverse_candidate_holdout,
        "bidirectional_full_history_adaptive_diagnostic": bidirectional,
        "candidate_selection": {
            "rule": "Freeze the first eligible forward/both proposal and the first eligible reverse/both proposal independently; one exact matching DSL spec for each direction.",
            "forward_hypothesis_id": candidate["hypothesis_id"],
            "forward_spec_sha256": candidate["spec_sha256"],
            "reverse_hypothesis_id": candidate["reverse_hypothesis_id"],
            "reverse_spec_sha256": candidate["reverse_spec_sha256"],
            "evaluated_directional_specification_count": 2 if candidate["hypothesis_id"] != candidate["reverse_hypothesis_id"] else 1,
            "selection_used_validation_scores": False,
            "automatic_candidate_selection": False,
            "registry_promotion": False,
        },
        "interpretation_limits": [
            "This is one exploratory fixed-specification evaluation, not proof of a predictive mechanism.",
            "The blocked CV folds reuse the same sealed validation interval and are correlated summaries, not independent confirmation.",
            "The forward candidate's full-history reverse result uses later historical draws and is retrospective stress testing only, not predictive evidence.",
            "The separately reverse-discovered candidate is evaluated only on the disjoint oldest R60 holdout, using strictly later history; this is retrospective only and not predictive evidence.",
            "The uniform-random expected overlap is 36/60 = 0.6 hits per six-number set; report uncertainty and do not infer an advantage from mean hits alone.",
            "Only one candidate is evaluated; no model-selected alternatives are scored on this interval.",
            "R54 is not evaluated because the current archive does not meet the default 40-draw training minimum plus a separate validation window. That minimum was not reduced.",
            "The full-history bidirectional adaptive diagnostic is a separate pre-registered safe-DSL baseline; it does not implement or approve A->B->C, A->T, or MOD5->T.",
            "For that diagnostic, each forward target is selected/scored using only earlier draws; each reverse target uses only later draws and is strictly retrospective.",
            "Its per-draw report records the target date, selected safe-catalog ID, hit count, and matched top-six column positions 1-6, but no target or predicted number values.",
            "The full-history adaptive diagnostic uses repeated overlapping selection windows; results are exploratory and are not independent confirmations.",
            "The reverse-discovered candidate report includes per-date hit count and matched top-six column positions, but excludes actual target numbers and predicted number values.",
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
        "reverse_candidate_holdout_predictive_evidence": False,
        "reverse_candidate_holdout_spec_sha256": reverse_candidate_holdout["spec_sha256"],
        "bidirectional_catalog_forward_mean_hits_out_of_6": bidirectional_r60["forward"]["mean_hits_out_of_6"],
        "bidirectional_catalog_forward_draw_count": bidirectional_r60["forward"]["evaluated_draw_count"],
        "bidirectional_catalog_reverse_mean_hits_out_of_6": bidirectional_r60["reverse_retrospective"]["mean_hits_out_of_6"],
        "bidirectional_catalog_reverse_draw_count": bidirectional_r60["reverse_retrospective"]["evaluated_draw_count"],
        "bidirectional_catalog_reverse_predictive_evidence": False,
    }
    entry["per_draw_values_stored"] = False
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
        if isinstance(result.get("claim_id"), str):
            stream.write("claim_id=" + result["claim_id"] + "\n")


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
    claim.add_argument("--github-output", type=str, default=None)
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
        elif args.command == "claim":
            result = create_claim(
                args.archive, args.ledger, args.proposals, args.specs,
                snapshot_directory=args.snapshot_directory,
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
