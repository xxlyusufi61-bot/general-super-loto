"""Offline GENERAL coordinator planner.

This module plans agent routing only. It does not fetch the archive, call an
LLM, run agents, or make predictions. Data tasks remain blocked from execution
until a separate runner performs Data Guard validation and resolves the target.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from general_core import is_data_task, target_date_from, task_mode_from


PLAN_VERSION = "0.1.0"

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
        raise ValueError("A target date conflicts with code/system-only mode.")

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan GENERAL agent routing without calling an AI model.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--task", help="Task text to route.")
    source.add_argument(
        "--task-env",
        action="store_true",
        help="Read task text from TASK_TEXT environment variable."
    )
    args = parser.parse_args()

    task = os.environ.get("TASK_TEXT", "") if args.task_env else args.task
    try:
        plan = build_plan(task)
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({
            "planner": "GENERAL Coordinator",
            "planner_version": PLAN_VERSION,
            "mode": "plan_only",
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
