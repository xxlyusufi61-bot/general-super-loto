import copy
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import general_target_queue as queue


NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)


def synthetic_rows(count, start="2021-01-01"):
    first = date.fromisoformat(start)
    rows = []
    for index in range(count):
        day = (first + timedelta(days=2 * index)).isoformat()
        base = (index * 7) % 60 + 1
        numbers = sorted({((base + offset * 11 - 1) % 60) + 1 for offset in range(6)})
        rows.append({"date": day, "numbers": numbers})
    return rows


def write_archive(path, rows):
    path.write_text(
        json.dumps({"draws": rows}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def append_rows(rows, count):
    start = (date.fromisoformat(rows[-1]["date"]) + timedelta(days=2)).isoformat()
    return rows + synthetic_rows(count, start=start)


class SingleActiveTargetQueueTests(unittest.TestCase):
    def test_bootstrap_and_unattempted_archive_growth_keep_only_the_latest_target(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "archive.json"
            rows = synthetic_rows(90)
            write_archive(archive, rows)
            first, summary = queue.capture_new_targets(
                queue.initial_queue(), archive_path=archive,
                attempts=[], events=[], now=NOW,
            )
            self.assertEqual(first["schema_version"], 2)
            self.assertEqual(first["active"]["target_date"], rows[-1]["date"])
            self.assertEqual(first["latest_snapshot"]["target_date"], rows[-1]["date"])
            self.assertEqual(summary["pending_target_count"], 1)
            self.assertEqual(summary["intermediate_draws_promoted_as_targets"], False)

            grown = append_rows(rows, 3)
            write_archive(archive, grown)
            promoted, summary = queue.capture_new_targets(
                first, archive_path=archive, attempts=[], events=[],
                now=NOW + timedelta(hours=1),
            )
            self.assertEqual(promoted["active"]["target_date"], grown[-1]["date"])
            self.assertEqual(promoted["latest_snapshot"]["target_date"], grown[-1]["date"])
            self.assertEqual(promoted["completed"], [])
            self.assertEqual(summary["pending_target_count"], 1)
            self.assertEqual(summary["latest_observed_date"], grown[-1]["date"])
            unchanged, second_summary = queue.capture_new_targets(
                promoted, archive_path=archive, attempts=[], events=[],
                now=NOW + timedelta(hours=2),
            )
            self.assertFalse(second_summary["changed"])
            self.assertEqual(unchanged, promoted)

    def test_in_progress_and_failed_target_remain_pinned_while_latest_snapshot_advances(self):
        for terminal in (False, True):
            with self.subTest(terminal=terminal), tempfile.TemporaryDirectory() as directory:
                archive = Path(directory) / "archive.json"
                original_rows = synthetic_rows(90)
                write_archive(archive, original_rows)
                initial, _ = queue.capture_new_targets(
                    queue.initial_queue(), archive_path=archive,
                    attempts=[], events=[], now=NOW,
                )
                active = initial["active"]
                attempt = {
                    "attempt_id": ("a" if terminal else "b") * 64,
                    "target_date": active["target_date"],
                    "archive_sha256": active["archive_sha256"],
                    "archive_record_count": active["archive_record_count"],
                    "claimed_at_utc": NOW.isoformat(),
                    "github_run_id": "91001",
                }
                events = []
                if terminal:
                    events.append({
                        "attempt_id": attempt["attempt_id"],
                        "outcome": "FAILED",
                        "recorded_at_utc": (NOW + timedelta(minutes=1)).isoformat(),
                        "reason_code": "NO_PROVIDER_REQUEST_SENT",
                    })
                latest_rows = append_rows(original_rows, 2)
                write_archive(archive, latest_rows)
                updated, summary = queue.capture_new_targets(
                    initial, archive_path=archive,
                    attempts=[attempt], events=events,
                    now=NOW + timedelta(hours=1),
                )
                self.assertEqual(updated["active"]["target_date"], active["target_date"])
                self.assertEqual(updated["active"]["archive_sha256"], active["archive_sha256"])
                self.assertEqual(updated["latest_snapshot"]["target_date"], latest_rows[-1]["date"])
                self.assertEqual(updated["completed"], [])
                self.assertEqual(summary["active_target_date"], active["target_date"])

                materialized = Path(directory) / "active-archive.json"
                result = queue.materialize_front(updated, materialized)
                loaded = queue.sealed.load_archive(materialized)
                self.assertEqual(result["target_date"], active["target_date"])
                self.assertEqual(loaded["last_date"], active["target_date"])
                self.assertEqual(loaded["record_count"], len(original_rows))

    def test_successful_and_no_candidate_packages_close_only_when_a_newer_draw_arrives(self):
        cases = (
            ("SUCCEEDED", "EVALUATED"),
            ("NO_ELIGIBLE_CANDIDATE", "NO_ELIGIBLE_CANDIDATE"),
        )
        for outcome, expected_completed in cases:
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                archive = Path(directory) / "archive.json"
                rows = synthetic_rows(90)
                write_archive(archive, rows)
                initial, _ = queue.capture_new_targets(
                    queue.initial_queue(), archive_path=archive,
                    attempts=[], events=[], now=NOW,
                )
                active = initial["active"]
                attempt = {
                    "attempt_id": ("c" if outcome == "SUCCEEDED" else "d") * 64,
                    "target_date": active["target_date"],
                    "archive_sha256": active["archive_sha256"],
                    "archive_record_count": active["archive_record_count"],
                    "claimed_at_utc": NOW.isoformat(),
                    "github_run_id": "91002",
                }
                event = {
                    "attempt_id": attempt["attempt_id"],
                    "outcome": outcome,
                    "recorded_at_utc": (NOW + timedelta(minutes=1)).isoformat(),
                    "reason_code": (
                        "EVALUATION_COMPLETED" if outcome == "SUCCEEDED"
                        else "NO_FROZEN_DIRECTIONAL_CANDIDATE"
                    ),
                }
                same_archive, _ = queue.capture_new_targets(
                    initial, archive_path=archive, attempts=[attempt],
                    events=[event], now=NOW + timedelta(hours=1),
                )
                self.assertEqual(same_archive["active"]["target_date"], active["target_date"])
                self.assertEqual(same_archive["completed"], [])

                newer_rows = append_rows(rows, 2)
                write_archive(archive, newer_rows)
                updated, summary = queue.capture_new_targets(
                    same_archive, archive_path=archive, attempts=[attempt],
                    events=[event], now=NOW + timedelta(hours=2),
                )
                self.assertEqual(updated["active"]["target_date"], newer_rows[-1]["date"])
                self.assertEqual(updated["completed"][-1]["target_date"], active["target_date"])
                self.assertEqual(updated["completed"][-1]["outcome"], expected_completed)
                self.assertEqual(summary["pending_target_count"], 1)

    def test_only_exact_evaluated_completion_or_ten_failed_attempts_release_active(self):
        rows = synthetic_rows(90)
        item = queue._make_item(
            rows, source_archive_sha256="e" * 64,
            enqueued_at_utc=NOW.isoformat(),
        )
        current = {
            "schema_version": 2,
            "observed_through_date": rows[-1]["date"],
            "active": item,
            "latest_snapshot": copy.deepcopy(item),
            "completed": [],
        }
        with self.assertRaisesRegex(queue.TargetQueueError, "Only an EVALUATED"):
            queue.complete_front(
                current, target_date=item["target_date"],
                archive_sha256=item["archive_sha256"],
                archive_record_count=len(rows), outcome="FAILED", now=NOW,
            )
        with self.assertRaisesRegex(queue.TargetQueueError, "exactly ten failed attempts"):
            queue.retire_exhausted_front(
                current, target_date=item["target_date"],
                archive_sha256=item["archive_sha256"],
                archive_record_count=len(rows), failed_attempt_count=9, now=NOW,
            )
        retired = queue.retire_exhausted_front(
            current, target_date=item["target_date"],
            archive_sha256=item["archive_sha256"],
            archive_record_count=len(rows), failed_attempt_count=10, now=NOW,
        )
        self.assertIsNone(retired["active"])
        self.assertEqual(retired["completed"][-1]["outcome"], "EXHAUSTED_RETRY_BUDGET")
        evaluated = queue.complete_front(
            current, target_date=item["target_date"],
            archive_sha256=item["archive_sha256"],
            archive_record_count=len(rows), outcome="EVALUATED", now=NOW,
        )
        self.assertIsNone(evaluated["active"])
        self.assertEqual(evaluated["completed"][-1]["outcome"], "EVALUATED")
        self.assertEqual(current["completed"], [], "input queue must remain immutable")

    def test_archive_mutation_beneath_active_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "archive.json"
            rows = synthetic_rows(90)
            write_archive(archive, rows)
            current, _ = queue.capture_new_targets(
                queue.initial_queue(), archive_path=archive,
                attempts=[], events=[], now=NOW,
            )
            before = copy.deepcopy(current)
            changed = copy.deepcopy(rows)
            changed[10]["numbers"] = sorted([1, 2, 3, 4, 5, 7])
            write_archive(archive, changed)
            with self.assertRaisesRegex(queue.TargetQueueError, "changed beneath"):
                queue.capture_new_targets(
                    current, archive_path=archive,
                    attempts=[], events=[], now=NOW,
                )
            self.assertEqual(current, before)

    def test_snapshot_tampering_and_duplicate_json_keys_are_rejected(self):
        rows = synthetic_rows(90)
        item = queue._make_item(
            rows, source_archive_sha256="f" * 64,
            enqueued_at_utc=NOW.isoformat(),
        )
        bad = copy.deepcopy(item)
        bad["snapshot_zlib_base64"] = bad["snapshot_zlib_base64"][:-4] + "AAAA"
        with self.assertRaises(queue.TargetQueueError):
            queue._decode_item(bad)
        with self.assertRaisesRegex(queue.TargetQueueError, "Duplicate JSON key"):
            queue._strict_json_loads(b'{"draws":[],"draws":[]}')

    def test_schema_v1_fifo_state_migrates_to_one_active_target_without_losing_failed_work(self):
        rows = synthetic_rows(90)
        legacy_items = [
            queue._make_item(
                rows[:index + 1],
                source_archive_sha256=("1" * 63) + str(index % 10),
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
        failed_attempt = {
            "attempt_id": "2" * 64,
            "target_date": legacy_items[0]["target_date"],
            "archive_sha256": legacy_items[0]["archive_sha256"],
            "archive_record_count": legacy_items[0]["archive_record_count"],
            "claimed_at_utc": NOW.isoformat(),
            "github_run_id": "91003",
        }
        failed_event = {
            "attempt_id": failed_attempt["attempt_id"],
            "outcome": "FAILED",
            "recorded_at_utc": (NOW + timedelta(minutes=1)).isoformat(),
            "reason_code": "NO_PROVIDER_REQUEST_SENT",
        }
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "archive.json"
            write_archive(archive, rows)
            migrated, _ = queue.capture_new_targets(
                legacy, archive_path=archive,
                attempts=[failed_attempt], events=[failed_event],
                now=NOW + timedelta(hours=1),
            )
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["active"]["target_date"], legacy_items[0]["target_date"])
        self.assertEqual(migrated["latest_snapshot"]["target_date"], rows[-1]["date"])
        self.assertEqual(migrated["completed"], [])


if __name__ == "__main__":
    unittest.main()
