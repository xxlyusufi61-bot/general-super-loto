#!/usr/bin/env python3
"""Fixed-scope, one-shot recovery preparation for the 2026-11-05 GENERAL target.

This helper never calls NVIDIA. It validates the immutable pre-timeout archive
snapshot and the single GitHub Actions dispatch that is permitted to run recovery.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

import general_data_guard as guard
import general_open_research as research
import general_sealed_evaluation as sealed

PROJECT_REPOSITORY = "xxlyusufi61-bot/general-super-loto"
RECOVERY_TARGET_DATE = "2026-11-05"
RECOVERY_SOURCE_COMMIT = "8be67c7ba561862aa81af988f3544ef5fbb7be71"
EXPECTED_ARCHIVE_SHA256 = "5319d759df2ab34bf15e8eed66fc85826b9628f7297daa39b6fd34803a40d452"
EXPECTED_ARCHIVE_BYTES = 140693
EXPECTED_ARCHIVE_RECORDS = 981
EXPECTED_FIRST_DATE = "2020-08-02"
RECOVERY_WORKFLOW_PATH = ".github/workflows/general-recovery-20261105.yml"
ALLOWED_ACTOR_LOGIN = "xxlyusufi61-bot"
ALLOWED_ACTOR_ID = 345811504
RECOVERY_CONFIRMATION_PHRASE = "I_AUTHORIZE_ONE_TIME_SUPER_LOTO_RECOVERY_2026_11_05_NO_RETRY"
PREVIOUS_PROVIDER_REQUESTS = 1
MAX_RECOVERY_PROVIDER_REQUESTS = 2
MAX_TOTAL_PROVIDER_REQUESTS = PREVIOUS_PROVIDER_REQUESTS + MAX_RECOVERY_PROVIDER_REQUESTS
API_ROOT = "https://api.github.com"
USER_AGENT = "GENERAL-One-Time-Target-Recovery/1.0"


class RecoveryError(RuntimeError):
    """A fixed-scope recovery precondition failed."""


def _normalise_rows(rows: Any, description: str) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise RecoveryError(description + " must contain a list of draw rows.")
    clean: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RecoveryError(description + " contains a malformed draw row.")
        if set(row) != {"date", "numbers"}:
            raise RecoveryError(description + " contains unexpected draw fields.")
        clean.append({"date": row["date"], "numbers": row["numbers"]})
    return clean


def _read_archive(path: str | Path, description: str) -> tuple[bytes, list[dict[str, Any]]]:
    archive_path = Path(path)
    try:
        raw = archive_path.read_bytes()
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RecoveryError(description + " could not be read as valid UTF-8 JSON.") from None
    if not isinstance(payload, dict) or set(payload) - {
        "source", "cross_check_source", "cross_check_validation",
        "multi_year_cross_source_validation", "fetched_at", "corrections_applied", "draws",
    }:
        raise RecoveryError(description + " has an unexpected archive structure.")
    if not isinstance(payload.get("draws"), list) or "records" in payload:
        raise RecoveryError(description + " must use the canonical draws field.")
    rows = _normalise_rows(payload["draws"], description)
    try:
        validation = guard.validate_archive(rows)
    except Exception:
        raise RecoveryError(description + " failed Data Guard validation.") from None
    if not isinstance(validation, dict) or validation.get("valid") is not True:
        raise RecoveryError(description + " did not pass Data Guard validation.")
    return raw, rows


def _summary_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "record_count": len(rows),
        "first_date": rows[0]["date"] if rows else None,
        "last_date": rows[-1]["date"] if rows else None,
    }


def prepare_recovery_archive(
    *,
    source_archive_path: str | Path,
    current_archive_path: str | Path,
    ledger_path: str | Path,
    output_path: str | Path,
    source_commit: str,
    metadata_path: str | Path | None = None,
) -> dict[str, Any]:
    """Verify and copy only the exact historical snapshot, then run sealed preflight."""
    if source_commit != RECOVERY_SOURCE_COMMIT:
        raise RecoveryError("Historical archive source commit does not match the fixed recovery pin.")

    source_raw, source_rows = _read_archive(source_archive_path, "Pinned historical archive")
    source_sha = hashlib.sha256(source_raw).hexdigest()
    if source_sha != EXPECTED_ARCHIVE_SHA256:
        raise RecoveryError("Pinned historical archive SHA-256 does not match the original failed attempt.")
    if len(source_raw) != EXPECTED_ARCHIVE_BYTES:
        raise RecoveryError("Pinned historical archive byte length does not match the original failed attempt.")

    summary = _summary_rows(source_rows)
    if (
        summary["record_count"] != EXPECTED_ARCHIVE_RECORDS
        or summary["first_date"] != EXPECTED_FIRST_DATE
        or summary["last_date"] != RECOVERY_TARGET_DATE
    ):
        raise RecoveryError("Pinned archive does not describe exactly the original 2026-11-05 target.")

    try:
        target, discovery_train = research.isolate_pre_target(source_rows)
    except Exception as exc:
        raise RecoveryError("Pinned archive target-isolation validation failed: " + type(exc).__name__) from None
    if target != RECOVERY_TARGET_DATE or len(discovery_train) != EXPECTED_ARCHIVE_RECORDS - 1:
        raise RecoveryError("Pinned archive pre-target partition differs from the original 2026-11-05 partition.")

    current_raw, current_rows = _read_archive(current_archive_path, "Current canonical archive")
    if len(current_rows) < len(source_rows):
        raise RecoveryError("Current canonical archive is missing rows from the pinned historical snapshot.")
    if current_rows[:len(source_rows)] != source_rows:
        raise RecoveryError("Current canonical archive's history through 2026-11-05 differs from the pinned snapshot.")
    if any(row["date"] <= RECOVERY_TARGET_DATE for row in current_rows[len(source_rows):]):
        raise RecoveryError("Current archive contains a non-chronological row after the pinned recovery prefix.")

    output = Path(output_path)
    if output.resolve() in {Path(source_archive_path).resolve(), Path(current_archive_path).resolve()}:
        raise RecoveryError("Recovery output must not overwrite either source archive.")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    try:
        temporary.write_bytes(source_raw)
        os.replace(temporary, output)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise RecoveryError("Could not atomically write the pinned recovery archive.") from None

    try:
        plan = sealed.preflight(output, ledger_path)
    except Exception as exc:
        output.unlink(missing_ok=True)
        raise RecoveryError("Pinned archive has no fresh, disjoint sealed windows: " + type(exc).__name__) from None

    if (
        plan.get("status") != "READY"
        or plan.get("target_date") != RECOVERY_TARGET_DATE
        or plan.get("target_numbers_exposed") is not False
        or plan.get("target_in_discovery") is not False
        or plan.get("target_in_validation") is not False
        or RECOVERY_TARGET_DATE in set(plan.get("forward_validation_dates", []))
        or RECOVERY_TARGET_DATE in set(plan.get("reverse_validation_dates", []))
        or plan.get("validation_dates_previously_consumed") is not False
        or plan.get("hosted_model_request_performed") is not False
        or plan.get("real_archive_evaluation_performed") is not False
    ):
        output.unlink(missing_ok=True)
        raise RecoveryError("Sealed preflight did not preserve the fixed target-isolation contract.")

    forward_count = len(plan.get("forward_validation_dates", []))
    reverse_count = len(plan.get("reverse_validation_dates", []))
    if (
        forward_count < sealed.MIN_VALIDATION_DRAWS
        or reverse_count < sealed.MIN_VALIDATION_DRAWS
        or set(plan.get("forward_validation_dates", [])).intersection(
            plan.get("reverse_validation_dates", [])
        )
    ):
        output.unlink(missing_ok=True)
        raise RecoveryError("Forward and reverse validation windows are missing or overlapping.")

    metadata = {
        "schema_version": 1,
        "status": "RECOVERY_ARCHIVE_READY",
        "target_date": RECOVERY_TARGET_DATE,
        "source_commit": RECOVERY_SOURCE_COMMIT,
        "archive_sha256": source_sha,
        "archive_byte_count": len(source_raw),
        "archive_record_count": len(source_rows),
        "pre_target_draw_count": len(discovery_train),
        "current_archive_record_count": len(current_rows),
        "current_archive_sha256": hashlib.sha256(current_raw).hexdigest(),
        "forward_validation_count": forward_count,
        "reverse_validation_count": reverse_count,
        "target_numbers_exposed": False,
        "target_in_discovery": False,
        "target_in_validation": False,
        "forward_reverse_holdouts_disjoint": True,
        "previously_consumed_holdout_dates_reused": False,
        "previous_provider_request_count": PREVIOUS_PROVIDER_REQUESTS,
        "maximum_new_provider_requests": MAX_RECOVERY_PROVIDER_REQUESTS,
        "maximum_total_target_provider_requests": MAX_TOTAL_PROVIDER_REQUESTS,
        "hosted_model_request_performed": False,
        "real_archive_evaluation_performed": False,
    }
    if metadata_path is not None:
        path = Path(metadata_path)
        path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return metadata


def _github_get_json(url: str, token: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if getattr(response, "status", 200) != 200:
                raise RecoveryError("GitHub Actions run-history API returned an unexpected status.")
            raw = response.read(2 * 1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RecoveryError("GitHub Actions one-shot authorization could not be verified.") from None
    if len(raw) > 2 * 1024 * 1024:
        raise RecoveryError("GitHub Actions run-history response exceeded the safety limit.")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise RecoveryError("GitHub Actions run-history response was invalid JSON.") from None
    if not isinstance(value, dict):
        raise RecoveryError("GitHub Actions run-history response had an invalid root.")
    return value


def verify_one_shot_dispatch(
    *,
    repository: str,
    run_id: str,
    run_attempt: str,
    event: str,
    ref: str,
    head_sha: str,
    confirmation: str,
    token: str,
    request_json: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Allow only the first original dispatch; reruns and later dispatches fail closed."""
    if repository != PROJECT_REPOSITORY:
        raise RecoveryError("Recovery is restricted to the canonical GENERAL repository.")
    if event != "workflow_dispatch" or ref != "refs/heads/main":
        raise RecoveryError("Recovery requires a manual dispatch from main.")
    if str(run_attempt) != "1":
        raise RecoveryError("Recovery workflow reruns are forbidden; the one-shot run is already consumed.")
    if confirmation != RECOVERY_CONFIRMATION_PHRASE:
        raise RecoveryError("The exact one-time recovery confirmation phrase is required.")
    if not run_id.isdigit() or not head_sha:
        raise RecoveryError("Current GitHub Actions run identity is incomplete.")
    if not isinstance(token, str) or not token.strip():
        raise RecoveryError("A GitHub token is required to verify one-shot authorization.")

    fetch = request_json or _github_get_json
    url = (
        API_ROOT + "/repos/" + PROJECT_REPOSITORY
        + "/actions/workflows/general-recovery-20261105.yml/runs?per_page=100"
    )
    history = fetch(url, token.strip())
    runs = history.get("workflow_runs")
    total_count = history.get("total_count")
    if (
        type(total_count) is not int
        or total_count != 1
        or not isinstance(runs, list)
        or len(runs) != 1
    ):
        raise RecoveryError("A previous or duplicate recovery workflow run exists; no second run is allowed.")

    run = runs[0]
    actor = run.get("actor") if isinstance(run, dict) else None
    if not isinstance(actor, dict):
        raise RecoveryError("GitHub did not return the authorized workflow actor.")
    checks = (
        str(run.get("id")) == run_id,
        run.get("run_attempt") == 1,
        run.get("event") == "workflow_dispatch",
        run.get("head_branch") == "main",
        run.get("head_sha") == head_sha,
        run.get("path") == RECOVERY_WORKFLOW_PATH,
        run.get("status") == "in_progress",
        actor.get("login") == ALLOWED_ACTOR_LOGIN,
        actor.get("id") == ALLOWED_ACTOR_ID,
    )
    if not all(checks):
        raise RecoveryError("GitHub run identity does not satisfy the fixed one-shot recovery policy.")

    return {
        "status": "ONE_TIME_RECOVERY_DISPATCH_AUTHORIZED",
        "run_id": run_id,
        "run_attempt": 1,
        "target_date": RECOVERY_TARGET_DATE,
        "maximum_new_provider_requests": MAX_RECOVERY_PROVIDER_REQUESTS,
        "maximum_total_target_provider_requests": MAX_TOTAL_PROVIDER_REQUESTS,
        "previous_provider_request_count": PREVIOUS_PROVIDER_REQUESTS,
        "github_run_history_count": total_count,
    }


def _write_github_output(path: str | None, values: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            rendered = str(value).lower() if isinstance(value, bool) else str(value)
            if "\n" in rendered or "\r" in rendered:
                raise RecoveryError("GitHub output values must be single-line.")
            stream.write(key + "=" + rendered + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("--archive", required=True, type=Path)
    prepare.add_argument("--current-archive", required=True, type=Path)
    prepare.add_argument("--ledger", required=True, type=Path)
    prepare.add_argument("--source-commit", required=True)
    prepare.add_argument("--output", required=True, type=Path)
    prepare.add_argument("--metadata", required=True, type=Path)
    prepare.add_argument("--github-output")

    authorize = sub.add_parser("authorize")
    authorize.add_argument("--repository", required=True)
    authorize.add_argument("--run-id", required=True)
    authorize.add_argument("--run-attempt", required=True)
    authorize.add_argument("--event", required=True)
    authorize.add_argument("--ref", required=True)
    authorize.add_argument("--head-sha", required=True)
    authorize.add_argument("--confirmation", required=True)
    authorize.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    authorize.add_argument("--github-output")

    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_recovery_archive(
                source_archive_path=args.archive,
                current_archive_path=args.current_archive,
                ledger_path=args.ledger,
                output_path=args.output,
                source_commit=args.source_commit,
                metadata_path=args.metadata,
            )
            _write_github_output(args.github_output, {
                "recovery_archive_ready": True,
                "recovery_target_date": result["target_date"],
                "recovery_archive_sha256": result["archive_sha256"],
                "recovery_forward_validation_count": result["forward_validation_count"],
                "recovery_reverse_validation_count": result["reverse_validation_count"],
            })
        else:
            result = verify_one_shot_dispatch(
                repository=args.repository,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                event=args.event,
                ref=args.ref,
                head_sha=args.head_sha,
                confirmation=args.confirmation,
                token=args.token,
            )
            _write_github_output(args.github_output, {
                "one_time_recovery_authorized": True,
                "recovery_run_id": result["run_id"],
            })
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print("GENERAL ONE-TIME RECOVERY BLOCKED: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
