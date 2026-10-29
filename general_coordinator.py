"""GENERAL coordinator: plan-only by default, with explicitly gated execution.

Data tasks require a successful archive/target preflight before any agent call.
This module does not promise or guarantee lottery predictions.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import general_core as core
from general_core import is_data_task, target_date_from, task_mode_from


PLAN_VERSION = "0.2.0"

VALIDATION_MARKERS = (
    "validate", "validation", "verify", "integrity", "archive", "target",
    "isolation", "leakage", "guard", "check", "test", "doğrula", "dogrula",
    "denetle", "arşiv", "arsiv", "hedef", "izolasyon", "sızıntı", "sizinti",
    "duplicate", "duplicate-date", "kronoloji", "chronology",
)
RESEARCH_MARKERS = (
    "formula", "method", "pattern", "research", "discovery", "hypothesis",
    "frequency", "transition", "statistics", "statistical", "a->b->c", "a->t",
    "mod5", "analiz", "araştır", "arastir", "keşfet", "kesfet", "örüntü",
    "frekans", "yöntem", "yontem", "formül", "formul", "istatistik",
)
CROSSOVER_MARKERS = (
    "crossover", "compare", "comparison", "combine", "combination", "ensemble",
    "cross-validation", "karşılaştır", "karsilastir", "birleştir", "birlestir",
    "birlikte değerlendir", "birlikte degerlendir", "çoklu yöntem", "coklu yontem",
)
CODE_MARKERS = (
    "bug", "error", "traceback", "exception", "refactor", "workflow", "python",
    "yaml", "syntax", "implementation", "implement", "kod", "hata", "iş akışı",
    "is akisi", "düzelt", "duzelt",
)


def _contains_any(task: str, markers: tuple[str, ...]) -> bool:
    normalized = task.casefold()
    return any(marker in normalized for marker in markers)


def build_plan(raw_task: str) -> dict[str, Any]:
    """Return a deterministic, offline routing plan for a GENERAL task."""
    if not isinstance(raw_task, str):
        raise ValueError("Task must be text.")

    target, task = target_date_from(raw_task)
    mode, task = task_mode_from(task)

    if not task:
        raise ValueError("Task is empty.")

    if target and mode in {"code", "system"}:
        raise RuntimeError("A target date conflicts with code/system-only mode.")

    if mode == "data" or (mode is None and is_data_task(task)):
        task_type = "data"
    elif mode in {"code", "system"}:
        task_type = mode
    elif _contains_any(task, CODE_MARKERS):
        task_type = "code"
    else:
        task_type = "system"

    if task_type == "data":
        agents = ["validation"]
        research_intent = _contains_any(task, RESEARCH_MARKERS)
        validation_intent = _contains_any(task, VALIDATION_MARKERS)
        crossover_intent = _contains_any(task, CROSSOVER_MARKERS)

        if research_intent or not validation_intent:
            agents.append("research")
        if crossover_intent:
            agents.append("crossover")
        agents = agents[:3]
    elif task_type == "code":
        agents = ["debug", "validation"]
    else:
        agents = ["validation", "debug"]

    data_requires_preflight = task_type == "data"
    if target:
        target_resolution = "explicit_target_requires_archive_guard_validation"
    elif data_requires_preflight:
        target_resolution = "resolve_latest_valid_archive_target_before_execution"
    else:
        target_resolution = "not_applicable"

    return {
        "planner": "GENERAL Coordinator",
        "planner_version": PLAN_VERSION,
        "mode": "plan_only",
        "task_type": task_type,
        "agent_order": agents,
        "agent_count": len(agents),
        "estimated_model_requests_if_executed": len(agents),
        "target_date": target,
        "target_resolution": target_resolution,
        "archive_loaded_by_planner": False,
        "archive_guard_run_by_planner": False,
        "model_requests_made": 0,
        "agents_executed": False,
        "requires_guarded_archive_preflight": data_requires_preflight,
        "execution_ready": not data_requires_preflight,
        "binding_rules_changed": False,
        "notes": [
            "This is a routing plan only; no agent has been executed.",
            "Data tasks must pass archive validation and target isolation before any model request.",
            "Target-draw numbers are not loaded or used by this planner.",
            "The estimated request count is conditional on a future sequential execution; it is not a quota guarantee."
        ],
    }


EXECUTION_CONFIRMATION = "I_APPROVE_UP_TO_3_NVIDIA_REQUESTS"
REQUIRED_PREFLIGHT_FLAGS = (
    "row_level_audit_pass",
    "archive_dates_chronological",
    "archive_dates_unique",
    "target_occurs_once",
    "training_strictly_before_target",
    "training_chronological",
    "excluded_dates_match_archive",
    "training_dates_match_archive",
    "partition_counts_match",
)


def _validate_data_preflight(data: Any, target: str, dynamic_target: bool) -> None:
    """Reject data execution unless all independently checked guard fields pass."""
    if not isinstance(data, dict):
        raise RuntimeError("Coordinator preflight did not return audit metadata.")
    if data.get("guard") != "PASS":
        raise RuntimeError("Coordinator preflight did not pass Data Guard.")
    if data.get("target_date") != target:
        raise RuntimeError("Coordinator preflight target does not match the frozen target.")
    if data.get("target_present") is not True:
        raise RuntimeError("Coordinator preflight target is missing from the archive.")
    if not isinstance(data.get("training_count"), int) or data["training_count"] < 1:
        raise RuntimeError("Coordinator preflight has no valid pre-target training history.")

    for flag in REQUIRED_PREFLIGHT_FLAGS:
        if data.get(flag) is not True:
            raise RuntimeError("Coordinator preflight audit failed: " + flag)

    if dynamic_target and data.get("target_is_latest_archive_date") is not True:
        raise RuntimeError("Coordinator's dynamic target is not the latest archive date.")


def execute_plan(
    raw_task: str,
    confirmation: str,
    *,
    agent_runner=None,
    core_module=core,
) -> dict[str, Any]:
    """Run a plan sequentially only after explicit live-request confirmation.

    Tests inject a fake agent_runner; production defaults to GENERAL Core.
    No agent is invoked until explicit confirmation and, for data tasks, a
    guarded archive/target preflight have both passed.
    """
    if confirmation != EXECUTION_CONFIRMATION:
        raise RuntimeError(
            "Live execution blocked. Exact confirmation phrase is required: "
            + EXECUTION_CONFIRMATION
        )
    plan = build_plan(raw_task)
    if plan["agent_count"] > 3 or not plan["agent_order"]:
        raise RuntimeError("Coordinator plan is empty or exceeds the three-agent limit.")
    if os.environ.get("GENERAL_NVIDIA_GUARD_PASSED") != "1":
        raise RuntimeError("Global NVIDIA safety guard has not been confirmed; no agent was invoked.")
    if not os.environ.get("NVIDIA_API_KEY", "").strip():
        raise RuntimeError("NVIDIA_API_KEY is missing; no agent was invoked.")

    explicit_target, normalized_task = target_date_from(raw_task)
    _, normalized_task = task_mode_from(normalized_task)
    if not normalized_task:
        raise RuntimeError("Task is empty.")

    target = explicit_target
    if plan["task_type"] == "data":
        dynamic_target = target is None
        if dynamic_target:
            target = core_module.latest_archive_target_date()
        if not isinstance(target, str) or not target:
            raise RuntimeError("Coordinator could not resolve a target date.")
        guarded_data = core_module.prepare_data(target)
        _validate_data_preflight(guarded_data, target, dynamic_target)
        agent_task = "mode=data target_date=" + target + " " + normalized_task
    else:
        agent_task = "mode=" + plan["task_type"] + " " + normalized_task

    runner = agent_runner or core_module.run_agent
    results = []
    prior_reports = []
    for agent in plan["agent_order"]:
        try:
            answer = runner(
                agent,
                agent_task,
                coordinator_context=prior_reports[-2:],
            )
        except Exception as exc:
            results.append({
                "agent": agent,
                "status": "failed",
                "error_type": type(exc).__name__,
            })
            return {
                "planner": "GENERAL Coordinator",
                "planner_version": PLAN_VERSION,
                "mode": "sequential_execution",
                "status": "failed",
                "task_type": plan["task_type"],
                "target_date": target,
                "agent_results": results,
                "successful_agents": sum(item["status"] == "success" for item in results),
                "agent_invocations_started": len(results),
                "model_requests_expected_if_all_agents_complete": len(plan["agent_order"]),
                "next_agent_started_after_failure": False,
                "target_numbers_in_coordinator_report": False,
                "binding_rules_changed": False,
            }

        results.append({
            "agent": agent,
            "status": "success",
            "report_received": isinstance(answer, str) and bool(answer.strip()),
        })
        if isinstance(answer, str) and answer.strip():
            prior_reports.append({
                "agent": agent,
                "report": answer[:4000],
            })

    return {
        "planner": "GENERAL Coordinator",
        "planner_version": PLAN_VERSION,
        "mode": "sequential_execution",
        "status": "success",
        "task_type": plan["task_type"],
        "target_date": target,
        "agent_results": results,
        "successful_agents": len(results),
        "agent_invocations_started": len(results),
        "model_requests_expected_if_all_agents_complete": len(plan["agent_order"]),
        "next_agent_started_after_failure": False,
        "target_numbers_in_coordinator_report": False,
        "binding_rules_changed": False,
        "note": "Agent reports passed to later agents are labeled unverified; no agent can override binding rules or Data Guard."
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan GENERAL agent routing without calling an AI model.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--task", help="Task text to route.")
    source.add_argument(
        "--task-env",
        action="store_true",
        help="Read task text from TASK_TEXT environment variable."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute the selected agents; requires the exact live-request confirmation phrase."
    )
    parser.add_argument(
        "--confirm-live-calls",
        default="",
        help="Must equal I_APPROVE_UP_TO_3_NVIDIA_REQUESTS to allow agent execution."
    )
    args = parser.parse_args()

    task = os.environ.get("TASK_TEXT", "") if args.task_env else args.task
    try:
        if args.execute:
            result = execute_plan(
                task,
                args.confirm_live_calls,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("status") == "success" else 1

        plan = build_plan(task)
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({
            "planner": "GENERAL Coordinator",
            "planner_version": PLAN_VERSION,
            "mode": "sequential_execution" if args.execute else "plan_only",
            "plan_status": "rejected",
            "reason": str(exc),
            "model_requests_made": 0,
            "agents_executed": False,
        }, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
