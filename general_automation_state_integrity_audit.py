#!/usr/bin/env python3
"""Read-only integrity audit for GENERAL's authoritative automation state store.

This audit verifies the active branch, manifest, object inventory, pinned
snapshot identity, and existing domain validators. It deliberately does not
decide whether autonomous research is authorized or whether the NVIDIA safety
lock is open. Those runtime policies are enforced by each calling workflow at
the appropriate boundary.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Callable

import general_automation_state as contract
import general_automation_state_writer as state_writer
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


class StateStoreIntegrityError(ValueError):
    """The authoritative state store does not satisfy its integrity contract."""


def build_state_integrity_report(
    snapshot: dict[str, Any],
    *,
    domain_validator: Callable[[dict[str, dict[str, Any]]], None] | None = None,
) -> dict[str, Any]:
    """Validate one verified state snapshot without applying runtime policy."""
    if not isinstance(snapshot, dict):
        raise StateStoreIntegrityError("verified state snapshot must be an object")
    if snapshot.get("branch") != contract.STATE_BRANCH:
        raise StateStoreIntegrityError("verified state snapshot has an unexpected branch")
    if snapshot.get("read_only") is not True:
        raise StateStoreIntegrityError("state snapshot was not marked read-only")

    commit_sha = snapshot.get("commit_sha")
    if not isinstance(commit_sha, str) or not contract.SHA1_RE.fullmatch(commit_sha):
        raise StateStoreIntegrityError("verified state snapshot commit identity is invalid")

    try:
        manifest = contract.validate_manifest(snapshot.get("manifest"))
    except contract.AutomationStateError as exc:
        raise StateStoreIntegrityError("state manifest failed validation: " + str(exc)) from None

    if manifest.get("status") != "ACTIVE" or manifest.get("generation", 0) < 1:
        raise StateStoreIntegrityError("state store is not active at a positive generation")
    if set(manifest["entries"]) != EXPECTED_OBJECTS:
        raise StateStoreIntegrityError(
            "state manifest does not contain exactly the four migrated runtime objects"
        )

    documents = snapshot.get("documents")
    if not isinstance(documents, dict) or set(documents) != EXPECTED_OBJECTS:
        raise StateStoreIntegrityError(
            "verified state snapshot does not contain exactly the four runtime objects"
        )
    verified_objects = snapshot.get("verified_objects")
    if (
        not isinstance(verified_objects, list)
        or len(verified_objects) != len(EXPECTED_OBJECTS)
        or any(not isinstance(key, str) for key in verified_objects)
        or set(verified_objects) != EXPECTED_OBJECTS
    ):
        raise StateStoreIntegrityError("reader did not verify every required state object")

    validator = domain_validator or state_writer.validate_domain_documents
    try:
        validator(documents)
    except Exception as exc:
        raise StateStoreIntegrityError(
            "one or more state objects failed domain validation: " + type(exc).__name__
        ) from None

    return {
        "mode": "ACTIVE_STATE_INTEGRITY_AUDIT",
        "authoritative_source": contract.STATE_BRANCH,
        "state_store_branch": contract.STATE_BRANCH,
        "state_store_commit_sha": commit_sha,
        "state_store_status": manifest["status"],
        "state_store_generation": manifest["generation"],
        "verified_objects": sorted(EXPECTED_OBJECTS),
        "domain_validation": "PASS",
        "runtime_policy_checks": "DELEGATED_TO_CALLING_WORKFLOW",
        "authorization_policy_checked": False,
        "nvidia_lock_policy_checked": False,
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
    args = parser.parse_args(argv)
    if not args.repository:
        parser.error("--repository or GITHUB_REPOSITORY is required")

    try:
        reader = GitHubStateStoreReader(args.repository, args.token)
        snapshot = reader.read_snapshot()
        report = build_state_integrity_report(snapshot)
    except (
        StateStoreIntegrityError,
        StateStoreReadError,
        contract.AutomationStateError,
        state_writer.StateStoreWriteError,
    ) as exc:
        print("STATE STORE INTEGRITY AUDIT FAILED:", type(exc).__name__, str(exc), file=sys.stderr)
        return 1

    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
