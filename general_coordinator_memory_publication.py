"""Offline safety helpers for the GENERAL coordinator memory-publication workflow.

This module deliberately emits only a small, allowlisted summary of coordinator
output. It never writes raw agent reports to the summary or workflow logs.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ALLOWED_AGENTS = {"validation", "debug", "research", "crossover"}
ALLOWED_TASK_TYPES = {"code", "system", "data"}
ALLOWED_RUN_STATUSES = {"success", "failed"}
SAFE_ERROR_TYPE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
MAX_AGENTS = 3


def _count(value: Any) -> int | None:
    if type(value) is int and 0 <= value <= MAX_AGENTS:
        return value
    return None


def _unparsed_summary() -> dict[str, Any]:
    return {
        "status": "unparsed",
        "task_type": None,
        "successful_agents": 0,
        "agent_invocations_started": 0,
        "agent_results": [],
        "report_text_suppressed": True,
    }


def summarize_payload(payload: Any) -> dict[str, Any]:
    """Return a strictly allowlisted summary of a coordinator result object."""
    summary = _unparsed_summary()
    if not isinstance(payload, dict) or payload.get("planner") != "GENERAL Coordinator":
        return summary

    raw_status = payload.get("status", payload.get("plan_status"))
    status = raw_status if isinstance(raw_status, str) and raw_status in {"success", "failed", "rejected"} else "unparsed"
    raw_task_type = payload.get("task_type")
    task_type = raw_task_type if isinstance(raw_task_type, str) and raw_task_type in ALLOWED_TASK_TYPES else None
    raw_results = payload.get("agent_results", [])
    if not isinstance(raw_results, list) or len(raw_results) > MAX_AGENTS:
        return summary

    safe_results: list[dict[str, Any]] = []
    malformed_result = False
    for item in raw_results:
        if not isinstance(item, dict):
            malformed_result = True
            continue
        agent = item.get("agent")
        result_status = item.get("status")
        if not isinstance(agent, str) or agent not in ALLOWED_AGENTS or not isinstance(result_status, str) or result_status not in {"success", "failed"}:
            malformed_result = True
            continue
        safe_item: dict[str, Any] = {"agent": agent, "status": result_status}
        report_received = item.get("report_received")
        if type(report_received) is bool:
            safe_item["report_received"] = report_received
        elif result_status == "success":
            malformed_result = True
        error_type = item.get("error_type")
        if isinstance(error_type, str) and SAFE_ERROR_TYPE.fullmatch(error_type):
            safe_item["error_type"] = error_type
        safe_results.append(safe_item)

    successful = _count(payload.get("successful_agents"))
    started = _count(payload.get("agent_invocations_started"))
    if successful is None or started is None or malformed_result:
        status = "unparsed"
    if successful is not None and successful != sum(x["status"] == "success" for x in safe_results):
        status = "unparsed"
    if started is not None and started != len(safe_results):
        status = "unparsed"
    if status == "success" and any(x["status"] != "success" for x in safe_results):
        status = "unparsed"

    return {
        "status": status,
        "task_type": task_type,
        "successful_agents": successful if successful is not None else 0,
        "agent_invocations_started": started if started is not None else 0,
        "agent_results": safe_results,
        "report_text_suppressed": True,
    }


def summarize_log(log_text: str) -> dict[str, Any]:
    """Find and parse the last coordinator JSON payload, suppressing report text."""
    if not isinstance(log_text, str):
        return _unparsed_summary()
    marker = '{\n  "planner": "GENERAL Coordinator"'
    start = log_text.rfind(marker)
    if start < 0:
        return _unparsed_summary()
    try:
        payload, _ = json.JSONDecoder().raw_decode(log_text[start:])
    except (json.JSONDecodeError, TypeError, ValueError):
        return _unparsed_summary()
    return summarize_payload(payload)


def publication_decision(
    coordinator_summary: Any,
    memory_summary: Any,
    publish_requested: str,
    baseline_bytes: bytes | None,
    current_main_bytes: bytes | None,
) -> dict[str, Any]:
    """Fail closed unless the candidate is safe, requested, and based on current main."""
    def blocked(reason: str) -> dict[str, Any]:
        return {"allowed": False, "reason": reason, "human_review_only": True}

    if publish_requested != "YES":
        return blocked("publication_not_requested")
    if not isinstance(coordinator_summary, dict):
        return blocked("coordinator_summary_invalid")
    status = coordinator_summary.get("status")
    if not isinstance(status, str) or status not in ALLOWED_RUN_STATUSES:
        return blocked("coordinator_status_not_publishable")
    if not isinstance(coordinator_summary.get("task_type"), str) or coordinator_summary.get("task_type") not in ALLOWED_TASK_TYPES:
        return blocked("task_type_invalid")
    results = coordinator_summary.get("agent_results")
    if not isinstance(results, list) or not results or len(results) > MAX_AGENTS:
        return blocked("agent_results_invalid")
    if _count(coordinator_summary.get("agent_invocations_started")) != len(results):
        return blocked("agent_invocation_count_mismatch")
    if _count(coordinator_summary.get("successful_agents")) != sum(
        isinstance(item, dict) and item.get("status") == "success" for item in results
    ):
        return blocked("successful_agent_count_mismatch")

    safe_reports = 0
    for item in results:
        if not isinstance(item, dict):
            return blocked("agent_result_invalid")
        if not isinstance(item.get("agent"), str) or item.get("agent") not in ALLOWED_AGENTS or not isinstance(item.get("status"), str) or item.get("status") not in {"success", "failed"}:
            return blocked("agent_result_invalid")
        received = item.get("report_received")
        if type(received) is not bool:
            return blocked("report_received_flag_invalid")
        if item["status"] == "success" and received:
            safe_reports += 1
    if safe_reports == 0:
        return blocked("no_successful_report_to_publish")
    if status == "success" and any(item.get("status") != "success" for item in results):
        return blocked("coordinator_result_inconsistent")

    if not isinstance(memory_summary, dict) or memory_summary.get("valid") is not True:
        return blocked("memory_guard_failed")
    if memory_summary.get("has_new_entries") is not True:
        return blocked("no_new_memory_entries")
    new_count = _count(memory_summary.get("new_entries_count"))
    if new_count is None or new_count < 1:
        return blocked("new_entry_count_invalid")

    if baseline_bytes is None or current_main_bytes is None:
        return blocked("main_baseline_unavailable")
    if baseline_bytes != current_main_bytes:
        return blocked("main_memory_changed_during_run")

    return {
        "allowed": True,
        "reason": "validated_for_manual_review",
        "human_review_only": True,
        "new_entries_count": new_count,
        "coordinator_status": status,
    }


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline safety helpers for coordinator memory publication.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary_parser = subparsers.add_parser("summarize-log")
    summary_parser.add_argument("--input", required=True)
    summary_parser.add_argument("--output", required=True)

    decision_parser = subparsers.add_parser("publication-check")
    decision_parser.add_argument("--coordinator-summary", required=True)
    decision_parser.add_argument("--memory-summary", required=True)
    decision_parser.add_argument("--baseline", required=True)
    decision_parser.add_argument("--current-main", required=True)
    decision_parser.add_argument("--publish-request", required=True)

    args = parser.parse_args()
    if args.command == "summarize-log":
        result = summarize_log(Path(args.input).read_text(encoding="utf-8", errors="replace"))
        Path(args.output).write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0

    try:
        decision = publication_decision(
            _read_json(args.coordinator_summary),
            _read_json(args.memory_summary),
            args.publish_request,
            Path(args.baseline).read_bytes(),
            Path(args.current_main).read_bytes(),
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        decision = {"allowed": False, "reason": "publication_inputs_unavailable", "human_review_only": True}
    print(json.dumps(decision, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
