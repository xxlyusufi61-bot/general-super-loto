#!/usr/bin/env python3
"""Fail-closed one-time GitHub Actions entry point for the Cycle #55 retry.

Only the provenance-pinned 2026-11-10 FIFO target is in scope. This module
verifies the live workflow-dispatch record before preparation or reservation.
It never calls NVIDIA, Telegram, unlocks the provider lock, or changes the
free-only authorization.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import general_autonomous_research as autonomous

API_ROOT = "https://api.github.com"
API_TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 256 * 1024


class ManualRetryDispatchError(RuntimeError):
    """Raised when the one-time manual retry dispatch cannot be authenticated."""


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ManualRetryDispatchError(name + " is required.")
    return value


def verify_dispatch(confirmation: str, action: str) -> dict[str, Any]:
    """Authenticate the exact in-progress workflow run through GitHub's API."""
    if confirmation != autonomous.CYCLE55_MANUAL_RETRY_CONFIRMATION:
        raise ManualRetryDispatchError("Exact one-time Cycle #55 confirmation phrase is required.")
    if action != "cycle55-manual-retry":
        raise ManualRetryDispatchError("Only the fixed cycle55-manual-retry action is accepted.")
    repository = _required_env("GITHUB_REPOSITORY")
    run_id = _required_env("GITHUB_RUN_ID")
    head_sha = _required_env("GITHUB_SHA")
    if repository != "xxlyusufi61-bot/general-super-loto":
        raise ManualRetryDispatchError("Repository identity does not match the authorized project.")
    if (
        _required_env("GITHUB_EVENT_NAME") != "workflow_dispatch"
        or _required_env("GITHUB_REF") != "refs/heads/main"
        or _required_env("GITHUB_RUN_ATTEMPT") != "1"
    ):
        raise ManualRetryDispatchError("Manual retry requires a first-attempt workflow_dispatch on main.")
    if not run_id.isdigit() or len(run_id) > 32:
        raise ManualRetryDispatchError("GitHub Actions run ID is invalid.")
    if len(head_sha) != 40 or any(ch not in "0123456789abcdef" for ch in head_sha):
        raise ManualRetryDispatchError("GitHub dispatch head SHA is invalid.")
    token = _required_env("GITHUB_TOKEN")
    url = f"{API_ROOT}/repos/{repository}/actions/runs/{run_id}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "GENERAL-Cycle55-One-Time-Retry/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=API_TIMEOUT_SECONDS) as response:
            if getattr(response, "status", 200) != 200:
                raise ManualRetryDispatchError("GitHub Actions run metadata request did not return HTTP 200.")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ManualRetryDispatchError("GitHub Actions run metadata request failed with HTTP " + str(exc.code) + ".") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ManualRetryDispatchError("GitHub Actions run metadata could not be verified; no retry was reserved.") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ManualRetryDispatchError("GitHub Actions run metadata exceeded its size limit.")
    try:
        run = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise ManualRetryDispatchError("GitHub Actions run metadata was not valid JSON.") from None
    if not isinstance(run, dict):
        raise ManualRetryDispatchError("GitHub Actions run metadata root is invalid.")

    actor = run.get("actor")
    triggering_actor = run.get("triggering_actor")
    if not isinstance(actor, dict) or not isinstance(triggering_actor, dict):
        raise ManualRetryDispatchError("GitHub dispatch actor metadata is missing.")
    if (
        str(run.get("id")) != run_id
        or run.get("event") != "workflow_dispatch"
        or run.get("status") != "in_progress"
        or type(run.get("run_attempt")) is not int
        or run.get("run_attempt") != 1
        or run.get("head_branch") != "main"
        or run.get("head_sha") != head_sha
        or run.get("workflow_id") != autonomous.CYCLE55_MANUAL_RETRY_WORKFLOW_ID
        or run.get("path") != autonomous.CYCLE55_MANUAL_RETRY_WORKFLOW_PATH
        or actor.get("login") != autonomous.CYCLE55_MANUAL_RETRY_ACTOR_LOGIN
        or actor.get("id") != autonomous.CYCLE55_MANUAL_RETRY_ACTOR_ID
        or triggering_actor.get("login") != autonomous.CYCLE55_MANUAL_RETRY_ACTOR_LOGIN
        or triggering_actor.get("id") != autonomous.CYCLE55_MANUAL_RETRY_ACTOR_ID
    ):
        raise ManualRetryDispatchError("GitHub run, workflow, actor, branch, or rerun identity failed the one-time guard.")
    created_at = run.get("created_at")
    if not isinstance(created_at, str):
        raise ManualRetryDispatchError("GitHub run creation timestamp is missing.")
    try:
        autonomous._timestamp(created_at, "github_run.created_at")
    except autonomous.AutonomousResearchError:
        raise ManualRetryDispatchError("GitHub run creation timestamp is invalid.") from None

    return {
        "id": run_id,
        "event": run["event"],
        "status": run["status"],
        "run_attempt": run["run_attempt"],
        "head_branch": run["head_branch"],
        "head_sha": run["head_sha"],
        "workflow_id": run["workflow_id"],
        "path": run["path"],
        "actor_login": actor["login"],
        "actor_id": actor["id"],
        "created_at": created_at,
    }


def _write_outputs(path: str | None, values: dict[str, Any]) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            if value is None:
                continue
            rendered = "true" if value is True else "false" if value is False else str(value)
            if "\n" in rendered or "\r" in rendered:
                raise ManualRetryDispatchError("GitHub output values must be single-line.")
            stream.write(key + "=" + rendered + "\n")


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--confirmation", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--state", type=Path, default=Path("general_autonomous_research_state.json"))
    parser.add_argument("--archive", type=Path, default=Path("super_loto_archive.json"))
    parser.add_argument("--ledger", type=Path, default=Path("general_evaluation_ledger.json"))
    parser.add_argument("--global-lock", type=Path, default=Path("general_nvidia_lock.json"))
    parser.add_argument("--github-output")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    _common_arguments(prepare)
    claim = sub.add_parser("claim")
    _common_arguments(claim)
    args = parser.parse_args(argv)

    try:
        run = verify_dispatch(args.confirmation, args.action)
        state = autonomous.read_state(args.state)
        if args.command == "prepare":
            result = autonomous.prepare_cycle55_manual_retry(
                state,
                archive_path=args.archive,
                ledger_path=args.ledger,
                global_lock_path=args.global_lock,
            )
        else:
            plan = autonomous.prepare_cycle55_manual_retry(
                state,
                archive_path=args.archive,
                ledger_path=args.ledger,
                global_lock_path=args.global_lock,
            )
            result = autonomous.claim_cycle55_manual_retry(
                state,
                plan,
                github_run=run,
                confirmation=args.confirmation,
            )
            autonomous.write_state(args.state, state)

        outputs = {
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
            "attempt_id": result.get("attempt_id"),
            "exception_consumed": result.get("exception_consumed") is True,
        }
        _write_outputs(args.github_output, outputs)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
        return 0
    except Exception as exc:
        print("CYCLE #55 MANUAL RETRY BLOCKED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
