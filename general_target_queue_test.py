import copy
import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import general_target_queue as queue


NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def synthetic_rows(count, start="2021-01-01"):
    first = date.fromisoformat(start)
    rows = []
    for index in range(count):
        day = (first + timedelta(days=2 * index)).isoformat()
        base = (index * 7) % 60 + 1
        numbers = sorted({((base + offset * 11 - 1) % 60) + 1 for offset in range(6)})
        if len(numbers) != 6:
            raise AssertionError("invalid synthetic 6-of-60 fixture")
        rows.append({"date": day, "numbers": numbers})
    return rows


def write_archive(path, rows):
    path.write_text(
        json.dumps({"draws": rows}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def append_draws(rows, count):
    start = (date.fromisoformat(rows[-1]["date"]) + timedelta(days=2)).isoformat()
    return rows + synthetic_rows(count, start=start)


class SingleActiveTargetQueueTests(unittest.TestCase):
    def test_bootstrap_pins_unattempted_active_target_while_new_draws_stage_as_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            rows = synthetic_rows(90)
            write_archive(archive, rows)

            initial, summary = queue.capture_new_targets(
                queue.initial_queue(), archive_path=archive,
                attempts=[], events=[], now=NOW,
            )
            self.assertEqual(initial["schema_version"], 2)
            self.assertEqual(summary["pending_target_count"], 1)
            self.assertEqual(initial["active"]["target_date"], rows[-1]["date"])
            self.assertEqual(initial["latest_snapshot"]["target_date"], rows[-1]["date"])
            active_hash = initial["active"]["archive_sha256"]
            self.assertEqual(initial["active"]["archive_record_count"], len(rows))
            self.assertEqual(initial["completed"], [])

            # A newer canonical archive can arrive after the active snapshot
            # was captured but before its first attempt is reserved. The active
            # target and its work must remain pinned to the original prefix.
            grown = append_draws(rows, 2)
            write_archive(archive, grown)
            updated, summary = queue.capture_new_targets(
                initial, archive_path=archive, attempts=[], events=[],
                now=NOW + timedelta(hours=1),
            )
            self.assertEqual(updated["active"]["target_date"], rows[-1]["date"])
            self.assertEqual(updated["active"]["archive_sha256"], active_hash)
            self.assertEqual(updated["active"]["archive_record_count"], len(rows))
            self.assertEqual(updated["latest_snapshot"]["target_date"], grown[-1]["date"])
            self.assertEqual(updated["latest_snapshot"]["archive_record_count"], len(grown))
            self.assertEqual(summary["pending_target_count"], 1)
            self.assertEqual(summary["active_target_date"], rows[-1]["date"])
            self.assertEqual(summary["latest_observed_date"], grown[-1]["date"])
            self.assertEqual(summary["intermediate_draws_promoted_as_targets"], False)
            self.assertEqual(updated["completed"], [])

            active_archive = root / "active-archive.json"
            materialized = queue.materialize_front(updated, active_archive)
            loaded = queue.sealed.load_archive(active_archive)
            self.assertEqual(materialized["target_date"], rows[-1]["date"])
            self.assertEqual(loaded["last_date"], rows[-1]["date"])
            self.assertEqual(loaded["record_count"], len(rows))

            unchanged, summary = queue.capture_new_targets(
                updated, archive_path=archive, attempts=[], events=[],
                now=NOW + timedelta(hours=2),
            )
            self.assertFalse(summary["changed"])
            self.assertEqual(unchanged, updated)

    def test_in_progress_or_failed_target_is_preserved_while_latest_is_staged(self):
        for outcome in ("IN_PROGRESS", "FAILED"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / "archive.json"
                original_rows = synthetic_rows(90)
                write_archive(archive, original_rows)
                initial, _ = queue.capture_new_targets(
                    queue.initial_queue(), archive_path=archive,
                    attempts=[], events=[], now=NOW,
                )
                active = initial["active"]
                attempt = {
                    "attempt_id": "a" * 64,
                    "target_date": active["target_date"],
                    "archive_sha256": active["archive_sha256"],
                    "archive_record_count": active["archive_record_count"],
                    "claimed_at_utc": NOW.isoformat(),
                    "github_run_id": "1234",
                }
                events = []
                if outcome == "FAILED":
                    events.append({
                        "attempt_id": attempt["attempt_id"],
                        "outcome": "FAILED",
                        "recorded_at_utc": (NOW + timedelta(minutes=1)).isoformat(),
                        "reason_code": "NO_PROVIDER_REQUEST_SENT",
                    })
                grown = append_draws(original_rows, 2)
                write_archive(archive, grown)

                updated, summary = queue.capture_new_targets(
                    initial, archive_path=archive, attempts=[attempt],
                    events=events, now=NOW + timedelta(hours=1),
                )
                self.assertEqual(updated["active"]["target_date"], active["target_date"])
                self.assertEqual(updated["active"]["archive_sha256"], active["archive_sha256"])
                self.assertEqual(updated["latest_snapshot"]["target_date"], grown[-1]["date"])
                self.assertEqual(updated["completed"], [])
                self.assertEqual(summary["active_target_date"], active["target_date"])

                workspace_archive = root / "active-archive.json"
                materialized = queue.materialize_front(updated, workspace_archive)
                loaded = queue.sealed.load_archive(workspace_archive)
                self.assertEqual(materialized["target_date"], active["target_date"])
                self.assertEqual(loaded["last_date"], active["target_date"])
                self.assertEqual(loaded["record_count"], len(original_rows))

    def test_successful_package_is_archived_then_latest_target_is_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            original_rows = synthetic_rows(90)
            write_archive(archive, original_rows)
            initial, _ = queue.capture_new_targets(
                queue.initial_queue(), archive_path=archive,
                attempts=[], events=[], now=NOW,
            )
            active = initial["active"]
            attempt = {
                "attempt_id": "b" * 64,
                "target_date": active["target_date"],
                "archive_sha256": active["archive_sha256"],
                "archive_record_count": active["archive_record_count"],
                "claimed_at_utc": NOW.isoformat(),
                "github_run_id": "1235",
            }
            events = [{
                "attempt_id": attempt["attempt_id"],
                "outcome": "SUCCEEDED",
                "recorded_at_utc": (NOW + timedelta(minutes=1)).isoformat(),
                "reason_code": "EVALUATION_COMPLETED",
            }]
            grown = append_draws(original_rows, 3)
            write_archive(archive, grown)
            updated, summary = queue.capture_new_targets(
                initial, archive_path=archive, attempts=[attempt],
                events=events, now=NOW + timedelta(hours=1),
            )
            self.assertEqual(updated["active"]["target_date"], grown[-1]["date"])
            self.assertEqual(updated["completed"][0]["target_date"], active["target_date"])
            self.assertEqual(updated["completed"][0]["outcome"], "EVALUATED")
            self.assertEqual(summary["pending_target_count"], 1)

    def test_success_and_no_candidate_keep_same_target_active_until_new_draw(self):
        for outcome, reason in (
            ("SUCCEEDED", "EVALUATION_COMPLETED"),
            ("NO_ELIGIBLE_CANDIDATE", "NO_FROZEN_DIRECTIONAL_CANDIDATE"),
        ):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / "archive.json"
                rows = synthetic_rows(90)
                write_archive(archive, rows)
                initial, _ = queue.capture_new_targets(
                    queue.initial_queue(), archive_path=archive,
                    attempts=[], events=[], now=NOW,
                )
                active = initial["active"]
                attempt = {
                    "attempt_id": "c" * 64,
                    "target_date": active["target_date"],
                    "archive_sha256": active["archive_sha256"],
                    "archive_record_count": active["archive_record_count"],
                    "claimed_at_utc": NOW.isoformat(),
                    "github_run_id": "1236",
                }
                events = [{
                    "attempt_id": attempt["attempt_id"],
                    "outcome": outcome,
                    "recorded_at_utc": (NOW + timedelta(minutes=1)).isoformat(),
                    "reason_code": reason,
                }]
                unchanged_target, _ = queue.capture_new_targets(
                    initial, archive_path=archive, attempts=[attempt],
                    events=events, now=NOW + timedelta(hours=1),
                )
                self.assertEqual(unchanged_target["active"]["target_date"], active["target_date"])
                self.assertEqual(unchanged_target["completed"], [])

                grown = append_draws(rows, 1)
                write_archive(archive, grown)
                promoted, _ = queue.capture_new_targets(
                    unchanged_target, archive_path=archive, attempts=[attempt],
                    events=events, now=NOW + timedelta(hours=2),
                )
                self.assertEqual(promoted["active"]["target_date"], grown[-1]["date"])
                self.assertEqual(promoted["completed"][0]["target_date"], active["target_date"])

    def test_only_exact_evaluated_completion_or_ten_failed_attempts_release_active_target(self):
        rows = synthetic_rows(90)
        raw = queue._snapshot_bytes(rows)
        item = queue._make_item(
            rows, source_archive_sha256="a" * 64,
            enqueued_at_utc=NOW.isoformat(),
        )
        q = {
            "schema_version": 2,
            "observed_through_date": rows[-1]["date"],
            "active": item,
            "latest_snapshot": copy.deepcopy(item),
            "completed": [],
        }
        with self.assertRaisesRegex(queue.TargetQueueError, "Only an EVALUATED"):
            queue.complete_front(
                q, target_date=rows[-1]["date"], archive_sha256=item["archive_sha256"],
                archive_record_count=len(rows), outcome="FAILED", now=NOW,
            )
        completed = queue.complete_front(
            q, target_date=rows[-1]["date"], archive_sha256=item["archive_sha256"],
            archive_record_count=len(rows), outcome="EVALUATED", now=NOW,
        )
        self.assertIsNone(completed["active"])
        self.assertEqual(completed["completed"][0]["outcome"], "EVALUATED")

        with self.assertRaisesRegex(queue.TargetQueueError, "exactly ten failed attempts"):
            queue.retire_exhausted_front(
                q, target_date=rows[-1]["date"], archive_sha256=item["archive_sha256"],
                archive_record_count=len(rows), failed_attempt_count=9, now=NOW,
            )
        retired = queue.retire_exhausted_front(
            q, target_date=rows[-1]["date"], archive_sha256=item["archive_sha256"],
            archive_record_count=len(rows), failed_attempt_count=10, now=NOW,
        )
        self.assertIsNone(retired["active"])
        self.assertEqual(retired["completed"][0]["outcome"], "EXHAUSTED_RETRY_BUDGET")
        self.assertEqual(len(q["completed"]), 0, "input queue must remain immutable")

    def test_archive_mutation_under_active_or_latest_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            rows = synthetic_rows(90)
            write_archive(archive, rows)
            initial, _ = queue.capture_new_targets(
                queue.initial_queue(), archive_path=archive,
                attempts=[], events=[], now=NOW,
            )
            changed = copy.deepcopy(rows)
            changed[10]["numbers"] = sorted([1, 2, 3, 4, 5, 7])
            write_archive(archive, changed)
            before = copy.deepcopy(initial)
            with self.assertRaisesRegex(queue.TargetQueueError, "changed beneath"):
                queue.capture_new_targets(
                    initial, archive_path=archive, attempts=[], events=[], now=NOW,
                )
            self.assertEqual(initial, before)

    def test_snapshot_payload_tampering_and_duplicate_json_keys_are_rejected(self):
        rows = synthetic_rows(90)
        item = queue._make_item(
            rows, source_archive_sha256="b" * 64,
            enqueued_at_utc=NOW.isoformat(),
        )
        bad = copy.deepcopy(item)
        bad["snapshot_zlib_base64"] = bad["snapshot_zlib_base64"][:-4] + "AAAA"
        with self.assertRaises(queue.TargetQueueError):
            queue._decode_item(bad)
        with self.assertRaisesRegex(queue.TargetQueueError, "Duplicate JSON key"):
            queue._strict_json_loads(b'{"draws":[],"draws":[]}')

    def test_legacy_fifo_migration_pins_unattempted_head_before_newer_queued_draws(self):
        rows = synthetic_rows(90)
        legacy_items = [
            queue._make_item(
                rows[:index + 1],
                source_archive_sha256=("d" * 63) + str(index % 10),
                enqueued_at_utc=NOW.isoformat(),
            )
            for index in range(87, 90)
        ]
        legacy = {
            "schema_version": 1,
            "observed_through_date": rows[-1]["date"],
            "items": legacy_items,
            "completed": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "archive.json"
            write_archive(archive, rows)
            migrated, summary = queue.capture_new_targets(
                legacy, archive_path=archive, attempts=[], events=[],
                now=NOW + timedelta(hours=1),
            )

        # The old scheduled workflow materialized the FIFO head for offline
        # discovery before reserving a provider attempt. Migration must retain
        # that exact unattempted head while staging the newest archive cursor.
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["active"]["target_date"], legacy_items[0]["target_date"])
        self.assertEqual(migrated["active"]["archive_sha256"], legacy_items[0]["archive_sha256"])
        self.assertEqual(migrated["latest_snapshot"]["target_date"], rows[-1]["date"])
        self.assertEqual(summary["pending_target_count"], 1)
        self.assertNotIn("items", migrated)

    def test_legacy_fifo_migration_preserves_first_unfinished_target_only(self):
        rows = synthetic_rows(90)
        legacy_items = [
            queue._make_item(
                rows[:index + 1],
                source_archive_sha256=("c" * 63) + str(index % 10),
                enqueued_at_utc=NOW.isoformat(),
            )
            for index in range(87, 90)
        ]
        legacy = {
            "schema_version": 1,
            "observed_through_date": rows[-1]["date"],
            "items": legacy_items,
            "completed": [],
        }
        attempt = {
            "attempt_id": "d" * 64,
            "target_date": legacy_items[0]["target_date"],
            "archive_sha256": legacy_items[0]["archive_sha256"],
            "archive_record_count": legacy_items[0]["archive_record_count"],
            "claimed_at_utc": NOW.isoformat(),
            "github_run_id": "1237",
        }
        event = {
            "attempt_id": attempt["attempt_id"],
            "outcome": "FAILED",
            "recorded_at_utc": (NOW + timedelta(minutes=1)).isoformat(),
            "reason_code": "NO_PROVIDER_REQUEST_SENT",
        }
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "archive.json"
            write_archive(archive, rows)
            migrated, _ = queue.capture_new_targets(
                legacy, archive_path=archive, attempts=[attempt],
                events=[event], now=NOW + timedelta(hours=1),
            )
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["active"]["target_date"], legacy_items[0]["target_date"])
        self.assertEqual(migrated["latest_snapshot"]["target_date"], rows[-1]["date"])
        self.assertEqual(migrated["completed"], [])


if __name__ == "__main__":
    unittest.main()
