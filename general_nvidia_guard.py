#!/usr/bin/env python3
"""Fail-closed global safety lock for every GENERAL NVIDIA API workflow."""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

LOCK_PATH = "general_nvidia_lock.json"
MAX_REASON_LENGTH = 320


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
    url, _ = _lock_url()
    response = _api_request("GET", url)
    sha = response.get("sha")
    encoded = response.get("content")
    if not isinstance(sha, str) or not isinstance(encoded, str):
        raise RuntimeError("global NVIDIA lock file is missing or unreadable")
    try:
        decoded = base64.b64decode("".join(encoded.split())).decode("utf-8")
        state = validate_lock_state(json.loads(decoded))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("global NVIDIA lock file is malformed; API use blocked") from None
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
    lock_parser = subparsers.add_parser("lock", help="Persist a global lock and notify Telegram")
    lock_parser.add_argument("--reason", required=True)
    subparsers.add_parser("unlock", help="Clear the lock; only the confirmed manual workflow should call this")
    args = parser.parse_args()

    if args.command == "check":
        return command_check()
    if args.command == "lock":
        return command_lock(args.reason)
    if args.command == "unlock":
        return command_unlock()
    return 2


if __name__ == "__main__":
    sys.exit(main())
