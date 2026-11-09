import copy
import hashlib
import json
import tempfile
import unittest
import zlib
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


class DurableTargetQueueTests(unittest.TestCase):
    def test_bootstrap_queues_only_current_latest_target_then_captures_every_intermediate_draw(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            rows = synthetic_rows(90)
            write_archive(archive, rows)

            initial, summary = queue.capture_new_targets(
                queue.initial_queue(), archive_path=archive,
                attempts=[], events=[], now=NOW,
            )
            self.assertTrue(summary["changed"])
            self.assertEqual(summary["pending_target_count"], 1)
            self.assertEqual(initial["items"][0]["target_date"], rows[-1]["date"])
            self.assertEqual(initial["observed_through_date"], rows[-1]["date"])

            rows.extend(synthetic_rows(2, start=(date.fromisoformat(rows[-1]["date"]) + timedelta(days=2)).isoformat()))
            write_archive(archive, rows)
            updated, summary = queue.capture_new_targets(
                initial, archive_path=archive,
                attempts=[], events=[], now=NOW + timedelta(hours=1),
            )
            self.assertEqual(summary["pending_target_count"], 3)
            self.assertEqual(
                [item["target_date"] for item in updated["items"]],
                [row["date"] for row in rows[-3:]],
            )
            self.assertEqual(updated["observed_through_date"], rows[-1]["date"])

            unchanged, summary = queue.capture_new_targets(
                updated, archive_path=archive,
                attempts=[], events=[], now=NOW + timedelta(hours=2),
            )
            self.assertFalse(summary["changed"])
            self.assertEqual(unchanged, updated)

    def test_materialize_returns_only_fifo_head_snapshot_and_validates_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            output = root / "workspace-archive.json"
            rows = synthetic_rows(90)
            write_archive(archive, rows)
            first, _ = queue.capture_new_targets(
                queue.initial_queue(), archive_path=archive,
                attempts=[], events=[], now=NOW,
            )
            rows.extend(synthetic_rows(2, start=(date.fromisoformat(rows[-1]["date"]) + timedelta(days=2)).isoformat()))
            write_archive(archive, rows)
            populated, _ = queue.capture_new_targets(
                first, archive_path=archive, attempts=[], events=[], now=NOW,
            )
            result = queue.materialize_front(populated, output)
            self.assertEqual(result["target_date"], rows[-3]["date"])
            self.assertEqual(result["archive_record_count"], len(rows) - 2)
            loaded = queue.sealed.load_archive(output)
            self.assertEqual(loaded["last_date"], rows[-3]["date"])
            self.assertEqual(loaded["record_count"], len(rows) - 2)
            self.assertNotEqual(loaded["last_date"], rows[-1]["date"])

    def test_only_matching_terminal_outcome_releases_fifo_head(self):
        rows = synthetic_rows(90)
        raw = queue._snapshot_bytes(rows)
        item = queue._make_item(
            rows, source_archive_sha256="a" * 64,
            enqueued_at_utc=NOW.isoformat(),
        )
        q = {
            "schema_version": 1,
            "observed_through_date": rows[-1]["date"],
            "items": [item],
            "completed": [],
        }
        with self.assertRaisesRegex(queue.TargetQueueError, "Only terminal successful"):
            queue.complete_front(
                q, target_date=rows[-1]["date"], archive_sha256=hashlib.sha256(raw).hexdigest(),
                archive_record_count=len(rows), outcome="FAILED", now=NOW,
            )
        with self.assertRaisesRegex(queue.TargetQueueError, "does not match"):
            queue.complete_front(
                q, target_date=rows[-2]["date"], archive_sha256=item["archive_sha256"],
                archive_record_count=len(rows), outcome="SUCCEEDED", now=NOW,
            )
        completed = queue.complete_front(
            q, target_date=rows[-1]["date"], archive_sha256=item["archive_sha256"],
            archive_record_count=len(rows), outcome="NO_ELIGIBLE_CANDIDATE", now=NOW,
        )
        self.assertEqual(completed["items"], [])
        self.assertEqual(completed["completed"][0]["target_date"], rows[-1]["date"])
        self.assertEqual(completed["completed"][0]["outcome"], "NO_ELIGIBLE_CANDIDATE")
        self.assertEqual(len(q["items"]), 1, "input queue must remain immutable")

    def test_archive_mutation_under_pending_snapshot_fails_closed_without_changing_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            rows = synthetic_rows(90)
            write_archive(archive, rows)
            q, _ = queue.capture_new_targets(
                queue.initial_queue(), archive_path=archive,
                attempts=[], events=[], now=NOW,
            )
            original = copy.deepcopy(q)
            changed = copy.deepcopy(rows)
            changed[10]["numbers"] = sorted([1, 2, 3, 4, 5, 7])
            write_archive(archive, changed)
            with self.assertRaisesRegex(queue.TargetQueueError, "changed beneath"):
                queue.capture_new_targets(
                    q, archive_path=archive, attempts=[], events=[], now=NOW,
                )
            self.assertEqual(q, original)

    def test_capacity_failure_does_not_advance_cursor_or_drop_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            rows = synthetic_rows(90)
            write_archive(archive, rows)
            q, _ = queue.capture_new_targets(
                queue.initial_queue(), archive_path=archive,
                attempts=[], events=[], now=NOW,
            )
            original_cursor = q["observed_through_date"]
            rows.extend(synthetic_rows(2, start=(date.fromisoformat(rows[-1]["date"]) + timedelta(days=2)).isoformat()))
            write_archive(archive, rows)
            prior_limit = queue.MAX_PENDING_TARGETS
            try:
                queue.MAX_PENDING_TARGETS = 1
                with self.assertRaisesRegex(queue.TargetQueueError, "capacity reached"):
                    queue.capture_new_targets(
                        q, archive_path=archive, attempts=[], events=[], now=NOW,
                    )
            finally:
                queue.MAX_PENDING_TARGETS = prior_limit
            self.assertEqual(q["observed_through_date"], original_cursor)
            self.assertEqual(len(q["items"]), 1)

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

        duplicate = b'{"draws":[],"draws":[]}'
        with self.assertRaisesRegex(queue.TargetQueueError, "Duplicate JSON key"):
            queue._strict_json_loads(duplicate)

    def test_legacy_active_attempt_is_queued_only_when_snapshot_identity_is_proven(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            rows = synthetic_rows(90)
            write_archive(archive, rows)
            raw = queue._snapshot_bytes(rows)
            attempt = {
                "attempt_id": "c" * 64,
                "target_date": rows[-1]["date"],
                "archive_sha256": hashlib.sha256(raw).hexdigest(),
                "archive_record_count": len(rows),
                "claimed_at_utc": NOW.isoformat(),
                "github_run_id": "123",
            }
            q, summary = queue.capture_new_targets(
                queue.initial_queue(), archive_path=archive,
                attempts=[attempt], events=[], now=NOW,
            )
            self.assertTrue(summary["has_pending"])
            self.assertEqual(q["items"][0]["target_date"], rows[-1]["date"])

            unproven = copy.deepcopy(attempt)
            unproven["archive_sha256"] = "d" * 64
            with self.assertRaisesRegex(queue.TargetQueueError, "cannot be proven"):
                queue.capture_new_targets(
                    queue.initial_queue(), archive_path=archive,
                    attempts=[unproven], events=[], now=NOW,
                )


if __name__ == "__main__":
    unittest.main()
