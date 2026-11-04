#!/usr/bin/env python3
"""Read-only integrity audit for GENERAL's migrated automation state.

This supersedes the bootstrap-only migration preflight in routine CI after the
four runtime objects have been migrated. It does not modify GitHub or call NVIDIA.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

import general_automation_state as contract
import general_automation_state_writer as state_writer
import general_nvidia_guard as nvidia_guard
from general_automation_state_reader import (
    GitHubStateStoreReader,
    StateStoreReadError,
)

EXPECTED_OBJECTS = {
    "archive",
    "evaluation_ledger",
    "autonomous_research_state",
    "research_memory",
}


class RuntimeStateAuditError(ValueError):
    """The migrated state-store does not satisfy its runtime contract."""


def build_runtime_audit_report(
    snapshot: dict[str, Any],
    shadow: dict[str, Any],
    lock_state: dict[str, Any],
    *,
    domain_validator: Callable[[dict[str, dict[str, Any]]], None] | None = None,
) -> dict[str, Any]:
    """Validate the active migration without rendering source records."""
    if not isinstance(snapshot, dict) or snapshot.get("branch") != contract.STATE_BRANCH:
        raise RuntimeStateAuditError("verified state snapshot has an unexpected branch")
    manifest = contract.validate_manifest(snapshot.get("manifest"))
    if manifest.get("status") != "ACTIVE" or manifest.get("generation", 0) < 1:
        raise RuntimeStateAuditError("state store is not active at a positive generation")
    if set(manifest["entries"]) != EXPECTED_OBJECTS:
        raise RuntimeStateAuditError("state manifest does not contain exactly the four migrated runtime objects")
    documents = snapshot.get("documents")
    if not isinstance(documents, dict) or set(documents) != EXPECTED_OBJECTS:
        raise RuntimeStateAuditError("verified state snapshot does not contain exactly the four runtime objects")
    if set(snapshot.get("verified_objects", [])) != EXPECTED_OBJECTS:
        raise RuntimeStateAuditError("reader did not verify every required state object")

    validator = domain_validator or state_writer.validate_domain_documents
    validator(documents)

    if not isinstance(shadow, dict) or shadow.get("mode") != "SHADOW_COMPARE":
        raise RuntimeStateAuditError("read-only shadow comparison is missing")
    if shadow.get("authoritative_source") != "main" or shadow.get("read_only") is not True:
        raise RuntimeStateAuditError("shadow comparison changed or misidentified the authoritative source")
    if shadow.get("state_store_commit_sha") != snapshot.get("commit_sha"):
        raise RuntimeStateAuditError("shadow comparison is not bound to the verified state snapshot")
    comparisons = shadow.get("comparisons")
    if not isinstance(comparisons, dict):
        raise RuntimeStateAuditError("shadow comparison object map is invalid")
    for key in EXPECTED_OBJECTS:
        if comparisons.get(key) != "MATCH":
            raise RuntimeStateAuditError("migrated state object does not match the main source before cutover")
    if comparisons.get("nvidia_lock") != "NOT_REGISTERED":
        raise RuntimeStateAuditError("NVIDIA global lock must remain outside the first state-store migration")
    if shadow.get("diverged") is not False or shadow.get("diverged_count") != 0:
        raise RuntimeStateAuditError("main/state shadow comparison detected divergence")
    if shadow.get("matched_count") != len(EXPECTED_OBJECTS):
        raise RuntimeStateAuditError("main/state shadow comparison matched-object count is incorrect")
    if shadow.get("not_registered_count") != 1:
        raise RuntimeStateAuditError("main/state shadow comparison lock exclusion count is incorrect")

    try:
        lock = nvidia_guard.validate_lock_state(lock_state)
    except Exception:
        raise RuntimeStateAuditError("main-branch NVIDIA lock failed validation") from None
    if lock.get("locked") is not True:
        raise RuntimeStateAuditError("global NVIDIA lock is not active")
    authorization = documents["autonomous_research_state"].get("authorization")
    if not isinstance(authorization, dict) or authorization.get("enabled") is not False:
        raise RuntimeStateAuditError("autonomous research must remain paused during state migration")

    return {
        "mode": "ACTIVE_STATE_RUNTIME_AUDIT",
        "state_store_branch": contract.STATE_BRANCH,
        "state_store_commit_sha": snapshot["commit_sha"],
        "state_store_status": manifest["status"],
        "state_store_generation": manifest["generation"],
        "verified_objects": sorted(EXPECTED_OBJECTS),
        "main_state_parity": "PASS",
        "matched_count": shadow["matched_count"],
        "nvidia_lock": "LOCKED_ON_MAIN_NOT_REGISTERED",
        "autonomous_research": "PAUSED",
        "target_values_rendered": False,
        "draw_rows_rendered": False,
        "github_writes": 0,
        "nvidia_requests": 0,
        "read_only": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--nvidia-lock-file", type=Path, default=Path("general_nvidia_lock.json"))
    args = parser.parse_args(argv)
    if not args.repository:
        parser.error("--repository or GITHUB_REPOSITORY is required")
    try:
        reader = GitHubStateStoreReader(args.repository, args.token)
        snapshot = reader.read_snapshot()
        shadow = reader.read_shadow_comparison()
        try:
            lock_state = json.loads(args.nvidia_lock_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise RuntimeStateAuditError("main-branch NVIDIA lock file is missing or invalid") from None
        report = build_runtime_audit_report(snapshot, shadow, lock_state)
    except (RuntimeStateAuditError, StateStoreReadError, contract.AutomationStateError) as exc:
        print("STATE RUNTIME AUDIT FAILED:", type(exc).__name__, str(exc), file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
