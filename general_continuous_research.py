#!/usr/bin/env python3
"""Bounded continuation control for one continuous GENERAL research session.

This module never calls NVIDIA, never writes project state, and never changes
authorization or budget records. It only validates a five-hour session envelope,
reads the authoritative state and safety ledgers, and dispatches the next
workflow_dispatch run after the current iteration has been fully reported.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import general_autonomous_research as autonomous
import general_automation_state_writer as state_writer
import general_nvidia_guard as nvidia
import general_target_queue as target_queue

REPOSITORY = "xxlyusufi61-bot/general-super-loto"
WORKFLOW_FILE = ".github/workflows/general-autonomous-research.yml"
BOT_ACTOR = "github-actions[bot]"
SESSION_WINDOW = timedelta(hours=5)
MAX_CONTINUOUS_ITERATIONS = 150
API_ROOT = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
RUN_ID_RE = re.compile(r"^[1-9][0-9]{0,19}$")
ITERATION_RE = re.compile(r"^[1-9][0-9]{0,3}$")


class ContinuousResearchError(RuntimeError):
    """A malformed session or unsafe continuation state."""


def _now_utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ContinuousResearchError("The session clock must be timezone-aware.")
    return current.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ContinuousResearchError(field + " must be a canonical UTC timestamp.")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ContinuousResearchError(field + " is invalid.") from None
    if _format_utc(parsed) != value:
        raise ContinuousResearchError(field + " is not canonical.")
    return parsed


def _positive_run_id(value: Any, field: str = "run ID") -> str:
    if not isinstance(value, str) or not RUN_ID_RE.fullmatch(value):
        raise ContinuousResearchError(field + " must be a canonical positive decimal ID.")
    return value


def _positive_iteration(value: Any) -> int:
    rendered = str(value)
    if not ITERATION_RE.fullmatch(rendered) or str(int(rendered)) != rendered:
        raise ContinuousResearchError("The continuous iteration number is invalid.")
    parsed = int(rendered)
    if parsed > MAX_CONTINUOUS_ITERATIONS:
        raise ContinuousResearchError("The continuous iteration limit was exceeded.")
    return parsed


def build_session_context(
    *,
    event_name: str,
    action: str,
    actor: str,
    ref: str,
    run_id: str,
    session_id: str = "",
    started_at_utc: str = "",
    deadline_at_utc: str = "",
    iteration: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create the first session envelope or validate a bot-dispatched successor."""
    current = _now_utc(now)
    current_run_id = _positive_run_id(run_id)

    if ref != "refs/heads/main":
        raise ContinuousResearchError("Continuous research is restricted to the default branch.")

    if event_name == "schedule":
        if session_id or started_at_utc or deadline_at_utc or iteration:
            raise ContinuousResearchError("A scheduled session cannot inherit continuation inputs.")
        start = current.replace(microsecond=0)
        return {
            "session_id": current_run_id,
            "started_at_utc": _format_utc(start),
            "deadline_at_utc": _format_utc(start + SESSION_WINDOW),
            "iteration": 1,
        }

    if event_name != "workflow_dispatch" or action != "continue" or actor != BOT_ACTOR:
        raise ContinuousResearchError("Only the internal bot continuation may start a successor run.")

    sid = _positive_run_id(session_id, "session ID")
    if current_run_id == sid:
        raise ContinuousResearchError("A continuation must use a new workflow run ID.")
    start = _parse_utc(started_at_utc, "session start")
    deadline = _parse_utc(deadline_at_utc, "session deadline")
    current_iteration = _positive_iteration(iteration)
    if current_iteration < 2:
        raise ContinuousResearchError("A successor run must start at iteration 2 or later.")
    if deadline - start != SESSION_WINDOW:
        raise ContinuousResearchError("The session deadline does not match the fixed five-hour window.")
    if start > current + timedelta(minutes=1):
        raise ContinuousResearchError("The session start cannot be in the future.")
    if current >= deadline:
        raise ContinuousResearchError("The continuous session has expired.")
    if current - start >= SESSION_WINDOW:
        raise ContinuousResearchError("The continuous session exceeded its fixed duration.")

    return {
        "session_id": sid,
        "started_at_utc": _format_utc(start),
        "deadline_at_utc": _format_utc(deadline),
        "iteration": current_iteration,
    }


def validate_current_session_context(
    *,
    event_name: str,
    action: str,
    actor: str,
    ref: str,
    run_id: str,
    session_id: str,
    started_at_utc: str,
    deadline_at_utc: str,
    iteration: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Validate the envelope already created by session-context for this run."""
    current = _now_utc(now)
    current_run_id = _positive_run_id(run_id)
    sid = _positive_run_id(session_id, "session ID")
    start = _parse_utc(started_at_utc, "session start")
    deadline = _parse_utc(deadline_at_utc, "session deadline")
    current_iteration = _positive_iteration(iteration)

    if ref != "refs/heads/main" or deadline - start != SESSION_WINDOW:
        raise ContinuousResearchError("The continuous session envelope is invalid.")
    if start > current + timedelta(minutes=1) or current >= deadline:
        raise ContinuousResearchError("The continuous session is not currently active.")

    if event_name == "schedule":
        if sid != current_run_id or current_iteration != 1 or action:
            raise ContinuousResearchError("The scheduled session identity does not match this run.")
    elif (
        event_name != "workflow_dispatch"
        or action != "continue"
        or actor != BOT_ACTOR
        or sid == current_run_id
        or current_iteration < 2
    ):
        raise ContinuousResearchError("The successor session identity is not authorized.")

    return {
        "session_id": sid,
        "started_at_utc": _format_utc(start),
        "deadline_at_utc": _format_utc(deadline),
        "iteration": current_iteration,
    }


def _write_github_output(path: str | None, values: dict[str, Any]) -> None:
    if not path:
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            if value is None:
                continue
            rendered = "true" if value is True else "false" if value is False else str(value)
            if "\n" in rendered or "\r" in rendered:
                raise ContinuousResearchError("GitHub output values must be single-line.")
            stream.write(key + "=" + rendered + "\n")


def _timestamp_from_ledger(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ContinuousResearchError("The NVIDIA ledger contains an invalid timestamp.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContinuousResearchError("The NVIDIA ledger contains a timezone-naive timestamp.")
    return parsed.astimezone(timezone.utc)


def budget_has_next_turn_capacity(
    rate_state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """Read-only capacity check for one fresh forward/reverse research pair."""
    checked = nvidia.validate_rate_limit_state(copy.deepcopy(rate_state))
    if checked.get("schema_version") != 3:
        return False, "BUDGET_SCHEMA_NOT_READY"

    effective_now = _now_utc(now) + timedelta(seconds=nvidia.RATE_LIMIT_RESERVATION_SAFETY_SECONDS)
    minute_cutoff = effective_now - timedelta(seconds=nvidia.NVIDIA_RATE_WINDOW_SECONDS)
    day_cutoff = effective_now - timedelta(seconds=nvidia.NVIDIA_DAILY_WINDOW_SECONDS)

    active_minute = [
        item for item in checked["requests"]
        if _timestamp_from_ledger(item["reserved_at_utc"]) >= minute_cutoff
    ]
    if len(active_minute) >= nvidia.NVIDIA_REQUESTS_PER_MINUTE:
        return False, "HARD_RATE_LIMIT_EXHAUSTED"

    active_day = [
        item for item in checked["daily_requests"]
        if _timestamp_from_ledger(item["reserved_at_utc"]) >= day_cutoff
    ]
    if len(active_day) >= nvidia.NVIDIA_DAILY_REQUEST_BUDGET:
        return False, "DAILY_BUDGET_EXHAUSTED"

    category_counts = {
        category: sum(item["category"] == category for item in active_day)
        for category in nvidia.NVIDIA_BUDGET_CATEGORY_LIMITS
    }
    if category_counts["forward_discovery"] >= nvidia.NVIDIA_BUDGET_CATEGORY_LIMITS["forward_discovery"]:
        return False, "FORWARD_DISCOVERY_BUDGET_EXHAUSTED"
    if category_counts["reverse_diagnostic"] >= nvidia.NVIDIA_BUDGET_CATEGORY_LIMITS["reverse_diagnostic"]:
        return False, "REVERSE_DIAGNOSTIC_BUDGET_EXHAUSTED"
    return True, "BUDGET_AVAILABLE"


def _safe_status_code(value: Any, fallback: str) -> str:
    if not isinstance(value, str) or not value:
        return fallback
    rendered = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").upper()
    return rendered[:80] or fallback


def _read_only_next_cycle_probe(
    *,
    repository: str,
    token: str,
) -> tuple[bool, str]:
    """Hydrate authoritative state and run the existing scheduler preflight in a temp copy."""
    if repository != REPOSITORY:
        return False, "UNEXPECTED_REPOSITORY"
    if not token:
        return False, "READ_TOKEN_MISSING"

    with tempfile.TemporaryDirectory(prefix="general-continuous-preflight-") as directory:
        root = Path(directory)
        reader = state_writer.GitHubStateStoreWriter(repository, token)
        reader.hydrate(root)

        state_path = root / "general_autonomous_research_state.json"
        archive_path = root / "super_loto_archive.json"
        ledger_path = root / "general_evaluation_ledger.json"
        lock_path = root / "general_nvidia_lock.json"

        state = autonomous.read_state(state_path)
        lock = nvidia.read_remote_lock()
        lock_path.write_text(
            json.dumps(lock, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )

        authorization = autonomous.authorization_status(state)
        if not authorization["authorized"]:
            return False, "AUTONOMOUS_AUTHORIZATION_INACTIVE"
        if lock.get("locked") is not False:
            return False, "GLOBAL_NVIDIA_LOCK_ACTIVE"

        rate_state, _rate_head = nvidia.read_remote_rate_limit()
        capacity, capacity_reason = budget_has_next_turn_capacity(rate_state)
        if not capacity:
            return False, capacity_reason

        attempts = state["attempts"]
        if not attempts:
            return False, "NO_RESEARCH_ATTEMPT_RECORDED"
        last_attempt = attempts[-1]
        last_event = next(
            (
                item for item in reversed(state["events"])
                if item["attempt_id"] == last_attempt["attempt_id"]
            ),
            None,
        )
        if last_event is None:
            return False, "LAST_ATTEMPT_NOT_FINALIZED"
        if last_event["outcome"] == "NO_ELIGIBLE_CANDIDATE":
            return False, "NO_EXECUTABLE_HYPOTHESIS"
        if last_event["outcome"] not in {"SUCCEEDED", "FAILED"}:
            return False, "LAST_ATTEMPT_NOT_TERMINAL"

        # The existing scheduler is the authority for retries, manual-unlock
        # rules, the per-target failed-attempt ceiling, frozen-claim recovery,
        # sealed-window freshness, and immutable target/archive identity.
        active = target_queue.active_item(state["target_queue"])
        if active is not None:
            target_queue.materialize_front(state["target_queue"], archive_path)

        original_cwd = Path.cwd()
        try:
            os.chdir(root)
            plan = autonomous.prepare_cycle(
                copy.deepcopy(state),
                archive_path=archive_path,
                ledger_path=ledger_path,
                global_lock_path=lock_path,
            )
        finally:
            os.chdir(original_cwd)

        if plan.get("eligible") is not True:
            return False, _safe_status_code(plan.get("status"), "NEXT_CYCLE_NOT_ELIGIBLE")
        return True, "NEXT_CYCLE_ELIGIBLE"


def build_continuation_plan(
    *,
    context: dict[str, Any],
    current_run_id: str,
    report_delivered: bool,
    probe_result: tuple[bool, str],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Decide whether to dispatch exactly one next run, without changing state."""
    current = _now_utc(now)
    _positive_run_id(current_run_id)
    session_id = _positive_run_id(context.get("session_id"), "session ID")
    started = _parse_utc(context.get("started_at_utc"), "session start")
    deadline = _parse_utc(context.get("deadline_at_utc"), "session deadline")
    iteration = _positive_iteration(context.get("iteration"))

    enabled = False
    reason = "CONTINUATION_NOT_ELIGIBLE"
    next_iteration = None

    if deadline - started != SESSION_WINDOW:
        reason = "INVALID_SESSION_WINDOW"
    elif current >= deadline:
        reason = "SESSION_TIME_LIMIT_REACHED"
    elif iteration >= MAX_CONTINUOUS_ITERATIONS:
        reason = "ITERATION_SAFETY_LIMIT_REACHED"
    elif report_delivered is not True:
        reason = "TELEGRAM_REPORT_NOT_CONFIRMED"
    else:
        probe_ok, probe_reason = probe_result
        if probe_ok:
            enabled = True
            reason = "NEXT_RESEARCH_TURN_READY"
            next_iteration = iteration + 1
        else:
            reason = _safe_status_code(probe_reason, "NEXT_CYCLE_NOT_ELIGIBLE")

    return {
        "continue_enabled": enabled,
        "stop_reason": reason,
        "session_id": session_id,
        "started_at_utc": _format_utc(started),
        "deadline_at_utc": _format_utc(deadline),
        "iteration": iteration,
        "next_iteration": next_iteration,
        "session_values_rendered": False,
    }


def _dispatch_workflow(
    *,
    repository: str,
    token: str,
    session_id: str,
    started_at_utc: str,
    deadline_at_utc: str,
    next_iteration: int,
    now: datetime | None = None,
) -> None:
    """Make one non-retried workflow_dispatch request; uncertain delivery is reported, not replayed."""
    if repository != REPOSITORY:
        raise ContinuousResearchError("Continuation dispatch is restricted to the canonical repository.")
    if not token:
        raise ContinuousResearchError("A GitHub Actions token is required for continuation dispatch.")
    sid = _positive_run_id(session_id, "session ID")
    start = _parse_utc(started_at_utc, "session start")
    deadline = _parse_utc(deadline_at_utc, "session deadline")
    current = _now_utc(now)
    if deadline - start != SESSION_WINDOW or current >= deadline:
        raise ContinuousResearchError("The continuous session is expired or malformed.")
    if type(next_iteration) is not int or next_iteration < 2 or next_iteration > MAX_CONTINUOUS_ITERATIONS:
        raise ContinuousResearchError("The next iteration is outside the safety limit.")

    url = (
        API_ROOT + "/repos/" + urllib.parse.quote(repository, safe="/")
        + "/actions/workflows/" + urllib.parse.quote(WORKFLOW_FILE, safe="/")
        + "/dispatches"
    )
    payload = {
        "ref": "main",
        "inputs": {
            "action": "continue",
            "continuous_session_id": sid,
            "continuous_started_at_utc": _format_utc(start),
            "continuous_deadline_at_utc": _format_utc(deadline),
            "continuous_iteration": str(next_iteration),
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "GENERAL-Continuous-Research-Dispatcher",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            if getattr(response, "status", None) != 204:
                raise ContinuousResearchError("GitHub did not confirm workflow continuation dispatch.")
    except urllib.error.HTTPError as exc:
        raise ContinuousResearchError(
            "GitHub rejected workflow continuation dispatch with HTTP " + str(exc.code) + "."
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        # The POST may have been accepted before transport failed. Never retry
        # an ambiguous dispatch because that could create a duplicate child.
        raise ContinuousResearchError(
            "Workflow continuation dispatch delivery is uncertain; no automatic replay was attempted."
        ) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    context_parser = sub.add_parser("session-context")
    context_parser.add_argument("--event", required=True)
    context_parser.add_argument("--action", default="")
    context_parser.add_argument("--actor", required=True)
    context_parser.add_argument("--ref", required=True)
    context_parser.add_argument("--run-id", required=True)
    context_parser.add_argument("--session-id", default="")
    context_parser.add_argument("--started-at-utc", default="")
    context_parser.add_argument("--deadline-at-utc", default="")
    context_parser.add_argument("--iteration", default="")
    context_parser.add_argument("--github-output")

    plan_parser = sub.add_parser("plan-next")
    plan_parser.add_argument("--event", required=True)
    plan_parser.add_argument("--action", default="")
    plan_parser.add_argument("--actor", required=True)
    plan_parser.add_argument("--ref", required=True)
    plan_parser.add_argument("--run-id", required=True)
    plan_parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    plan_parser.add_argument("--session-id", required=True)
    plan_parser.add_argument("--started-at-utc", required=True)
    plan_parser.add_argument("--deadline-at-utc", required=True)
    plan_parser.add_argument("--iteration", required=True)
    plan_parser.add_argument("--report-delivered", choices=("true", "false"), required=True)
    plan_parser.add_argument("--github-output")

    dispatch_parser = sub.add_parser("dispatch")
    dispatch_parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    dispatch_parser.add_argument("--session-id", required=True)
    dispatch_parser.add_argument("--started-at-utc", required=True)
    dispatch_parser.add_argument("--deadline-at-utc", required=True)
    dispatch_parser.add_argument("--next-iteration", type=int, required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "session-context":
            context = build_session_context(
                event_name=args.event,
                action=args.action,
                actor=args.actor,
                ref=args.ref,
                run_id=args.run_id,
                session_id=args.session_id,
                started_at_utc=args.started_at_utc,
                deadline_at_utc=args.deadline_at_utc,
                iteration=args.iteration,
            )
            _write_github_output(args.github_output, context)
            print(json.dumps({
                "status": "CONTINUOUS_SESSION_CONTEXT_VALID",
                "iteration": context["iteration"],
                "session_values_rendered": False,
            }, sort_keys=True))
            return 0

        if args.command == "plan-next":
            context = validate_current_session_context(
                event_name=args.event,
                action=args.action,
                actor=args.actor,
                ref=args.ref,
                run_id=args.run_id,
                session_id=args.session_id,
                started_at_utc=args.started_at_utc,
                deadline_at_utc=args.deadline_at_utc,
                iteration=args.iteration,
            )
            current = _now_utc()
            if current >= _parse_utc(context["deadline_at_utc"], "session deadline"):
                probe = (False, "SESSION_TIME_LIMIT_REACHED")
            elif args.report_delivered != "true":
                probe = (False, "TELEGRAM_REPORT_NOT_CONFIRMED")
            else:
                token = os.environ.get("GITHUB_TOKEN", "").strip()
                try:
                    probe = _read_only_next_cycle_probe(repository=args.repository, token=token)
                except Exception as exc:
                    print(
                        "CONTINUATION PREFLIGHT BLOCKED: " + type(exc).__name__,
                        file=sys.stderr,
                    )
                    probe = (False, "READ_ONLY_PREFLIGHT_FAILED")
            plan = build_continuation_plan(
                context=context,
                current_run_id=args.run_id,
                report_delivered=(args.report_delivered == "true"),
                probe_result=probe,
            )
            _write_github_output(args.github_output, plan)
            print(json.dumps({
                "continue_enabled": plan["continue_enabled"],
                "stop_reason": plan["stop_reason"],
                "iteration": plan["iteration"],
                "next_iteration": plan["next_iteration"],
                "session_values_rendered": False,
            }, sort_keys=True))
            return 0

        if args.command == "dispatch":
            token = os.environ.get("GITHUB_TOKEN", "").strip()
            _dispatch_workflow(
                repository=args.repository,
                token=token,
                session_id=args.session_id,
                started_at_utc=args.started_at_utc,
                deadline_at_utc=args.deadline_at_utc,
                next_iteration=args.next_iteration,
            )
            print(json.dumps({
                "status": "NEXT_RESEARCH_WORKFLOW_DISPATCHED",
                "iteration": args.next_iteration,
                "session_values_rendered": False,
            }, sort_keys=True))
            return 0

        return 2
    except Exception as exc:
        print("CONTINUOUS RESEARCH BLOCKED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
