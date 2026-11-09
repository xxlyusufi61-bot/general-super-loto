#!/usr/bin/env python3
"""Fail-closed global safety lock for every GENERAL NVIDIA API workflow."""
import argparse
import atexit
import base64
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

LOCK_PATH = "general_nvidia_lock.json"
RATE_LIMIT_PATH = "general_nvidia_rate_limit.json"
RATE_LIMIT_BRANCH = "general-nvidia-rate-limit-state"
NVIDIA_REQUESTS_PER_MINUTE = 40  # immutable hard ceiling
NVIDIA_NORMAL_REQUESTS_PER_MINUTE = 30
NVIDIA_RATE_WINDOW_SECONDS = 60
NVIDIA_DAILY_WINDOW_SECONDS = 86400
NVIDIA_DAILY_REQUEST_BUDGET = 200
NVIDIA_MAX_REQUESTS_PER_PACKAGE = 4
NVIDIA_BUDGET_CATEGORY_LIMITS = {
    "forward_discovery": 100,
    "forward_critique": 30,
    "reverse_diagnostic": 20,
    "between_draws": 30,
    "retry_reserve": 20,
}
# The shared state branch currently stores schema-v2 ledgers using this exact
# former budget. Accept it only as a known predecessor, normalize it in memory,
# and preserve every reservation; the next authorized reservation persists v2
# with the new limits. No zero-fill or history rewrite is performed.
LEGACY_NVIDIA_DAILY_REQUEST_BUDGET = 72
LEGACY_NVIDIA_BUDGET_CATEGORY_LIMITS = {
    "forward_discovery": 40,
    "forward_critique": 12,
    "reverse_diagnostic": 8,
    "between_draws": 8,
    "retry_reserve": 4,
}
RATE_LIMIT_RESERVATION_SAFETY_SECONDS = 30
MAX_REASON_LENGTH = 320
MAX_DIAGNOSTIC_RUN_IDS = 10000
DIAGNOSTIC_REPOSITORY = "xxlyusufi61-bot/general-super-loto"
DIAGNOSTIC_WORKFLOW_ID = 403997867
DIAGNOSTIC_WORKFLOW_PATH = ".github/workflows/nvidia-diagnostic.yml"
DIAGNOSTIC_APPROVAL_PHRASE = "I_APPROVE_ONE_DIAGNOSTIC_REQUEST"
DIAGNOSTIC_REQUEST_SOURCE = "open-research-forward"
DIAGNOSTIC_AUTHORIZED_ACTOR_LOGIN = "xxlyusufi61-bot"
DIAGNOSTIC_AUTHORIZED_ACTOR_ID = 345811504
SINGLE_REQUEST_WORKFLOW_PATH = ".github/workflows/general-single-request-normal-token-test.yml"
SINGLE_REQUEST_WORKFLOW_ID = 410813288
SINGLE_REQUEST_APPROVAL_PHRASE = "I_APPROVE_ONE_NORMAL_RESEARCH_REQUEST_8192_TOKENS"
SINGLE_REQUEST_SOURCE = "open-research-single-forward"
SINGLE_REQUEST_AUTHORIZED_ACTOR_LOGIN = DIAGNOSTIC_AUTHORIZED_ACTOR_LOGIN
SINGLE_REQUEST_AUTHORIZED_ACTOR_ID = DIAGNOSTIC_AUTHORIZED_ACTOR_ID
GIT_COMMAND_TIMEOUT_SECONDS = 30
_RATE_LIMIT_WORKTREE = None


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def initial_lock_state():
    return {
        "schema_version": 1,
        "locked": False,
        "reason": "",
        "locked_at": None,
        "locked_by": None,
        "last_lock_reason": "",
        "last_unlocked_at": None,
        "updated_at": None,
    }


def validate_lock_state(value):
    if not isinstance(value, dict):
        raise ValueError("lock state must be a JSON object")
    if value.get("schema_version") != 1:
        raise ValueError("unsupported lock-state schema")
    if not isinstance(value.get("locked"), bool):
        raise ValueError("locked must be a boolean")
    if not isinstance(value.get("reason"), str):
        raise ValueError("reason must be a string")
    if not isinstance(value.get("last_lock_reason"), str):
        raise ValueError("last_lock_reason must be a string")
    for key in ("locked_at", "locked_by", "last_unlocked_at", "updated_at"):
        if value.get(key) is not None and not isinstance(value.get(key), str):
            raise ValueError(key + " must be a string or null")
    if value["locked"] and (not value["reason"].strip() or not value.get("locked_at")):
        raise ValueError("an active lock must include a reason and timestamp")
    return value


def make_locked_state(previous, reason, stamp=None, locked_by=None):
    previous = validate_lock_state(previous)
    stamp = stamp or utc_now()
    reason = " ".join(str(reason).split())[:MAX_REASON_LENGTH]
    if not reason:
        reason = "NVIDIA API result was uncertain; fail-closed pause."
    return {
        "schema_version": 1,
        "locked": True,
        "reason": reason,
        "locked_at": stamp,
        "locked_by": locked_by,
        "last_lock_reason": reason,
        "last_unlocked_at": previous.get("last_unlocked_at"),
        "updated_at": stamp,
    }


def make_unlocked_state(previous, stamp=None):
    previous = validate_lock_state(previous)
    stamp = stamp or utc_now()
    last_reason = previous.get("reason") or previous.get("last_lock_reason") or ""
    return {
        "schema_version": 1,
        "locked": False,
        "reason": "",
        "locked_at": None,
        "locked_by": None,
        "last_lock_reason": last_reason,
        "last_unlocked_at": stamp,
        "updated_at": stamp,
    }


def should_activate_after_failure(attempted, succeeded):
    return bool(attempted) and not bool(succeeded)



def budget_category_for_source(source, requested=None):
    """Resolve every provider request into one of the five shared daily buckets."""
    if requested is not None:
        if requested not in NVIDIA_BUDGET_CATEGORY_LIMITS:
            raise ValueError("unknown NVIDIA daily budget category")
        return requested
    if os.environ.get("GENERAL_NVIDIA_RESEARCH_RETRY_MODE", "").strip().lower() == "true":
        return "retry_reserve"
    explicit = os.environ.get("GENERAL_NVIDIA_BUDGET_CATEGORY", "").strip()
    if explicit:
        if explicit not in NVIDIA_BUDGET_CATEGORY_LIMITS:
            raise ValueError("GENERAL_NVIDIA_BUDGET_CATEGORY is invalid")
        return explicit
    source = str(source)
    if source == "open-research-forward":
        return "forward_discovery"
    if source == "open-research-reverse":
        return "reverse_diagnostic"
    if source == "open-research-forward-critique":
        return "forward_critique"
    if source == "open-research-between-draws":
        return "between_draws"
    agent = os.environ.get("AGENT_ID", os.environ.get("GENERAL_AGENT_ID", "")).strip().lower()
    if agent == "research":
        return "forward_discovery"
    if agent in {"crossover"}:
        return "between_draws"
    if agent in {"debug", "validation"}:
        return "forward_critique"
    # Diagnostics, coordinator support calls, and unclassified normal agents
    # consume the conservative critique bucket; no request escapes accounting.
    return "forward_critique"


def _parse_aware_timestamp(value, field_name):
    if not isinstance(value, str):
        raise ValueError(field_name + " must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(field_name + " must be a valid ISO-8601 timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(field_name + " must include a timezone")
    return parsed.astimezone(timezone.utc)


def initial_rate_limit_state():
    return {
        "schema_version": 2,
        "window_seconds": NVIDIA_RATE_WINDOW_SECONDS,
        "max_requests": NVIDIA_REQUESTS_PER_MINUTE,
        "normal_max_requests": NVIDIA_NORMAL_REQUESTS_PER_MINUTE,
        "daily_window_seconds": NVIDIA_DAILY_WINDOW_SECONDS,
        "daily_max_requests": NVIDIA_DAILY_REQUEST_BUDGET,
        "category_limits": dict(NVIDIA_BUDGET_CATEGORY_LIMITS),
        "package_max_requests": NVIDIA_MAX_REQUESTS_PER_PACKAGE,
        "requests": [],
        "daily_requests": [],
        "diagnostic_run_ids": [],
        "updated_at_utc": None,
    }


def validate_rate_limit_state(value):
    if not isinstance(value, dict):
        raise ValueError("NVIDIA rate-limit state must be a JSON object")
    # Schema v1 is readable only so its accounting can be reconstructed from
    # the append-only state-branch commit history before any new provider call.
    if value.get("schema_version") == 1:
        allowed = {
            "schema_version", "window_seconds", "max_requests", "requests",
            "diagnostic_run_ids", "updated_at_utc",
        }
        legacy_allowed = allowed - {"diagnostic_run_ids"}
        if set(value) not in (allowed, legacy_allowed):
            raise ValueError("legacy NVIDIA rate-limit schema is invalid")
        if value.get("window_seconds") != NVIDIA_RATE_WINDOW_SECONDS:
            raise ValueError("NVIDIA rate-limit window was changed or is invalid")
        if value.get("max_requests") != NVIDIA_REQUESTS_PER_MINUTE:
            raise ValueError("NVIDIA hard request cap was changed or is invalid")
        requests = value.get("requests")
        if not isinstance(requests, list) or len(requests) > NVIDIA_REQUESTS_PER_MINUTE:
            raise ValueError("NVIDIA rate-limit history is invalid")
        previous = None
        for entry in requests:
            if not isinstance(entry, dict) or set(entry) != {"reserved_at_utc", "source"}:
                raise ValueError("legacy NVIDIA request entry is invalid")
            stamp = _parse_aware_timestamp(entry.get("reserved_at_utc"), "NVIDIA request timestamp")
            source = entry.get("source")
            if not isinstance(source, str) or not source or len(source) > 80:
                raise ValueError("NVIDIA request source is invalid")
            if previous is not None and stamp < previous:
                raise ValueError("NVIDIA request history is not chronological")
            previous = stamp
        diagnostic_run_ids = value.get("diagnostic_run_ids", [])
        if not isinstance(diagnostic_run_ids, list) or len(diagnostic_run_ids) > MAX_DIAGNOSTIC_RUN_IDS:
            raise ValueError("diagnostic run-id history is invalid")
        seen_diagnostic_run_ids = set()
        for run_id in diagnostic_run_ids:
            if (
                not isinstance(run_id, str)
                or not run_id.isascii()
                or not run_id.isdigit()
                or len(run_id) > 32
                or str(int(run_id)) != run_id
                or int(run_id) < 1
                or run_id in seen_diagnostic_run_ids
            ):
                raise ValueError("diagnostic run-id history contains an invalid or duplicate ID")
            seen_diagnostic_run_ids.add(run_id)
        if value.get("updated_at_utc") is not None:
            _parse_aware_timestamp(value["updated_at_utc"], "NVIDIA rate-limit updated_at_utc")
        return value

    required = {
        "schema_version", "window_seconds", "max_requests", "normal_max_requests",
        "daily_window_seconds", "daily_max_requests", "category_limits",
        "package_max_requests", "requests", "daily_requests",
        "diagnostic_run_ids", "updated_at_utc",
    }
    if value.get("schema_version") != 2 or set(value) != required:
        raise ValueError("unsupported or malformed NVIDIA budget ledger schema")
    fixed = (
        ("window_seconds", NVIDIA_RATE_WINDOW_SECONDS),
        ("max_requests", NVIDIA_REQUESTS_PER_MINUTE),
        ("normal_max_requests", NVIDIA_NORMAL_REQUESTS_PER_MINUTE),
        ("daily_window_seconds", NVIDIA_DAILY_WINDOW_SECONDS),
        ("package_max_requests", NVIDIA_MAX_REQUESTS_PER_PACKAGE),
    )
    for key, expected in fixed:
        if type(value.get(key)) is not int or value[key] != expected:
            raise ValueError("NVIDIA budget constant was changed: " + key)

    stored_daily_budget = value.get("daily_max_requests")
    stored_category_limits = value.get("category_limits")
    if type(stored_daily_budget) is not int:
        raise ValueError("NVIDIA daily budget constant has an invalid type")
    is_current_budget = (
        stored_daily_budget == NVIDIA_DAILY_REQUEST_BUDGET
        and stored_category_limits == NVIDIA_BUDGET_CATEGORY_LIMITS
    )
    is_known_legacy_budget = (
        stored_daily_budget == LEGACY_NVIDIA_DAILY_REQUEST_BUDGET
        and stored_category_limits == LEGACY_NVIDIA_BUDGET_CATEGORY_LIMITS
    )
    if not (is_current_budget or is_known_legacy_budget):
        raise ValueError("NVIDIA daily category allocations or budget are unknown")

    # Upgrade only the in-memory configuration. Reservation records are
    # validated and retained unchanged; persistence occurs only with the next
    # separately authorized reservation.
    if is_known_legacy_budget:
        value = dict(value)
        value["daily_max_requests"] = NVIDIA_DAILY_REQUEST_BUDGET
        value["category_limits"] = dict(NVIDIA_BUDGET_CATEGORY_LIMITS)

    requests = value.get("requests")
    if not isinstance(requests, list) or len(requests) > NVIDIA_REQUESTS_PER_MINUTE:
        raise ValueError("NVIDIA rolling rate history is invalid")
    previous = None
    for entry in requests:
        if not isinstance(entry, dict) or set(entry) != {"reserved_at_utc", "source"}:
            raise ValueError("NVIDIA rolling request entry is invalid")
        stamp = _parse_aware_timestamp(entry.get("reserved_at_utc"), "NVIDIA request timestamp")
        source = entry.get("source")
        if not isinstance(source, str) or not source or len(source) > 80:
            raise ValueError("NVIDIA request source is invalid")
        if previous is not None and stamp < previous:
            raise ValueError("NVIDIA rolling request history is not chronological")
        previous = stamp

    daily = value.get("daily_requests")
    if not isinstance(daily, list) or len(daily) > 10000:
        raise ValueError("NVIDIA rolling 24-hour request history is invalid")
    previous = None
    reservation_ids = set()
    category_counts = {name: 0 for name in NVIDIA_BUDGET_CATEGORY_LIMITS}
    for entry in daily:
        expected_keys = {"reserved_at_utc", "source", "category", "package_id", "reservation_id"}
        if not isinstance(entry, dict) or set(entry) != expected_keys:
            raise ValueError("NVIDIA daily request entry is invalid")
        stamp = _parse_aware_timestamp(entry.get("reserved_at_utc"), "NVIDIA daily request timestamp")
        if previous is not None and stamp < previous:
            raise ValueError("NVIDIA daily request history is not chronological")
        previous = stamp
        if not isinstance(entry.get("source"), str) or not entry["source"] or len(entry["source"]) > 80:
            raise ValueError("NVIDIA daily request source is invalid")
        category = entry.get("category")
        if category not in NVIDIA_BUDGET_CATEGORY_LIMITS:
            raise ValueError("NVIDIA daily request category is invalid")
        package_id = entry.get("package_id")
        reservation_id = entry.get("reservation_id")
        if not isinstance(package_id, str) or not package_id or len(package_id) > 160:
            raise ValueError("NVIDIA request package identifier is invalid")
        if not isinstance(reservation_id, str) or not reservation_id or len(reservation_id) > 100:
            raise ValueError("NVIDIA request reservation identifier is invalid")
        if reservation_id in reservation_ids:
            raise ValueError("NVIDIA reservation identifier is duplicated")
        reservation_ids.add(reservation_id)
        category_counts[category] += 1
    # Historical migration may legitimately discover that the old system used
    # more than the new budget. Preserve every such reservation and let the
    # reservation function block new calls until the rolling window recovers.

    ids = value.get("diagnostic_run_ids")
    if not isinstance(ids, list) or len(ids) > MAX_DIAGNOSTIC_RUN_IDS:
        raise ValueError("diagnostic run-id history is invalid")
    seen = set()
    for run_id in ids:
        if (
            not isinstance(run_id, str) or not run_id.isascii() or not run_id.isdigit()
            or len(run_id) > 32 or str(int(run_id)) != run_id or int(run_id) < 1
            or run_id in seen
        ):
            raise ValueError("diagnostic run-id history contains an invalid or duplicate ID")
        seen.add(run_id)
    if value.get("updated_at_utc") is not None:
        _parse_aware_timestamp(value["updated_at_utc"], "NVIDIA rate-limit updated_at_utc")
    return value


def reserve_rate_limit_slot(
    previous,
    source,
    now=None,
    *,
    diagnostic_run_id=None,
    budget_category=None,
    package_id=None,
):
    previous = validate_rate_limit_state(previous)
    if previous.get("schema_version") != 2:
        raise ValueError("legacy NVIDIA budget history must be reconstructed before reservation")
    source = " ".join(str(source).split())[:80]
    if not source:
        raise ValueError("NVIDIA request source is required")
    category = budget_category_for_source(source, budget_category)
    diagnostic_run_ids = list(previous.get("diagnostic_run_ids", []))
    if diagnostic_run_id is not None:
        if (
            not isinstance(diagnostic_run_id, str) or not diagnostic_run_id.isascii()
            or not diagnostic_run_id.isdigit() or len(diagnostic_run_id) > 32
            or str(int(diagnostic_run_id)) != diagnostic_run_id or int(diagnostic_run_id) < 1
        ):
            raise ValueError("diagnostic run ID is invalid")
        if diagnostic_run_id in diagnostic_run_ids:
            raise ValueError("this diagnostic workflow run has already reserved its one request")
        if len(diagnostic_run_ids) >= MAX_DIAGNOSTIC_RUN_IDS:
            raise ValueError("diagnostic one-shot history is full; provider request blocked")

    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("NVIDIA rate-limit clock must be timezone-aware")
    now = now.astimezone(timezone.utc)
    effective_now = now + timedelta(seconds=RATE_LIMIT_RESERVATION_SAFETY_SECONDS)
    minute_cutoff = effective_now - timedelta(seconds=NVIDIA_RATE_WINDOW_SECONDS)
    day_cutoff = effective_now - timedelta(seconds=NVIDIA_DAILY_WINDOW_SECONDS)

    active = []
    for entry in previous["requests"]:
        stamp = _parse_aware_timestamp(entry["reserved_at_utc"], "NVIDIA request timestamp")
        if stamp >= minute_cutoff:
            active.append(entry)
    if len(active) >= NVIDIA_REQUESTS_PER_MINUTE:
        raise ValueError("the immutable 40-requests-per-minute hard limit is exhausted")
    if len(active) >= NVIDIA_NORMAL_REQUESTS_PER_MINUTE:
        oldest = _parse_aware_timestamp(active[0]["reserved_at_utc"], "NVIDIA request timestamp")
        release_at = oldest + timedelta(seconds=NVIDIA_RATE_WINDOW_SECONDS + 1)
        wait_seconds = max(1, math.ceil((release_at - effective_now).total_seconds()))
        return None, wait_seconds

    daily = [
        dict(entry) for entry in previous["daily_requests"]
        if _parse_aware_timestamp(entry["reserved_at_utc"], "NVIDIA daily request timestamp") >= day_cutoff
    ]
    if len(daily) >= NVIDIA_DAILY_REQUEST_BUDGET:
        release_at = _parse_aware_timestamp(daily[0]["reserved_at_utc"], "NVIDIA daily request timestamp") + timedelta(seconds=NVIDIA_DAILY_WINDOW_SECONDS + 1)
        raise ValueError(
            "shared rolling 24-hour NVIDIA budget (" + str(NVIDIA_DAILY_REQUEST_BUDGET) + ") is exhausted; next slot is after "
            + release_at.isoformat()
        )
    category_count = sum(1 for item in daily if item["category"] == category)
    category_limit = NVIDIA_BUDGET_CATEGORY_LIMITS[category]
    if category_count >= category_limit:
        matching = [item for item in daily if item["category"] == category]
        release_at = _parse_aware_timestamp(matching[0]["reserved_at_utc"], "NVIDIA category timestamp") + timedelta(seconds=NVIDIA_DAILY_WINDOW_SECONDS + 1)
        raise ValueError(
            "NVIDIA daily allocation for " + category + " (" + str(category_limit)
            + ") is exhausted; next slot is after " + release_at.isoformat()
        )

    if package_id is None:
        run_id = os.environ.get("GITHUB_RUN_ID", "").strip()
        run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1").strip()
        package_id = (run_id + ":" + run_attempt) if run_id else ("local:" + str(os.getpid()))
    if not isinstance(package_id, str) or not package_id or len(package_id) > 160:
        raise ValueError("NVIDIA job-package identity is invalid")
    if sum(1 for item in daily if item["package_id"] == package_id) >= NVIDIA_MAX_REQUESTS_PER_PACKAGE:
        raise ValueError("NVIDIA job package has reached its four-request maximum")

    reservation = {
        "reserved_at_utc": effective_now.isoformat(),
        "source": source,
        "category": category,
        "package_id": package_id,
        "reservation_id": uuid.uuid4().hex,
    }
    daily.append(reservation)
    active.append({"reserved_at_utc": effective_now.isoformat(), "source": source})
    next_state = {
        "schema_version": 2,
        "window_seconds": NVIDIA_RATE_WINDOW_SECONDS,
        "max_requests": NVIDIA_REQUESTS_PER_MINUTE,
        "normal_max_requests": NVIDIA_NORMAL_REQUESTS_PER_MINUTE,
        "daily_window_seconds": NVIDIA_DAILY_WINDOW_SECONDS,
        "daily_max_requests": NVIDIA_DAILY_REQUEST_BUDGET,
        "category_limits": dict(NVIDIA_BUDGET_CATEGORY_LIMITS),
        "package_max_requests": NVIDIA_MAX_REQUESTS_PER_PACKAGE,
        "requests": active,
        "daily_requests": daily,
        "diagnostic_run_ids": diagnostic_run_ids + ([diagnostic_run_id] if diagnostic_run_id is not None else []),
        "updated_at_utc": now.isoformat(),
    }
    return validate_rate_limit_state(next_state), 0


def migrate_legacy_rate_limit_state(previous, reservation_history, now=None):
    """Build a 24-hour ledger from append-only reservation commits; never zero-fill unknown history."""
    previous = validate_rate_limit_state(previous)
    if previous.get("schema_version") != 1:
        return validate_rate_limit_state(previous)
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("NVIDIA migration clock must be timezone-aware")
    cutoff = now.astimezone(timezone.utc) - timedelta(seconds=NVIDIA_DAILY_WINDOW_SECONDS)
    if not isinstance(reservation_history, list):
        raise ValueError("legacy NVIDIA reservation history is unavailable")
    daily = []
    seen = set()
    for item in reservation_history:
        if not isinstance(item, dict) or set(item) != {"commit_sha", "reserved_at_utc", "source"}:
            raise ValueError("legacy NVIDIA reservation history entry is malformed")
        stamp = _parse_aware_timestamp(item["reserved_at_utc"], "legacy reservation timestamp")
        if stamp < cutoff:
            continue
        commit_sha = item["commit_sha"]
        source = item["source"]
        if not isinstance(commit_sha, str) or len(commit_sha) != 40 or commit_sha in seen:
            raise ValueError("legacy NVIDIA reservation commit ID is invalid or duplicated")
        seen.add(commit_sha)
        category = budget_category_for_source(source)
        daily.append({
            "reserved_at_utc": stamp.isoformat(),
            "source": source,
            "category": category,
            "package_id": "legacy:" + commit_sha,
            "reservation_id": commit_sha,
        })
    daily.sort(key=lambda entry: _parse_aware_timestamp(entry["reserved_at_utc"], "legacy reservation timestamp"))
    current_minute = [
        item for item in previous["requests"]
        if _parse_aware_timestamp(item["reserved_at_utc"], "legacy current minute timestamp") >= cutoff
    ]
    for minute_item in current_minute:
        if not any(
            item["source"] == minute_item["source"]
            and item["reserved_at_utc"] == _parse_aware_timestamp(minute_item["reserved_at_utc"], "legacy current minute timestamp").isoformat()
            for item in daily
        ):
            raise ValueError("legacy NVIDIA reservation history is incomplete; provider requests blocked")
    updated = previous.get("updated_at_utc")
    if updated is not None and _parse_aware_timestamp(updated, "legacy ledger update timestamp") >= cutoff:
        updated_stamp = _parse_aware_timestamp(updated, "legacy ledger update timestamp")
        if not any(
            abs(( _parse_aware_timestamp(item["reserved_at_utc"], "legacy reservation timestamp") - updated_stamp).total_seconds()) <= 120
            for item in daily
        ):
            raise ValueError("legacy NVIDIA reservation history is incomplete; provider requests blocked")
    state = initial_rate_limit_state()
    state["requests"] = list(previous["requests"])
    state["daily_requests"] = daily
    state["diagnostic_run_ids"] = list(previous.get("diagnostic_run_ids", []))
    state["updated_at_utc"] = now.astimezone(timezone.utc).isoformat()
    return validate_rate_limit_state(state)

def _run_git(arguments, *, cwd=None):
    try:
        completed = subprocess.run(
            ["git"] + list(arguments),
            cwd=str(cwd or Path(__file__).resolve().parent),
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=GIT_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        # Do not echo Git stderr: it may include remote details. All state
        # operations are fail-closed and no NVIDIA call may follow a failure.
        raise RuntimeError(
            "GitHub shared safety-state operation failed; NVIDIA request blocked."
        ) from None
    return completed.stdout


def _fetch_remote_branch(branch, *, full_history=False):
    if not isinstance(branch, str) or not branch or "/" in branch or ".." in branch:
        raise RuntimeError("Invalid GENERAL safety-state branch; NVIDIA request blocked.")
    refspec = "+refs/heads/" + branch + ":refs/remotes/origin/" + branch
    if not full_history:
        _run_git(["fetch", "--no-tags", "--depth=1", "origin", refspec])
        return
    shallow = _run_git(["rev-parse", "--is-shallow-repository"]).strip() == "true"
    if shallow:
        _run_git(["fetch", "--unshallow", "--no-tags", "origin", refspec])
    else:
        _run_git(["fetch", "--no-tags", "origin", refspec])


def _cleanup_rate_limit_worktree():
    global _RATE_LIMIT_WORKTREE
    worktree = _RATE_LIMIT_WORKTREE
    _RATE_LIMIT_WORKTREE = None
    if worktree is None:
        return
    try:
        _run_git(["worktree", "remove", "--force", str(worktree)])
    except Exception:
        shutil.rmtree(worktree, ignore_errors=True)


def _required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError("required environment setting is missing: " + name)
    return value


def _api_request(method, url, payload=None):
    token = _required_env("GITHUB_TOKEN")
    headers = {
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "GENERAL-NVIDIA-Safety-Guard",
    }
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        raise RuntimeError("GitHub lock-state API returned HTTP " + str(error.code)) from None
    except urllib.error.URLError:
        raise RuntimeError("GitHub lock-state API connection failed") from None
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError("GitHub lock-state API returned invalid JSON") from None
    if not isinstance(result, dict):
        raise RuntimeError("GitHub lock-state API returned an invalid object")
    return result



def validate_diagnostic_workflow_run(run, env=None):
    """Validate an active, first-attempt run of the one approved diagnostic workflow."""
    env = os.environ if env is None else env
    if not isinstance(run, dict):
        raise RuntimeError("GitHub workflow-run metadata is not an object; diagnostic request blocked.")

    repository = env.get("GITHUB_REPOSITORY", "")
    run_id = env.get("GITHUB_RUN_ID", "")
    if repository != DIAGNOSTIC_REPOSITORY:
        raise RuntimeError("Diagnostic exception is restricted to the canonical repository.")
    if env.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise RuntimeError("Diagnostic exception requires workflow_dispatch.")
    if env.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Diagnostic exception requires the main branch.")
    if env.get("GITHUB_WORKFLOW_REF") != (
        DIAGNOSTIC_REPOSITORY + "/" + DIAGNOSTIC_WORKFLOW_PATH + "@refs/heads/main"
    ):
        raise RuntimeError("Diagnostic exception requires the exact diagnostic workflow reference.")
    if (
        not isinstance(run_id, str)
        or not run_id.isascii()
        or not run_id.isdigit()
        or len(run_id) > 32
        or str(int(run_id)) != run_id
        or int(run_id) < 1
    ):
        raise RuntimeError("GitHub workflow run ID is missing or invalid.")
    if env.get("GITHUB_RUN_ATTEMPT") != "1":
        raise RuntimeError("Diagnostic workflow reruns are not permitted.")
    if env.get("GENERAL_CONFIRM_LIVE_CALLS") != DIAGNOSTIC_APPROVAL_PHRASE:
        raise RuntimeError("The exact one-request diagnostic approval is required.")

    expected_sha = env.get("GITHUB_SHA", "")
    if not isinstance(expected_sha, str) or len(expected_sha) != 40:
        raise RuntimeError("GitHub workflow commit identity is missing or invalid.")
    if type(run.get("id")) is not int or run.get("id") != int(run_id):
        raise RuntimeError("GitHub workflow run identity does not match the current runner.")
    if type(run.get("workflow_id")) is not int or run.get("workflow_id") != DIAGNOSTIC_WORKFLOW_ID:
        raise RuntimeError("The run does not belong to the approved NVIDIA diagnostic workflow.")
    if run.get("path") != DIAGNOSTIC_WORKFLOW_PATH:
        raise RuntimeError("The workflow-run path is not the approved diagnostic workflow.")
    if run.get("event") != "workflow_dispatch":
        raise RuntimeError("GitHub did not record this run as workflow_dispatch.")
    actor = run.get("actor")
    if (
        not isinstance(actor, dict)
        or type(actor.get("id")) is not int
        or actor.get("id") != DIAGNOSTIC_AUTHORIZED_ACTOR_ID
        or actor.get("login") != DIAGNOSTIC_AUTHORIZED_ACTOR_LOGIN
    ):
        raise RuntimeError(
            "Only the explicitly authorized GitHub account may dispatch this diagnostic workflow."
        )
    if run.get("head_branch") != "main":
        raise RuntimeError("GitHub did not record main as the workflow branch.")
    if run.get("head_sha") != expected_sha:
        raise RuntimeError("GitHub workflow commit does not match the current runner commit.")
    if type(run.get("run_attempt")) is not int or run.get("run_attempt") != 1:
        raise RuntimeError("Only the first attempt of a diagnostic workflow run is permitted.")
    if run.get("status") != "in_progress" or run.get("conclusion") is not None:
        raise RuntimeError("The diagnostic workflow run is not currently active.")
    repository_metadata = run.get("repository")
    head_repository = run.get("head_repository")
    if not isinstance(repository_metadata, dict) or repository_metadata.get("full_name") != DIAGNOSTIC_REPOSITORY:
        raise RuntimeError("GitHub workflow metadata belongs to a different repository.")
    if not isinstance(head_repository, dict) or head_repository.get("full_name") != DIAGNOSTIC_REPOSITORY:
        raise RuntimeError("GitHub workflow head repository is not the canonical repository.")
    return run_id


def read_current_diagnostic_workflow_run():
    """Read and validate the real GitHub Actions run; runner environment alone is insufficient."""
    repository = _required_env("GITHUB_REPOSITORY")
    run_id = _required_env("GITHUB_RUN_ID")
    api_base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    url = (
        api_base
        + "/repos/"
        + urllib.parse.quote(repository, safe="/")
        + "/actions/runs/"
        + urllib.parse.quote(run_id, safe="")
    )
    run = _api_request("GET", url)
    return validate_diagnostic_workflow_run(run)


def validate_single_request_workflow_run(run, env=None):
    """Validate the only first-attempt manual workflow allowed one normal-mode request."""
    env = os.environ if env is None else env
    if not isinstance(run, dict):
        raise RuntimeError("GitHub workflow-run metadata is not an object; single request blocked.")

    repository = env.get("GITHUB_REPOSITORY", "")
    run_id = env.get("GITHUB_RUN_ID", "")
    if repository != DIAGNOSTIC_REPOSITORY:
        raise RuntimeError("Single-request validation is restricted to the canonical repository.")
    if env.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        raise RuntimeError("Single-request validation requires workflow_dispatch.")
    if env.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Single-request validation requires the main branch.")
    if env.get("GITHUB_WORKFLOW_REF") != (
        DIAGNOSTIC_REPOSITORY + "/" + SINGLE_REQUEST_WORKFLOW_PATH + "@refs/heads/main"
    ):
        raise RuntimeError("Single-request validation requires the exact workflow reference.")
    if (
        not isinstance(run_id, str)
        or not run_id.isascii()
        or not run_id.isdigit()
        or len(run_id) > 32
        or str(int(run_id)) != run_id
        or int(run_id) < 1
    ):
        raise RuntimeError("Single-request GitHub workflow run ID is missing or invalid.")
    if env.get("GITHUB_RUN_ATTEMPT") != "1":
        raise RuntimeError("Single-request workflow reruns are not permitted.")
    if env.get("GENERAL_CONFIRM_LIVE_CALLS") != SINGLE_REQUEST_APPROVAL_PHRASE:
        raise RuntimeError("The exact one-request normal-mode approval is required.")

    expected_sha = env.get("GITHUB_SHA", "")
    if (
        not isinstance(expected_sha, str)
        or len(expected_sha) != 40
        or any(character not in "0123456789abcdefABCDEF" for character in expected_sha)
    ):
        raise RuntimeError("Single-request workflow commit identity is missing or invalid.")
    if type(run.get("id")) is not int or run.get("id") != int(run_id):
        raise RuntimeError("GitHub single-request run identity does not match the current runner.")
    workflow_id = run.get("workflow_id")
    if type(workflow_id) is not int or workflow_id != SINGLE_REQUEST_WORKFLOW_ID:
        raise RuntimeError("GitHub run does not belong to the registered single-request workflow ID.")
    if run.get("path") != SINGLE_REQUEST_WORKFLOW_PATH:
        raise RuntimeError("The run does not belong to the approved single-request workflow.")
    if run.get("event") != "workflow_dispatch":
        raise RuntimeError("GitHub did not record the single-request run as workflow_dispatch.")
    actor = run.get("actor")
    if (
        not isinstance(actor, dict)
        or type(actor.get("id")) is not int
        or actor.get("id") != SINGLE_REQUEST_AUTHORIZED_ACTOR_ID
        or actor.get("login") != SINGLE_REQUEST_AUTHORIZED_ACTOR_LOGIN
    ):
        raise RuntimeError("Only the explicitly authorized GitHub account may dispatch this workflow.")
    if run.get("head_branch") != "main":
        raise RuntimeError("GitHub did not record main as the single-request workflow branch.")
    if run.get("head_sha") != expected_sha:
        raise RuntimeError("Single-request workflow commit does not match the current runner commit.")
    if type(run.get("run_attempt")) is not int or run.get("run_attempt") != 1:
        raise RuntimeError("Only the first attempt of a single-request workflow run is permitted.")
    if run.get("status") != "in_progress" or run.get("conclusion") is not None:
        raise RuntimeError("The single-request workflow run is not currently active.")
    repository_metadata = run.get("repository")
    head_repository = run.get("head_repository")
    if not isinstance(repository_metadata, dict) or repository_metadata.get("full_name") != DIAGNOSTIC_REPOSITORY:
        raise RuntimeError("GitHub single-request metadata belongs to a different repository.")
    if not isinstance(head_repository, dict) or head_repository.get("full_name") != DIAGNOSTIC_REPOSITORY:
        raise RuntimeError("GitHub single-request head repository is not the canonical repository.")
    return run_id


def read_current_single_request_workflow_run():
    """Read the live Actions run from GitHub; runner environment variables alone are insufficient."""
    repository = _required_env("GITHUB_REPOSITORY")
    run_id = _required_env("GITHUB_RUN_ID")
    api_base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    url = (
        api_base
        + "/repos/"
        + urllib.parse.quote(repository, safe="/")
        + "/actions/runs/"
        + urllib.parse.quote(run_id, safe="")
    )
    run = _api_request("GET", url)
    return validate_single_request_workflow_run(run)


def _lock_url():
    repo = _required_env("GITHUB_REPOSITORY")
    api_base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    branch = os.environ.get("GENERAL_DEFAULT_BRANCH", "main").strip() or "main"
    url = (
        api_base + "/repos/" + urllib.parse.quote(repo, safe="/")
        + "/contents/" + urllib.parse.quote(LOCK_PATH, safe="/")
        + "?ref=" + urllib.parse.quote(branch, safe="")
    )
    return url, branch


def read_remote_lock():
    branch = os.environ.get("GENERAL_DEFAULT_BRANCH", "main").strip() or "main"
    _fetch_remote_branch(branch)
    ref = "refs/remotes/origin/" + branch + ":" + LOCK_PATH
    try:
        raw = _run_git(["show", ref])
        sha = _run_git(["rev-parse", ref]).strip()
        state = validate_lock_state(json.loads(raw))
    except (ValueError, json.JSONDecodeError):
        raise RuntimeError("global NVIDIA lock file is malformed; API use blocked") from None
    except RuntimeError:
        raise RuntimeError("global NVIDIA lock file is missing or unreadable; API use blocked") from None
    if not sha:
        raise RuntimeError("global NVIDIA lock file SHA is missing; API use blocked")
    return state, sha


def write_remote_lock(state, sha, message):
    url, branch = _lock_url()
    raw = (json.dumps(validate_lock_state(state), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    payload = {
        "message": message,
        "content": base64.b64encode(raw).decode("ascii"),
        "sha": sha,
        "branch": branch,
    }
    response = _api_request("PUT", url.split("?ref=", 1)[0], payload)
    commit = response.get("commit")
    if not isinstance(commit, dict) or not isinstance(commit.get("sha"), str):
        raise RuntimeError("GitHub did not confirm the global lock commit")
    return commit["sha"]



def _ensure_rate_limit_worktree():
    global _RATE_LIMIT_WORKTREE
    if _RATE_LIMIT_WORKTREE is not None and Path(_RATE_LIMIT_WORKTREE).is_dir():
        return Path(_RATE_LIMIT_WORKTREE)

    _fetch_remote_branch(RATE_LIMIT_BRANCH)
    remote_ref = "refs/remotes/origin/" + RATE_LIMIT_BRANCH
    worktree = Path(tempfile.mkdtemp(prefix="general-nvidia-rate-limit-"))
    # git worktree add expects a path that does not already exist.
    worktree.rmdir()
    _run_git(["worktree", "add", "--detach", str(worktree), remote_ref])
    _run_git(["config", "user.name", "GENERAL NVIDIA Rate Guard"], cwd=worktree)
    _run_git(
        ["config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
        cwd=worktree,
    )
    _RATE_LIMIT_WORKTREE = worktree
    atexit.register(_cleanup_rate_limit_worktree)
    return worktree


def _reconstruct_legacy_reservations(worktree, now):
    cutoff = now.astimezone(timezone.utc) - timedelta(seconds=NVIDIA_DAILY_WINDOW_SECONDS)
    since = (cutoff - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    log = _run_git(
        ["log", "--since=" + since, "--format=%H%x09%s", "--", RATE_LIMIT_PATH],
        cwd=worktree,
    )
    history = []
    for line in log.splitlines():
        parts = line.split("\t", 1)
        if len(parts) != 2 or parts[1] != "chore: reserve shared NVIDIA request slot":
            continue
        commit_sha = parts[0]
        if len(commit_sha) != 40:
            raise RuntimeError("Legacy NVIDIA rate-limit history has an invalid commit; provider request blocked.")
        try:
            snapshot = json.loads(_run_git(["show", commit_sha + ":" + RATE_LIMIT_PATH], cwd=worktree))
            requests = snapshot.get("requests") if isinstance(snapshot, dict) else None
            latest = requests[-1] if isinstance(requests, list) and requests else None
        except (ValueError, IndexError):
            raise RuntimeError("Legacy NVIDIA reservation commit cannot be reconstructed; provider request blocked.") from None
        if not isinstance(latest, dict) or set(latest) != {"reserved_at_utc", "source"}:
            raise RuntimeError("Legacy NVIDIA reservation commit is malformed; provider request blocked.")
        stamp = _parse_aware_timestamp(latest.get("reserved_at_utc"), "legacy reservation timestamp")
        if stamp >= cutoff:
            history.append({
                "commit_sha": commit_sha,
                "reserved_at_utc": stamp.isoformat(),
                "source": latest["source"],
            })
    return history


def read_remote_rate_limit():
    worktree = _ensure_rate_limit_worktree()
    path = worktree / RATE_LIMIT_PATH
    try:
        state = validate_rate_limit_state(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        raise RuntimeError(
            "shared NVIDIA rate-limit ledger is missing or malformed; API use blocked"
        ) from None
    head = _run_git(["rev-parse", "HEAD"], cwd=worktree).strip()
    if not head:
        raise RuntimeError("shared NVIDIA rate-limit branch head is unreadable; API use blocked")
    if state.get("schema_version") == 1:
        try:
            # Only the one-time legacy migration needs full commit history.
            # Normal v2 reservations use a shallow fetch of the current head.
            _fetch_remote_branch(RATE_LIMIT_BRANCH, full_history=True)
            remote_ref = "refs/remotes/origin/" + RATE_LIMIT_BRANCH
            _run_git(["reset", "--hard", remote_ref], cwd=worktree)
            state = validate_rate_limit_state(json.loads(path.read_text(encoding="utf-8")))
            head = _run_git(["rev-parse", "HEAD"], cwd=worktree).strip()
            if state.get("schema_version") != 1 or not head:
                raise RuntimeError("legacy state changed unexpectedly during migration")
            history = _reconstruct_legacy_reservations(worktree, datetime.now(timezone.utc))
            state = migrate_legacy_rate_limit_state(state, history)
        except Exception as error:
            raise RuntimeError(
                "Legacy 24-hour NVIDIA usage could not be verified; provider request blocked. "
                + str(error)
            ) from None
    return state, head

def write_remote_rate_limit(state, sha):
    worktree = _ensure_rate_limit_worktree()
    current_head = _run_git(["rev-parse", "HEAD"], cwd=worktree).strip()
    if current_head != sha:
        raise RuntimeError(
            "shared NVIDIA rate-limit state changed unexpectedly; provider request blocked"
        )

    path = worktree / RATE_LIMIT_PATH
    raw = json.dumps(
        validate_rate_limit_state(state),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ) + "\n"
    try:
        path.write_text(raw, encoding="utf-8")
    except OSError:
        raise RuntimeError(
            "shared NVIDIA rate-limit ledger could not be written; provider request blocked"
        ) from None

    _run_git(["add", RATE_LIMIT_PATH], cwd=worktree)
    _run_git(
        ["commit", "-m", "chore: reserve shared NVIDIA request slot"],
        cwd=worktree,
    )
    new_head = _run_git(["rev-parse", "HEAD"], cwd=worktree).strip()
    # Normal fast-forward push only. If another writer moved the branch, this
    # fails closed instead of overwriting or losing another reservation.
    _run_git(
        ["push", "origin", "HEAD:refs/heads/" + RATE_LIMIT_BRANCH],
        cwd=worktree,
    )
    return new_head


def reserve_nvidia_request(source, *, diagnostic_exception=False, single_request_exception=False, budget_category=None):
    """Persist one shared request slot with narrowly verified one-request modes."""
    source = " ".join(str(source).split())[:80]
    if not source:
        raise RuntimeError("NVIDIA request source is required; no provider request was sent.")
    if type(diagnostic_exception) is not bool or type(single_request_exception) is not bool:
        raise RuntimeError("NVIDIA exception flags must be explicit booleans.")
    if diagnostic_exception and single_request_exception:
        raise RuntimeError("Diagnostic and normal single-request exceptions cannot be combined.")
    if diagnostic_exception and source != DIAGNOSTIC_REQUEST_SOURCE:
        raise RuntimeError("The diagnostic exception cannot authorize a non-diagnostic request source.")
    if single_request_exception and source != SINGLE_REQUEST_SOURCE:
        raise RuntimeError("The normal single-request exception cannot authorize another request source.")

    while True:
        # The GitHub API identity is rechecked at the reservation boundary. A
        # runner-provided environment flag or an earlier preflight is not enough.
        diagnostic_run_id = (
            read_current_diagnostic_workflow_run()
            if diagnostic_exception
            else None
        )
        if single_request_exception:
            read_current_single_request_workflow_run()
        # Every provider reservation reads the remote lock again. Normal GENERAL
        # and agent requests remain blocked whenever the shared lock is active.
        lock_state, _ = read_remote_lock()
        if diagnostic_exception:
            if not lock_state["locked"]:
                raise RuntimeError(
                    "The one-request diagnostic exception requires the global safety lock to remain active."
                )
        elif lock_state["locked"]:
            raise RuntimeError(
                "Global NVIDIA safety lock is active; no provider request was sent."
            )

        previous, sha = read_remote_rate_limit()
        package_run = os.environ.get("GITHUB_RUN_ID", "").strip()
        package_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1").strip()
        package_id = (package_run + ":" + package_attempt) if package_run else ("local:" + str(os.getpid()))
        try:
            next_state, wait_seconds = reserve_rate_limit_slot(
                previous,
                source,
                diagnostic_run_id=diagnostic_run_id,
                budget_category=budget_category,
                package_id=package_id,
            )
        except ValueError as error:
            raise RuntimeError("NVIDIA shared budget blocked this request: " + str(error)) from None
        if next_state is None:
            print(
                "GENERAL NVIDIA RATE LIMIT: normal 30-requests-per-minute operating ceiling reached; "
                "the immutable hard ceiling remains 40. Waiting " + str(wait_seconds) + " seconds."
            )
            time.sleep(wait_seconds)
            continue

        try:
            commit_sha = write_remote_rate_limit(next_state, sha)
        except Exception as error:
            raise RuntimeError(
                "Shared NVIDIA rate-limit reservation could not be committed; "
                "no provider request was sent. " + str(error)
            ) from None

        print(
            "GENERAL NVIDIA BUDGET: reservation committed; "
            + str(len(next_state["requests"])) + "/30 normal slots (40 hard) in 60 seconds; "
            + str(len(next_state["daily_requests"])) + "/" + str(NVIDIA_DAILY_REQUEST_BUDGET) + " shared slots in 24 hours; "
            + "category=" + budget_category_for_source(source, budget_category) + ". "
            + "State commit: " + commit_sha
        )
        return next_state["requests"][-1]


def send_telegram(message):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id or not chat_id.lstrip("-").isdigit():
        print("TELEGRAM_NOTIFICATION: unavailable (required secrets missing)")
        return False
    payload = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message[:3900],
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/sendMessage",
        data=payload,
        headers={"Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception:
        print("TELEGRAM_NOTIFICATION: failed; token and response details suppressed")
        return False
    if not isinstance(result, dict) or result.get("ok") is not True:
        print("TELEGRAM_NOTIFICATION: Telegram did not confirm delivery")
        return False
    print("TELEGRAM_NOTIFICATION: delivered")
    return True


def command_single_request_check():
    """Require the exact one-request workflow and an active lock before archive preflight."""
    try:
        run_id = read_current_single_request_workflow_run()
        state, _ = read_remote_lock()
        if not state["locked"]:
            raise RuntimeError(
                "The normal-mode token test requires the global NVIDIA lock to be active before its one-shot unlock."
            )
    except Exception as error:
        print("GENERAL SINGLE-REQUEST TOKEN TEST: BLOCKED")
        print("Reason:", str(error))
        return 1
    print("GENERAL SINGLE-REQUEST TOKEN TEST: AUTHORIZED")
    print("Workflow run:", run_id)
    print("Global NVIDIA lock: active; no provider request was sent.")
    return 0


def command_single_request_unlock():
    """Temporarily clear the lock only for the authorized one-request test workflow."""
    try:
        run_id = read_current_single_request_workflow_run()
        previous, sha = read_remote_lock()
        if not previous["locked"]:
            raise RuntimeError(
                "The global NVIDIA lock was already clear; this one-shot unlock refuses to change that state."
            )
        state = make_unlocked_state(previous)
        commit_sha = write_remote_lock(
            state, sha, "chore: temporarily unlock for one-request normal token validation"
        )
    except Exception as error:
        print("GENERAL SINGLE-REQUEST TOKEN TEST: TEMPORARY UNLOCK FAILED")
        print("Reason:", str(error))
        return 1
    message = (
        "🔓 GENERAL — TEMPORARY NVIDIA UNLOCK FOR ONE REQUEST\n"
        "The approved normal-mode 8192-token validation workflow has temporarily cleared the lock.\n"
        "This workflow must restore the fail-closed lock after its single forward request.\n"
        "Workflow run: " + run_id + "\n"
        "Unlock commit: " + commit_sha
    )
    print("GENERAL NVIDIA LOCK: TEMPORARILY CLEARED FOR ONE REQUEST")
    print("Workflow run:", run_id)
    print("Commit:", commit_sha)
    send_telegram(message)
    return 0


def command_diagnostic_check():
    """Preflight only the exact approved diagnostic while the global lock remains active."""
    try:
        run_id = read_current_diagnostic_workflow_run()
        state, _ = read_remote_lock()
        if not state["locked"]:
            raise RuntimeError(
                "The diagnostic lock exception is available only while the global NVIDIA lock is active."
            )
    except Exception as error:
        print("GENERAL NVIDIA DIAGNOSTIC EXCEPTION: BLOCKED")
        print("Reason:", str(error))
        print("No NVIDIA provider request was sent and the global lock was not changed.")
        return 2
    print("GENERAL NVIDIA GLOBAL SAFETY LOCK: ACTIVE")
    print("Exact diagnostic workflow verified; one request may be reserved for run:", run_id)
    print("Normal GENERAL and agent requests remain blocked; no request has been reserved yet.")
    return 0


def command_check():
    try:
        state, _ = read_remote_lock()
    except Exception as error:
        message = (
            "⛔ GENERAL SAFETY GATE ERROR\n"
            "NVIDIA API calls are blocked because the global lock state could not be verified.\n"
            "Reason: " + str(error) + "\n"
            "No NVIDIA request was sent. Check the GitHub lock file and permissions."
        )
        print(message)
        send_telegram(message)
        return 2
    if state["locked"]:
        print("GENERAL NVIDIA GLOBAL SAFETY LOCK: ACTIVE")
        print("Reason:", state["reason"])
        print("No NVIDIA API request was sent. Manual confirmed unlock is required.")
        return 2
    print("GENERAL NVIDIA GLOBAL SAFETY LOCK: CLEAR")
    print("Remote main-branch lock state verified. NVIDIA request may proceed.")
    return 0


def command_lock(reason):
    workflow_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    workflow_repo = os.environ.get("GITHUB_REPOSITORY", "")
    workflow_run = os.environ.get("GITHUB_RUN_ID", "")
    run_url = workflow_url + "/" + workflow_repo + "/actions/runs/" + workflow_run if workflow_repo and workflow_run else None
    actor = os.environ.get("GITHUB_ACTOR", "")
    locked_by = (actor + (" | " + run_url if run_url else ""))[:500] or run_url
    try:
        previous, sha = read_remote_lock()
        if previous["locked"]:
            print("GENERAL NVIDIA GLOBAL SAFETY LOCK: already active")
            print("Reason:", previous["reason"])
            return 0
        state = make_locked_state(previous, reason, locked_by=locked_by)
        commit_sha = write_remote_lock(
            state, sha, "chore: activate global NVIDIA safety lock"
        )
    except Exception as error:
        message = (
            "🚨 GENERAL CRITICAL SAFETY ALERT\n"
            "NVIDIA API failure detected, but the global lock could not be confirmed/persisted.\n"
            "Do not start NVIDIA workflows manually until the lock is checked.\n"
            "Reason: " + str(error)
        )
        print(message)
        send_telegram(message)
        return 1
    message = (
        "⛔ GENERAL — NVIDIA GLOBAL SAFETY LOCK ACTIVATED\n"
        "All GENERAL workflows that call NVIDIA must stop before any new NVIDIA request.\n"
        "Automatic retries and automatic unlocking are disabled.\n"
        "Reason: " + state["reason"] + "\n"
        "Lock commit: " + commit_sha + "\n"
        "The lock remains active until a separately confirmed manual unlock."
        + ("\nRun: " + run_url if run_url else "")
    )
    print("GENERAL NVIDIA GLOBAL SAFETY LOCK: PERSISTED")
    print("Reason:", state["reason"])
    send_telegram(message)
    return 0


def command_unlock():
    try:
        previous, sha = read_remote_lock()
        if not previous["locked"]:
            print("GENERAL NVIDIA GLOBAL SAFETY LOCK: already clear; no change made")
            return 0
        state = make_unlocked_state(previous)
        commit_sha = write_remote_lock(
            state, sha, "chore: manually clear global NVIDIA safety lock"
        )
    except Exception as error:
        print("GENERAL NVIDIA GLOBAL SAFETY LOCK: unlock failed; lock must remain treated as active")
        print("Reason:", str(error))
        send_telegram(
            "⚠️ GENERAL — manual NVIDIA unlock failed. The lock should remain treated as active.\n"
            + str(error)
        )
        return 1
    message = (
        "🔓 GENERAL — NVIDIA SAFETY LOCK MANUALLY CLEARED\n"
        "The confirmed manual unlock was recorded on the default branch.\n"
        "Previous lock reason: " + (state["last_lock_reason"] or "not recorded") + "\n"
        "Commit: " + commit_sha + "\n"
        "NVIDIA workflows may run again; any new uncertain API failure will reactivate the lock."
    )
    print("GENERAL NVIDIA GLOBAL SAFETY LOCK: CLEARED BY MANUAL CONFIRMATION")
    print("Commit:", commit_sha)
    send_telegram(message)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="Fail closed unless the default-branch lock is verified clear")
    subparsers.add_parser("diagnostic-check", help="Verify the exact one-request diagnostic while the global lock stays active")
    subparsers.add_parser("single-request-check", help="Verify the exact normal-mode one-request workflow while the lock stays active")
    subparsers.add_parser("single-request-unlock", help="Temporarily unlock only for the exact normal-mode one-request workflow")
    reserve_parser = subparsers.add_parser("reserve", help="Atomically reserve one shared NVIDIA request slot")
    reserve_parser.add_argument("--source", required=True)
    lock_parser = subparsers.add_parser("lock", help="Persist a global lock and notify Telegram")
    lock_parser.add_argument("--reason", required=True)
    subparsers.add_parser("unlock", help="Clear the lock; only the confirmed manual workflow should call this")
    args = parser.parse_args()

    if args.command == "check":
        return command_check()
    if args.command == "diagnostic-check":
        return command_diagnostic_check()
    if args.command == "single-request-check":
        return command_single_request_check()
    if args.command == "single-request-unlock":
        return command_single_request_unlock()
    if args.command == "reserve":
        reserve_nvidia_request(args.source)
        return 0
    if args.command == "lock":
        return command_lock(args.reason)
    if args.command == "unlock":
        return command_unlock()
    return 2


if __name__ == "__main__":
    sys.exit(main())
