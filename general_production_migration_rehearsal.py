#!/usr/bin/env python3
"""Read-only rehearsal of the real state-v5/queue-v1 migration on a verified production snapshot.

The reader uses GET-only GitHub access. All migration functions operate on deep
copies and a temporary archive file. This script never imports the state writer,
writes the production branch, calls NVIDIA, or sends Telegram messages.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import general_autonomous_research as autonomous
import general_research_memory
import general_sealed_evaluation as sealed
import general_target_queue as target_queue
from general_automation_state_reader import GitHubStateStoreReader


REPOSITORY = "xxlyusufi61-bot/general-super-loto"
EXPECTED_ACTIVE_DATE = "2026-11-15"
EXPECTED_INTERMEDIATE_DATE = "2026-11-17"
EXPECTED_LATEST_DATE = "2026-11-19"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def rehearsal_mode(source_state: dict, source_queue: dict) -> str:
    """Select the one-time legacy migration or current-schema validation path."""
    pair = (source_state.get("schema_version"), source_queue.get("schema_version"))
    if pair == (5, 1):
        return "MIGRATE_LEGACY"
    if pair == (6, 2):
        return "VALIDATE_CURRENT"
    raise RuntimeError("Production scheduler/queue schema pair is unsupported; rehearsal stopped.")


def validate_current_schema_snapshot(source_state: dict, archive_rows: list[dict]) -> dict:
    """Validate an already-migrated production state without mutating it."""
    checked_state = copy.deepcopy(source_state)
    autonomous.validate_state(checked_state)
    queue = target_queue.validate_queue(checked_state["target_queue"])
    rows = target_queue._copy_rows(archive_rows)
    latest = target_queue.latest_snapshot_item(queue)
    active = target_queue.active_item(queue)
    require(latest is not None, "Migrated queue has no latest archive snapshot.")
    require(
        latest["target_date"] <= rows[-1]["date"]
        and latest["archive_record_count"] <= len(rows),
        "Migrated latest snapshot is newer than the canonical archive.",
    )
    target_queue._verify_item_against_archive(latest, rows)
    if active is not None:
        target_queue._verify_item_against_archive(active, rows)
    require(
        queue["observed_through_date"] == rows[-1]["date"],
        "Migrated queue cursor does not match the canonical archive.",
    )
    return {
        "active_target_date": active["target_date"] if active is not None else None,
        "latest_snapshot_date": latest["target_date"],
    }


def main() -> None:
    repository = os.environ.get("GITHUB_REPOSITORY", REPOSITORY)
    token = os.environ.get("GITHUB_TOKEN")
    reader = GitHubStateStoreReader(repository, token)
    snapshot = reader.read_snapshot()

    require(snapshot.get("read_only") is True, "Production snapshot was not marked read-only.")
    require(snapshot.get("branch") == "general-automation-state", "Unexpected production state branch.")
    manifest = snapshot["manifest"]
    documents = snapshot["documents"]
    require(set(documents) == {"archive", "evaluation_ledger", "autonomous_research_state", "research_memory"},
            "Production state object inventory changed; rehearsal stopped.")

    source_state = copy.deepcopy(documents["autonomous_research_state"])
    source_archive = copy.deepcopy(documents["archive"])
    source_ledger = copy.deepcopy(documents["evaluation_ledger"])
    source_memory = copy.deepcopy(documents["research_memory"])
    source_queue = source_state.get("target_queue")

    require(isinstance(source_queue, dict), "Production target queue is missing.")
    mode = rehearsal_mode(source_state, source_queue)
    migration_required = mode == "MIGRATE_LEGACY"
    already_migrated = mode == "VALIDATE_CURRENT"
    if migration_required:
        require(isinstance(source_queue.get("items"), list), "Legacy FIFO items are missing.")
        legacy_dates = [item.get("target_date") for item in source_queue["items"]]
        require(
            legacy_dates == [EXPECTED_ACTIVE_DATE, EXPECTED_INTERMEDIATE_DATE, EXPECTED_LATEST_DATE],
            "Legacy target order changed; do not apply assumptions from an outdated rehearsal.",
        )
        require(
            source_state.get("target_evaluations") is None,
            "State v5 unexpectedly contains a target-evaluation receipt field.",
        )
    else:
        # Subsequent PRs must remain green after the one-time production
        # migration. Validate the already-migrated objects without assuming
        # that the active target date is still the original migration target.
        autonomous.validate_state(source_state)
        target_queue.validate_queue(source_queue)
        require(
            isinstance(source_state.get("target_evaluations"), list),
            "Migrated scheduler state is missing its score-free evaluation receipts.",
        )

    archive_entry = manifest["entries"]["archive"]
    # Fetch the exact immutable Git blob identified by the already-verified manifest.
    # No archive rows, draw values, predictions or scores are printed.
    raw_archive = reader._blob_text(archive_entry["blob_sha"])
    raw_digest = hashlib.sha256(raw_archive.encode("utf-8")).hexdigest()
    require(raw_digest == archive_entry["content_sha256"],
            "Exact production archive bytes did not match the verified manifest.")
    require(json.loads(raw_archive) == source_archive,
            "Archive blob content differs from the verified production snapshot.")

    # Preserve all existing history exactly. Only the schema and target queue
    # representation may change in the isolated copy.
    preserved_fields = (
        "authorization", "attempts", "events", "recovery_history",
        "manual_retry_history", "workflow_retry_history",
    )
    if already_migrated:
        preserved_fields += ("target_evaluations",)
    preserved_before = {key: copy.deepcopy(source_state[key]) for key in preserved_fields}
    source_documents_before = copy.deepcopy(documents)

    with tempfile.TemporaryDirectory(prefix="general-production-migration-rehearsal-") as directory:
        root = Path(directory)
        archive_path = root / "super_loto_archive.json"
        archive_path.write_text(raw_archive, encoding="utf-8")

        if migration_required:
            # Execute the actual PR v5->v6 state validator/migrator on a deep copy.
            migrated_state = copy.deepcopy(source_state)
            autonomous.validate_state(migrated_state)
            require(migrated_state.get("schema_version") == 6,
                    "Scheduler state did not migrate from v5 to v6.")
            require(migrated_state.get("target_evaluations") == [],
                    "New target-evaluation receipt ledger did not initialize empty.")

            # Execute the actual PR queue v1->v2 migration with the real, verified
            # production archive, attempts and events.
            migrated_queue, summary = target_queue.capture_new_targets(
                migrated_state["target_queue"],
                archive_path=archive_path,
                attempts=migrated_state["attempts"],
                events=migrated_state["events"],
                now=datetime.now(timezone.utc),
            )
            migrated_state["target_queue"] = migrated_queue
            autonomous.validate_state(migrated_state)

            active = target_queue.active_item(migrated_queue)
            latest = target_queue.latest_snapshot_item(migrated_queue)
            require(migrated_queue.get("schema_version") == 2,
                    "Target queue did not migrate from v1 to v2.")
            require(active is not None and active["target_date"] == EXPECTED_ACTIVE_DATE,
                    "The original active target was not preserved.")
            require(latest is not None and latest["target_date"] == EXPECTED_LATEST_DATE,
                    "The latest production archive snapshot was not retained.")
            require(active["archive_sha256"] == source_queue["items"][0]["archive_sha256"],
                    "The active snapshot hash changed during migration.")
            require(latest["archive_sha256"] == source_queue["items"][-1]["archive_sha256"],
                    "The latest snapshot hash changed during migration.")
            require(migrated_queue.get("observed_through_date") == EXPECTED_LATEST_DATE,
                    "The migrated archive cursor is incorrect.")
            require(len(migrated_queue.get("completed", [])) == len(source_queue.get("completed", [])),
                    "Legacy completed-target history changed unexpectedly.")
            require(all(item in migrated_queue["completed"] for item in source_queue.get("completed", [])),
                    "An existing completed-target record was not preserved.")
            require(not any(item["target_date"] == EXPECTED_INTERMEDIATE_DATE
                            for item in migrated_queue.get("completed", [])),
                    "The intermediate queued draw was incorrectly promoted to a separate target.")
            require(summary.get("pending_target_count") == 1 and summary.get("has_pending") is True,
                    "Migration did not produce exactly one active target.")
            require(len(migrated_queue.get("items", [])) == 0,
                    "Legacy FIFO items remain after queue-v2 migration.")
        else:
            # Production has already completed this one-time migration. Validate
            # the real v6/v2 state against the exact archive without assuming the
            # original active target is still current or trying to migrate twice.
            migrated_state = copy.deepcopy(source_state)
            archive_view = sealed.load_archive(archive_path)
            validated = validate_current_schema_snapshot(
                migrated_state, archive_view["rows"]
            )
            migrated_queue = target_queue.validate_queue(migrated_state["target_queue"])
            active = target_queue.active_item(migrated_queue)
            latest = target_queue.latest_snapshot_item(migrated_queue)
        for key, value in preserved_before.items():
            require(migrated_state[key] == value,
                    "Historical scheduler field changed during migration: " + key)

        # Verify every migrated document still passes the real validators.
        ledger_path = root / "general_evaluation_ledger.json"
        ledger_path.write_text(
            json.dumps(source_ledger, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        sealed.load_ledger(ledger_path)

        memory_path = root / "general_research_memory.json"
        memory_path.write_text(
            json.dumps(source_memory, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        general_research_memory.load_memory(memory_path)
        require(documents == source_documents_before,
                "The production snapshot object was mutated during the rehearsal.")

    print("PRODUCTION MIGRATION REHEARSAL: PASS")
    print("Source: verified general-automation-state snapshot (read-only)")
    if migration_required:
        print("Scheduler state: v5 -> v6 PASS")
        print("Target queue: v1 -> v2 PASS")
        print("Active target preserved:", EXPECTED_ACTIVE_DATE)
        print("Latest snapshot preserved:", EXPECTED_LATEST_DATE)
        print("Intermediate draw not promoted:", EXPECTED_INTERMEDIATE_DATE)
    else:
        print("Scheduler state: v6 already migrated; validation PASS")
        print("Target queue: v2 already migrated; validation PASS")
        print("Active/latest snapshot integrity: PASS")
    print("Attempt/event/recovery/manual/workflow history: preserved")
    print("Production writes: 0 | NVIDIA requests: 0 | Telegram messages: 0")


if __name__ == "__main__":
    main()
