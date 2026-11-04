#!/usr/bin/env python3
"""Read-only preflight for a future GENERAL automation-state migration.

This module computes a deterministic metadata-only plan from one pinned main
commit and verifies that the state branch is still an empty bootstrap store.
It never writes GitHub state, never moves data, and never calls NVIDIA.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import general_automation_state as contract
import general_autonomous_research as autonomous
import general_data_guard as data_guard
import general_nvidia_guard as nvidia_guard
import general_research_memory as research_memory
import general_sealed_evaluation as sealed_evaluation
from general_automation_state_reader import (
    GitHubStateStoreReader,
    StateStoreReadError,
)

PLAN_VERSION = "1.0"
MAX_OBJECT_BYTES = 8 * 1024 * 1024
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
COPY_ORDER = (
    "archive",
    "evaluation_ledger",
    "autonomous_research_state",
    "research_memory",
)
DEFERRED_OBJECTS = {"nvidia_lock"}
EXPECTED_YEARS = tuple(str(year) for year in range(2020, 2027))
EXCLUDED_INPUTS = {
    "legacy_memory": {
        "path": "general_memory.json",
        "reason": "Legacy/manual memory is not GENERAL-origin state.",
    },
    "method_registry": {
        "path": "general_method_registry.json",
        "reason": "Method registry is code/governance metadata, not runtime state.",
    },
    "nvidia_rate_limit": {
        "path": "general_nvidia_rate_limit.json",
        "branch": "general-nvidia-rate-limit-state",
        "reason": "The independent shared 40-requests-per-minute ledger stays on its own branch.",
    },
    "sealed_snapshots": {
        "path": "general_evaluation_snapshots/",
        "reason": "Immutable sealed evaluation snapshots are not part of this state-store bootstrap.",
    },
}


class SyncPreflightError(RuntimeError):
    """Raised when any source, isolation, or bootstrap precondition fails."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _validate_utc_text(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise SyncPreflightError(label + " is missing.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SyncPreflightError(label + " is not a valid ISO-8601 timestamp.") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SyncPreflightError(label + " must include a timezone.")
    return parsed


def _read_source_object(root: Path, object_key: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if object_key not in contract.STATE_OBJECTS:
        raise SyncPreflightError("A non-allowlisted state object was requested.")
    path = root / contract.STATE_OBJECTS[object_key]
    try:
        raw = path.read_bytes()
    except OSError:
        raise SyncPreflightError("A required main-branch state object is missing or unreadable.") from None
    if len(raw) > MAX_OBJECT_BYTES:
        raise SyncPreflightError("A source object exceeds the existing state-reader size limit.")
    try:
        content = raw.decode("utf-8")
    except UnicodeError:
        raise SyncPreflightError("A source object is not valid UTF-8.") from None
    try:
        value = contract._strict_json_loads(content)
        contract.validate_state_document(object_key, content)
    except contract.AutomationStateError as exc:
        raise SyncPreflightError("A source object failed strict JSON validation: " + str(exc)) from None
    if not isinstance(value, dict):
        raise SyncPreflightError("A source object root must be a JSON object.")
    metadata = {
        "path": contract.STATE_OBJECTS[object_key],
        "git_blob_sha": contract.git_blob_sha(content),
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
    }
    return content, value, metadata


def _validate_archive(root: Path, archive: dict[str, Any]) -> dict[str, Any]:
    if "draws" not in archive or "records" in archive:
        raise SyncPreflightError("Canonical archive must use one unambiguous draws list.")
    rows = archive.get("draws")
    if not isinstance(rows, list):
        raise SyncPreflightError("Canonical archive draws must be a list.")

    # Reuse the existing sealed-evaluation loader and Data Guard. This validates
    # active scope, sorted number sets, chronology, and unique draw dates.
    try:
        loaded = sealed_evaluation.load_archive(root / contract.STATE_OBJECTS["archive"])
        summary = data_guard.validate_archive(rows)
    except Exception as exc:
        raise SyncPreflightError(
            "Canonical archive or Data Guard validation failed: " + type(exc).__name__
        ) from None
    if loaded.get("record_count") != summary.get("record_count"):
        raise SyncPreflightError("Archive loader and Data Guard record counts disagree.")

    # The canonical fetcher writes a fetched_at timestamp plus a 2025
    # independent cross-check and a per-year 2020-2026 reconciliation map.
    # Validate the actual stored evidence; never synthesize missing audit fields.
    fetched_at = _validate_utc_text(archive.get("fetched_at"), "Archive fetched_at")
    year_counts = Counter(row["date"][:4] for row in rows)

    cross = archive.get("cross_check_validation")
    expected_2025_count = year_counts.get("2025", 0)
    if (
        not isinstance(cross, dict)
        or type(cross.get("year")) is not int
        or cross.get("year") != 2025
        or cross.get("result") != "PASS"
    ):
        raise SyncPreflightError("The independent 2025 cross-source validation is missing or not passing.")
    for field in (
        "primary_record_count",
        "independent_record_count",
        "matching_dates_and_number_sets",
    ):
        value = cross.get(field)
        if type(value) is not int or value != expected_2025_count:
            raise SyncPreflightError("The recorded 2025 cross-source counts do not match the canonical archive.")
    cross_pages = cross.get("pages_checked")
    if (
        not isinstance(cross_pages, list)
        or not cross_pages
        or any(not isinstance(page, str) or not page.strip() for page in cross_pages)
    ):
        raise SyncPreflightError("The recorded 2025 independent-source page coverage is incomplete.")
    if _validate_utc_text(cross.get("checked_at"), "2025 cross-source audit timestamp") > fetched_at:
        raise SyncPreflightError("The 2025 cross-source audit timestamp is later than the archive fetch.")

    multi = archive.get("multi_year_cross_source_validation")
    if not isinstance(multi, dict) or set(multi) != set(EXPECTED_YEARS):
        raise SyncPreflightError("The recorded multi-year cross-source coverage is incomplete.")
    for year in EXPECTED_YEARS:
        item = multi.get(year)
        if (
            not isinstance(item, dict)
            or item.get("result") != "PASS"
            or item.get("draw_numbers_in_report") is not False
            or not isinstance(item.get("source"), str)
            or not item.get("source").strip()
        ):
            raise SyncPreflightError("A year in the recorded multi-year cross-source audit is not passing.")
        pages = item.get("pages_checked")
        if (
            not isinstance(pages, list)
            or not pages
            or any(not isinstance(page, str) or not page.strip() for page in pages)
        ):
            raise SyncPreflightError("A year in the multi-year cross-source audit has incomplete page coverage.")
        if _validate_utc_text(item.get("checked_at"), "Multi-year audit timestamp") > fetched_at:
            raise SyncPreflightError("A multi-year audit timestamp is later than the archive fetch.")

        count_fields = (
            "archive_compared_count_before_merge",
            "independent_source_count",
            "records_added_from_independent_source",
            "matching_dates_and_number_sets_after_merge",
        )
        counts = {}
        for field in count_fields:
            value = item.get(field)
            if type(value) is not int or value < 0:
                raise SyncPreflightError("A multi-year cross-source count is missing or invalid.")
            counts[field] = value
        if (
            counts["archive_compared_count_before_merge"]
            + counts["records_added_from_independent_source"]
            != counts["matching_dates_and_number_sets_after_merge"]
            or counts["independent_source_count"]
            != counts["matching_dates_and_number_sets_after_merge"]
            or counts["matching_dates_and_number_sets_after_merge"]
            != year_counts.get(year, 0)
        ):
            raise SyncPreflightError("A multi-year cross-source count does not reconcile with the canonical archive.")

    # The latest archived draw is treated as the sealed target only for this
    # internal isolation check. Neither its date nor its values enter the plan.
    target_date = summary.get("last_date")
    try:
        isolated = data_guard.isolate_target(rows, target_date)
    except Exception as exc:
        raise SyncPreflightError("Latest-target isolation failed: " + type(exc).__name__) from None
    expected_count = summary.get("record_count")
    if (
        isolated.get("isolation_pass") is not True
        or isolated.get("target_present_in_archive") is not True
        or isolated.get("target_numbers_exposed") is not False
        or isolated.get("archive_record_count") != expected_count
        or isolated.get("excluded_record_count") != 1
        or isolated.get("training_record_count") != expected_count - 1
        or not isinstance(isolated.get("training_last_date"), str)
        or isolated.get("training_last_date") >= target_date
        or isolated.get("excluded_first_date") != target_date
    ):
        raise SyncPreflightError("Latest-target isolation did not produce exactly one sealed exclusion.")

    # Aggregate-only diagnostics: no target date or draw values enter the plan.
    return {
        "status": "PASS",
        "record_count": expected_count,
        "training_record_count": expected_count - 1,
        "sealed_exclusion_count": 1,
        "strict_chronology": True,
        "active_scope_valid": True,
        "cross_source_2025": "PASS",
        "multi_year_cross_source": "PASS",
        "target_values_rendered": False,
        "target_date_rendered": False,
    }


def _validate_object_schemas(root: Path, documents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    try:
        ledger = sealed_evaluation.load_ledger(root / contract.STATE_OBJECTS["evaluation_ledger"])
        scheduler = autonomous.validate_state(documents["autonomous_research_state"])
        memory = research_memory.load_memory(root / contract.STATE_OBJECTS["research_memory"])
        lock = nvidia_guard.validate_lock_state(documents["nvidia_lock"])
    except Exception as exc:
        raise SyncPreflightError("A persistent state object failed its domain validator: " + type(exc).__name__) from None

    if scheduler["authorization"]["enabled"] is not False:
        raise SyncPreflightError("Autonomous research must be paused before state migration can be prepared.")
    if lock.get("locked") is not True:
        raise SyncPreflightError("The global NVIDIA lock must remain active during state migration preparation.")

    return {
        "evaluation_ledger": {"status": "PASS", "entry_count": len(ledger["entries"])},
        "autonomous_research_state": {
            "status": "PASS",
            "authorization_enabled": False,
            "attempt_count": len(scheduler["attempts"]),
            "event_count": len(scheduler["events"]),
        },
        "research_memory": {"status": "PASS", "entry_count": len(memory["entries"])},
        "nvidia_lock": {"status": "PASS_LOCKED", "locked": True},
    }


def build_sync_preflight_plan(
    source_root: str | Path,
    expected_main_commit: str,
    shadow_comparison: dict[str, Any],
) -> dict[str, Any]:
    """Validate a pinned local main snapshot against the empty remote state store."""
    if not isinstance(expected_main_commit, str) or not COMMIT_SHA_RE.fullmatch(expected_main_commit):
        raise SyncPreflightError("Expected main commit must be a lowercase 40-character SHA.")
    if not isinstance(shadow_comparison, dict) or shadow_comparison.get("mode") != "SHADOW_COMPARE":
        raise SyncPreflightError("A verified read-only shadow comparison is required.")
    if shadow_comparison.get("main_commit_sha") != expected_main_commit:
        actual = shadow_comparison.get("main_commit_sha")
        raise SyncPreflightError(
            "Pinned main SHA mismatch (expected " + expected_main_commit
            + ", shadow comparison returned " + str(actual) + ")."
        )
    if (
        shadow_comparison.get("authoritative_source") != "main"
        or shadow_comparison.get("read_only") is not True
        or shadow_comparison.get("diverged") is not False
        or shadow_comparison.get("state_store_status") != "BOOTSTRAP_ONLY"
        or shadow_comparison.get("state_store_generation") != 0
    ):
        raise SyncPreflightError("Remote state store is not in the expected read-only bootstrap state.")
    comparisons = shadow_comparison.get("comparisons")
    if (
        not isinstance(comparisons, dict)
        or set(comparisons) != set(contract.STATE_OBJECTS)
        or any(value != "NOT_REGISTERED" for value in comparisons.values())
    ):
        raise SyncPreflightError("The state store is not empty across the full allowlist.")
    if (
        shadow_comparison.get("matched_count") != 0
        or shadow_comparison.get("diverged_count") != 0
        or shadow_comparison.get("not_registered_count") != len(contract.STATE_OBJECTS)
    ):
        raise SyncPreflightError("Shadow-comparison counts do not match the empty bootstrap contract.")
    state_commit = shadow_comparison.get("state_store_commit_sha")
    if not isinstance(state_commit, str) or not COMMIT_SHA_RE.fullmatch(state_commit):
        raise SyncPreflightError("Verified state-store commit SHA is missing or invalid.")

    root = Path(source_root).resolve()
    documents: dict[str, dict[str, Any]] = {}
    metadata: dict[str, dict[str, Any]] = {}
    for object_key in contract.STATE_OBJECTS:
        _, value, item_metadata = _read_source_object(root, object_key)
        documents[object_key] = value
        metadata[object_key] = item_metadata

    archive_checks = _validate_archive(root, documents["archive"])
    domain_checks = _validate_object_schemas(root, documents)

    object_plan = []
    for object_key in contract.STATE_OBJECTS:
        item = {
            "key": object_key,
            **metadata[object_key],
        }
        if object_key in DEFERRED_OBJECTS:
            item["disposition"] = "DEFERRED_MAIN_AUTHORITATIVE"
            item["reason"] = "Keep the global lock on main until every lock reader and writer is switched together."
        else:
            item["disposition"] = "ELIGIBLE_FOR_FUTURE_ATOMIC_TRANSFER"
        object_plan.append(item)

    copy_fingerprint = hashlib.sha256(
        _canonical_json([
            {"key": key, **metadata[key]}
            for key in COPY_ORDER
        ])
    ).hexdigest()

    return {
        "plan_version": PLAN_VERSION,
        "mode": "READ_ONLY_STATE_SYNC_PREFLIGHT",
        "source": {
            "branch": "main",
            "commit_sha": expected_main_commit,
            "authoritative": True,
        },
        "target_store": {
            "branch": contract.STATE_BRANCH,
            "commit_sha": state_commit,
            "status": "BOOTSTRAP_ONLY",
            "generation": 0,
            "next_generation_if_separately_approved": 1,
        },
        "objects": object_plan,
        "future_atomic_transfer_order": list(COPY_ORDER),
        "deferred_objects": ["nvidia_lock"],
        "excluded_inputs": EXCLUDED_INPUTS,
        "integrity_checks": {
            "archive": archive_checks,
            "evaluation_ledger": domain_checks["evaluation_ledger"],
            "autonomous_research_state": domain_checks["autonomous_research_state"],
            "research_memory": domain_checks["research_memory"],
            "nvidia_lock": domain_checks["nvidia_lock"],
        },
        "transaction_design": {
            "idempotency_fingerprint": copy_fingerprint,
            "preserve_source_bytes_exactly": True,
            "manifest_generation_check_required": True,
            "single_state_branch_commit_required": True,
            "recheck_main_and_state_heads_before_commit": True,
            "partial_object_publication_allowed": False,
            "rollback": "Compare-and-swap the state ref back to the recorded bootstrap commit only if it still points to the migration commit; never modify main.",
        },
        "safety": {
            "read_only": True,
            "data_transferred": False,
            "github_write_operations": 0,
            "nvidia_requests": 0,
            "global_lock_changed": False,
            "rate_limit_branch_touched": False,
            "target_date_rendered": False,
            "target_values_rendered": False,
            "main_remains_authoritative": True,
        },
    }


def _load_shadow_comparison(path: str | Path) -> dict[str, Any]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        value = contract._strict_json_loads(raw)
    except (OSError, UnicodeError, contract.AutomationStateError) as exc:
        raise SyncPreflightError("The preceding read-only shadow comparison could not be loaded: " + type(exc).__name__) from None
    if not isinstance(value, dict) or value.get("mode") != "SHADOW_COMPARE":
        raise SyncPreflightError("The preceding step did not produce a valid shadow-comparison document.")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="GitHub owner/repository; defaults to GITHUB_REPOSITORY.",
    )
    parser.add_argument(
        "--expected-main-commit",
        default=os.environ.get("GITHUB_SHA"),
        help="The exact checked-out main commit; defaults to GITHUB_SHA.",
    )
    parser.add_argument(
        "--source-root",
        default=".",
        help="Directory containing the checked-out main state objects.",
    )
    parser.add_argument(
        "--shadow-comparison-file",
        help="Reuse the verified JSON document from the immediately preceding read-only shadow step.",
    )
    args = parser.parse_args(argv)
    if not args.repository:
        parser.error("--repository or GITHUB_REPOSITORY is required")
    if not args.expected_main_commit:
        parser.error("--expected-main-commit or GITHUB_SHA is required")

    try:
        if args.shadow_comparison_file:
            comparison = _load_shadow_comparison(args.shadow_comparison_file)
        else:
            reader = GitHubStateStoreReader(
                args.repository,
                token=os.environ.get("GITHUB_TOKEN"),
            )
            comparison = reader.read_shadow_comparison()
        plan = build_sync_preflight_plan(
            args.source_root,
            args.expected_main_commit,
            comparison,
        )
    except (SyncPreflightError, StateStoreReadError, contract.AutomationStateError) as exc:
        print("STATE SYNC PREFLIGHT BLOCKED:", type(exc).__name__, str(exc), file=sys.stderr)
        return 1

    print(json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
