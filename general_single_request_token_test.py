#!/usr/bin/env python3
"""One-forward-request validation of GENERAL's normal 8,192-token SDK path.

This runner uses the ordinary blank-slate prompt builder and call_model path,
but never stores the model text, proposals, evaluation claims, or memory updates.
The workflow temporarily unlocks and restores the global lock around this runner.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import general_open_research as research
import general_sealed_evaluation as sealed
import general_research_memory as research_memory
from general_live_consent import has_explicit_single_request_consent

RESULT_PATH = Path(__file__).resolve().parent / "general_single_request_token_test_result.json"
VERSION = "1.0.0"
RESEARCH_SCOPE_START = date(2020, 8, 2)


def _validate_environment() -> None:
    if os.environ.get("GENERAL_SINGLE_REQUEST_MODE", "").strip().lower() != "true":
        raise RuntimeError("Normal one-request mode is not explicitly enabled; no provider request was sent.")
    if os.environ.get("GENERAL_DIAGNOSTIC_MODE", "").strip().lower() == "true":
        raise RuntimeError("The normal token test cannot run in diagnostic mode.")
    if not has_explicit_single_request_consent(os.environ.get("GENERAL_CONFIRM_LIVE_CALLS", "")):
        raise RuntimeError("The exact one-request normal-mode approval is missing; no provider request was sent.")
    if os.environ.get("GENERAL_NVIDIA_GUARD_PASSED") != "1":
        raise RuntimeError("The unlocked global safety-lock check was not confirmed; no provider request was sent.")
    if os.environ.get("GENERAL_ALLOW_NVIDIA_CALL", "").strip().lower() != "true":
        raise RuntimeError("The workflow did not explicitly enable its one approved NVIDIA request.")
    if os.environ.get("GENERAL_NVIDIA_FREE_USAGE_VERIFIED", "").strip().lower() != "true":
        raise RuntimeError("The explicit free-use attestation is missing; no provider request was sent.")
    if not os.environ.get("NVIDIA_API_KEY", "").strip():
        raise RuntimeError("NVIDIA_API_KEY is missing; no provider request was sent.")
    if research.DEFAULT_MAX_COMPLETION_TOKENS != 8192:
        raise RuntimeError("The normal research token budget is not the expected 8,192.")
    if research._NVIDIA_REQUESTS_ATTEMPTED != 0 or research._NVIDIA_REQUEST_LABELS_ATTEMPTED:
        raise RuntimeError("A provider request was already attempted in this process; token test stopped.")


def _validate_forward_partition(
    target: str,
    discovery_rows: list[dict[str, Any]],
    partition: dict[str, Any],
) -> tuple[list[str], list[str]]:
    if partition.get("target_date") != target:
        raise RuntimeError("The sealed partition target does not match Data Guard.")
    if partition.get("discovery_rows") != discovery_rows:
        raise RuntimeError("The sealed discovery partition changed before prompt construction.")
    if partition.get("target_numbers_exposed") is not False:
        raise RuntimeError("The sealed target-isolation proof did not pass.")
    regimes = partition.get("regimes")
    if not isinstance(regimes, dict) or not isinstance(regimes.get("R60"), dict):
        raise RuntimeError("The sealed partition does not contain the active R60 regime.")
    regime = regimes["R60"]
    if regime.get("status") != "READY":
        raise RuntimeError("Fresh, disjoint forward and reverse holdouts are not ready.")
    forward_rows = regime.get("validation_rows")
    reverse_rows = regime.get("reverse_validation_rows")
    if not isinstance(forward_rows, list) or not isinstance(reverse_rows, list):
        raise RuntimeError("The directional holdout partitions are malformed.")

    def dates_of(rows: list[dict[str, Any]], label: str) -> list[str]:
        dates: list[str] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("date"), str):
                raise RuntimeError(label + " contains a malformed row.")
            value = row["date"]
            try:
                parsed = date.fromisoformat(value)
            except ValueError:
                raise RuntimeError(label + " contains an invalid date.") from None
            if parsed.isoformat() != value:
                raise RuntimeError(label + " contains a non-canonical date.")
            dates.append(value)
        return dates

    discovery_dates = dates_of(discovery_rows, "Discovery partition")
    forward_dates = dates_of(forward_rows, "Forward holdout")
    reverse_dates = dates_of(reverse_rows, "Reverse holdout")
    target_day = date.fromisoformat(target)
    if target_day.isoformat() != target:
        raise RuntimeError("The isolated target date is not canonical.")
    if len(discovery_dates) != len(set(discovery_dates)):
        raise RuntimeError("Discovery contains duplicate dates.")
    if any(
        date.fromisoformat(value) < RESEARCH_SCOPE_START
        or date.fromisoformat(value) >= target_day
        for value in discovery_dates + forward_dates + reverse_dates
    ):
        raise RuntimeError("A pre-scope, target, or future record entered a pre-target partition.")
    if set(discovery_dates).intersection(forward_dates + reverse_dates):
        raise RuntimeError("A sealed holdout date entered model discovery.")
    if set(forward_dates).intersection(reverse_dates):
        raise RuntimeError("Forward and reverse sealed holdout windows overlap.")
    if target in set(discovery_dates + forward_dates + reverse_dates):
        raise RuntimeError("The isolated target entered discovery or a sealed holdout.")
    if not discovery_dates:
        raise RuntimeError("No eligible discovery history remains before the isolated target.")
    return forward_dates, reverse_dates


def _sha256_if_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_token_test(
    *,
    archive_path: Path = research.ARCHIVE,
    ledger_path: Path | None = None,
    memory_path: Path | None = None,
    registry_path: Path | None = None,
    result_path: Path = RESULT_PATH,
) -> dict[str, Any]:
    """Run one normal-mode forward request; all persistent project stores remain read-only."""
    _validate_environment()
    result_path.unlink(missing_ok=True)
    for marker in (Path("/tmp/general_nvidia_api_attempted"), Path("/tmp/general_nvidia_api_succeeded")):
        marker.unlink(missing_ok=True)

    resolved_ledger = Path(ledger_path) if ledger_path is not None else research.BASE / "general_evaluation_ledger.json"
    resolved_memory = Path(memory_path) if memory_path is not None else research.BASE / "general_research_memory.json"
    resolved_registry = Path(registry_path) if registry_path is not None else research.BASE / "general_method_registry.json"

    preflight = sealed.preflight(archive_path, resolved_ledger)
    if not isinstance(preflight, dict) or preflight.get("status") != "READY":
        raise RuntimeError("The sealed forward/reverse preflight did not return READY.")

    rows = research.parse_archive(archive_path)
    target, discovery_rows, _public_plan = research.isolate_discovery_only(
        rows, ledger_path=resolved_ledger
    )
    archive_proof = sealed.load_archive(archive_path)
    archive_sha256 = archive_proof.get("archive_sha256")
    if not isinstance(archive_sha256, str) or len(archive_sha256) != 64:
        raise RuntimeError("The validated archive did not provide a canonical SHA-256 proof.")
    if archive_proof.get("rows") != rows:
        raise RuntimeError("The archive changed between Data Guard and sealed partitioning.")

    current_ledger = sealed.load_ledger(resolved_ledger)
    used_dates = sealed.get_used_validation_dates(current_ledger)
    partition = sealed.split_archive(
        rows,
        archive_sha256=archive_sha256,
        used_validation_dates=used_dates,
    )
    forward_dates, reverse_dates = _validate_forward_partition(
        target, discovery_rows, partition
    )

    if _sha256_if_file(archive_path) != archive_sha256:
        raise RuntimeError("Archive bytes changed after validation.")
    ledger_before = _sha256_if_file(resolved_ledger)
    memory_before = _sha256_if_file(resolved_memory)
    registry_before = _sha256_if_file(resolved_registry)

    forward_memory = research_memory.direction_context(
        "forward", target, memory_path=resolved_memory
    )
    messages = research.build_messages(
        target,
        discovery_rows,
        "Validate the ordinary GENERAL normal-mode completion budget with one forward-only request.",
        direction="forward",
        prior_memory=forward_memory,
    )
    if len(messages) != 2 or messages[0].get("role") != "system" or messages[1].get("role") != "user":
        raise RuntimeError("The normal forward research prompt has an invalid message structure.")
    payload = json.loads(messages[1]["content"])
    if (
        payload.get("discovery_direction") != "forward"
        or payload.get("history_order") != "oldest_to_newest"
        or payload.get("target_date_metadata_only") != target
        or payload.get("post_target_records_included") is not False
        or payload.get("discovery_history_by_regime", {}).get("R60") != discovery_rows
    ):
        raise RuntimeError("The outbound normal research prompt failed its forward target-isolation checks.")
    if set(row["date"] for row in discovery_rows).intersection(forward_dates + reverse_dates):
        raise RuntimeError("A sealed holdout is present in the outbound prompt.")

    usage_path = research.USAGE_REPORT.with_name(
        research.USAGE_REPORT.stem + "_forward" + research.USAGE_REPORT.suffix
    )
    usage_path.unlink(missing_ok=True)
    response_text = research.call_model(messages, request_label="forward")
    if not isinstance(response_text, str) or not response_text.strip():
        raise RuntimeError("The provider returned no usable final message.content.")
    if research._NVIDIA_REQUESTS_ATTEMPTED != 1:
        raise RuntimeError("The normal-mode token test did not make exactly one provider request.")
    if research._NVIDIA_REQUEST_LABELS_ATTEMPTED != {"forward"}:
        raise RuntimeError("The normal-mode token test was not forward-only.")

    usage = json.loads(usage_path.read_text(encoding="utf-8"))
    if (
        usage.get("max_tokens_requested") != 8192
        or usage.get("model_finish_reason") != "stop"
        or usage.get("response_complete") is not True
        or usage.get("automatic_retries") != 0
        or usage.get("sdk_max_retries") != 0
        or usage.get("stream_requested") is not False
    ):
        raise RuntimeError("The live response did not prove a complete, non-streaming 8,192-token normal-mode request.")

    unchanged = {
        "archive": _sha256_if_file(archive_path) == archive_sha256,
        "evaluation_ledger": _sha256_if_file(resolved_ledger) == ledger_before,
        "research_memory": _sha256_if_file(resolved_memory) == memory_before,
        "method_registry": _sha256_if_file(resolved_registry) == registry_before,
    }
    if not all(unchanged.values()):
        raise RuntimeError("A canonical project input/store changed during the read-only token test.")

    result = {
        "schema_version": 1,
        "runner_version": VERSION,
        "status": "SUCCESS",
        "mode": "normal_research_single_forward_request",
        "model": research.MODEL,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "archive_sha256": archive_sha256,
        "archive_record_count": len(rows),
        "target_date_metadata_only": target,
        "discovery_direction": "forward",
        "discovery_history_order": "oldest_to_newest",
        "discovery_rows_sent": len(discovery_rows),
        "forward_holdout_rows_withheld": len(forward_dates),
        "reverse_holdout_rows_withheld": len(reverse_dates),
        "sealed_validation_rows_sent": 0,
        "target_values_sent": False,
        "target_or_later_rows_sent": False,
        "reverse_request_sent": False,
        "provider_requests_attempted": 1,
        "automatic_retries": 0,
        "max_tokens_requested": 8192,
        "request_mode": "ordinary call_model normal-mode branch",
        "response_complete": True,
        "finish_reason": "stop",
        "raw_response_persisted": False,
        "hypotheses_saved_or_promoted": False,
        "real_archive_evaluation_performed": False,
        "evaluation_ledger_modified": False,
        "research_memory_modified": False,
        "method_registry_modified": False,
        "canonical_inputs_unchanged": unchanged,
    }
    _write_result(result_path, result)
    return result


def main() -> int:
    try:
        result = run_token_test()
    except Exception as error:
        failure = {
            "schema_version": 1,
            "runner_version": VERSION,
            "status": "FAILED",
            "failure_type": type(error).__name__,
            "provider_requests_attempted": research._NVIDIA_REQUESTS_ATTEMPTED,
            "raw_response_persisted": False,
        }
        try:
            _write_result(RESULT_PATH, failure)
        except OSError:
            print("Could not write sanitized one-request token-test status.")
        print("GENERAL NORMAL-MODE TOKEN TEST FAILED:", type(error).__name__)
        return 1
    print("GENERAL NORMAL-MODE TOKEN TEST: SUCCESS")
    print("Mode: ordinary call_model branch; forward only")
    print("Provider requests:", result["provider_requests_attempted"], "| automatic retries: 0")
    print("max_tokens:", result["max_tokens_requested"], "| finish_reason:", result["finish_reason"])
    print("Target values sent: NO | reverse request sent: NO")
    print("Ledger, memory, registry writes: NONE")
    print("Raw model response persisted: NO")
    print("Result:", RESULT_PATH.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
