import unittest

import general_autonomous_research as autonomous
import general_production_migration_rehearsal as rehearsal
import general_target_queue as target_queue


def make_rows():
    return [
        {"date": "2026-11-15", "numbers": [1, 8, 17, 26, 35, 44]},
        {"date": "2026-11-17", "numbers": [2, 9, 18, 27, 36, 45]},
        {"date": "2026-11-19", "numbers": [3, 10, 19, 28, 37, 46]},
    ]


def current_state(rows, *, include_active=True, mutate_latest=False):
    source_sha = "a" * 64
    timestamp = "2026-11-20T12:00:00+00:00"
    latest_rows = [dict(row) for row in rows]
    if mutate_latest:
        latest_rows[-1]["numbers"] = [4, 11, 20, 29, 38, 47]
    latest = target_queue._make_item(
        latest_rows,
        source_archive_sha256=source_sha,
        enqueued_at_utc=timestamp,
    )
    active = target_queue._make_item(
        rows,
        source_archive_sha256=source_sha,
        enqueued_at_utc=timestamp,
    ) if include_active and not mutate_latest else None
    state = autonomous.initial_state()
    state["target_queue"] = {
        "schema_version": 2,
        "observed_through_date": rows[-1]["date"],
        "active": active,
        "latest_snapshot": latest,
        "completed": [],
    }
    return state


class ProductionMigrationRehearsalTests(unittest.TestCase):
    def test_schema_mode_accepts_legacy_and_current_pairs_only(self):
        self.assertEqual(
            rehearsal.rehearsal_mode({"schema_version": 5}, {"schema_version": 1}),
            "MIGRATE_LEGACY",
        )
        self.assertEqual(
            rehearsal.rehearsal_mode({"schema_version": 6}, {"schema_version": 2}),
            "VALIDATE_CURRENT",
        )
        with self.assertRaisesRegex(RuntimeError, "unsupported"):
            rehearsal.rehearsal_mode({"schema_version": 5}, {"schema_version": 2})

    def test_already_migrated_snapshot_validates_without_requiring_original_active_date(self):
        rows = make_rows()
        state = current_state(rows)
        result = rehearsal.validate_current_schema_snapshot(state, rows)
        self.assertEqual(result["active_target_date"], rows[-1]["date"])
        self.assertEqual(result["latest_snapshot_date"], rows[-1]["date"])
        self.assertEqual(state["schema_version"], 6)
        self.assertEqual(state["target_queue"]["schema_version"], 2)

    def test_already_migrated_idle_queue_is_valid_when_latest_snapshot_matches(self):
        rows = make_rows()
        state = current_state(rows, include_active=False)
        result = rehearsal.validate_current_schema_snapshot(state, rows)
        self.assertIsNone(result["active_target_date"])
        self.assertEqual(result["latest_snapshot_date"], rows[-1]["date"])

    def test_already_migrated_latest_snapshot_must_match_canonical_archive(self):
        rows = make_rows()
        state = current_state(rows, include_active=False, mutate_latest=True)
        with self.assertRaises(target_queue.TargetQueueError):
            rehearsal.validate_current_schema_snapshot(state, rows)


if __name__ == "__main__":
    unittest.main()
