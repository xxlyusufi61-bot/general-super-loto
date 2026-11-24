import copy
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import general_autonomous_research as auto
import general_sealed_evaluation as sealed
import general_target_admission as admission
import general_target_queue as queue


NOW = datetime(2026, 11, 24, 16, 0, tzinfo=timezone.utc)


def synthetic_rows(count, start="2020-08-02"):
    first = date.fromisoformat(start)
    rows = []
    for index in range(count):
        day = (first + timedelta(days=2 * index)).isoformat()
        base = (index * 7) % 60 + 1
        numbers = sorted({((base + offset * 11 - 1) % 60) + 1 for offset in range(6)})
        if len(numbers) != 6:
            raise AssertionError("synthetic 6-of-60 fixture is invalid")
        rows.append({"date": day, "numbers": numbers})
    return rows


def append_draws(rows, count):
    first = date.fromisoformat(rows[-1]["date"]) + timedelta(days=2)
    return rows + synthetic_rows(count, start=first.isoformat())


def write_archive(path, rows):
    path.write_text(
        json.dumps({"draws": rows}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def write_empty_ledger(path):
    path.write_text(json.dumps({
        "schema_version": 1,
        "purpose": "test append-only sealed ledger",
        "entries": [],
    }), encoding="utf-8")


def ready_preflight(archive_path, ledger_path, *, offline_memory_path):
    archive = sealed.load_archive(archive_path)
    history = archive["rows"][:-1]
    return {
        "status": "READY",
        "partition_version": sealed.PARTITION_VERSION,
        "directional_validation_windows_disjoint": True,
        "target_date": archive["last_date"],
        "archive_sha256": archive["archive_sha256"],
        "archive_record_count": archive["record_count"],
        "target_numbers_exposed": False,
        "target_in_discovery": False,
        "target_in_validation": False,
        "target_in_reverse_validation": False,
        "validation_dates_previously_consumed": False,
        "forward_validation_dates": [history[-2]["date"], history[-1]["date"]],
        "reverse_validation_dates": [history[0]["date"], history[1]["date"]],
        "regimes": {"R60": {"status": "READY", "discovery_count": 40}},
    }


class TargetAdmissionMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive = self.root / "archive.json"
        self.ledger = self.root / "ledger.json"
        self.memory = self.root / "memory.json"
        self.memory.write_text("{}", encoding="utf-8")
        write_empty_ledger(self.ledger)

    def tearDown(self):
        self.temp.cleanup()

    def test_untouched_ineligible_active_target_is_closed_and_unready_latest_stays_staged(self):
        rows = synthetic_rows(90)
        write_archive(self.archive, rows)
        initial_queue, _ = queue.capture_new_targets(
            queue.initial_queue(),
            archive_path=self.archive,
            attempts=[],
            events=[],
            now=NOW,
        )
        state = auto.initial_state()
        state["target_queue"] = initial_queue
        state_before_attempts = copy.deepcopy(state["attempts"])
        state_before_events = copy.deepcopy(state["events"])

        newer_rows = append_draws(rows, 2)
        write_archive(self.archive, newer_rows)
        insufficient = sealed.SealedEvaluationError(
            "R60 has no sufficiently sized fresh forward sealed validation interval; "
            "preserve the minimum history and wait for more unconsumed draws."
        )
        with patch.object(admission.sealed, "preflight", side_effect=insufficient):
            updated, report = admission.maintain_targets(
                state,
                archive_path=self.archive,
                ledger_path=self.ledger,
                memory_path=self.memory,
                workspace=self.root,
                now=NOW + timedelta(minutes=1),
            )

        self.assertEqual(report["status"], "WAITING_FOR_FRESH_SEALED_WINDOWS")
        self.assertFalse(report["active_available"])
        self.assertEqual(report["retired_target_date"], rows[-1]["date"])
        self.assertIsNone(updated["target_queue"]["active"])
        self.assertEqual(
            updated["target_queue"]["completed"][-1]["outcome"],
            "NOT_EVALUABLE_INSUFFICIENT_FRESH_WINDOW",
        )
        self.assertEqual(
            updated["target_queue"]["completed"][-1]["archive_sha256"],
            initial_queue["active"]["archive_sha256"],
        )
        self.assertEqual(updated["target_queue"]["latest_snapshot"]["target_date"], newer_rows[-1]["date"])
        self.assertEqual(updated["attempts"], state_before_attempts)
        self.assertEqual(updated["events"], state_before_events)
        self.assertFalse(report["target_values_rendered"])
        self.assertFalse(report["hosted_model_request_performed"])
        self.assertNotIn("numbers", report)

    def test_latest_snapshot_is_admitted_only_after_ready_preflight(self):
        rows = synthetic_rows(90)
        write_archive(self.archive, rows)
        staged, summary = queue.capture_new_targets(
            queue.initial_queue(),
            archive_path=self.archive,
            attempts=[],
            events=[],
            now=NOW,
            admit_latest=False,
        )
        self.assertTrue(summary["has_latest_candidate"])
        self.assertIsNone(staged["active"])
        state = auto.initial_state()
        state["target_queue"] = staged

        with patch.object(admission.sealed, "preflight", side_effect=ready_preflight):
            updated, report = admission.maintain_targets(
                state,
                archive_path=self.archive,
                ledger_path=self.ledger,
                memory_path=self.memory,
                workspace=self.root,
                now=NOW + timedelta(minutes=1),
            )

        self.assertEqual(report["status"], "LATEST_TARGET_ADMITTED")
        self.assertTrue(report["active_available"])
        self.assertEqual(
            updated["target_queue"]["active"]["target_date"],
            rows[-1]["date"],
        )
        self.assertEqual(updated["attempts"], [])
        self.assertEqual(updated["events"], [])
        self.assertFalse(report["target_values_rendered"])
        self.assertFalse(report["hosted_model_request_performed"])

    def test_active_target_with_any_attempt_is_not_auto_terminalized(self):
        rows = synthetic_rows(90)
        write_archive(self.archive, rows)
        staged, _ = queue.capture_new_targets(
            queue.initial_queue(),
            archive_path=self.archive,
            attempts=[],
            events=[],
            now=NOW,
        )
        state = auto.initial_state()
        state["target_queue"] = staged
        active = staged["active"]
        state["attempts"].append({
            "attempt_id": "a" * 64,
            "target_date": active["target_date"],
            "archive_sha256": active["archive_sha256"],
            "archive_record_count": active["archive_record_count"],
            "claimed_at_utc": NOW.isoformat(),
            "github_run_id": "445600",
        })
        auto.validate_state(state)

        insufficient = sealed.SealedEvaluationError(
            "R60 has no sufficiently sized fresh forward sealed validation interval; "
            "preserve the minimum history and wait for more unconsumed draws."
        )
        with patch.object(admission.sealed, "preflight", side_effect=insufficient):
            updated, report = admission.maintain_targets(
                state,
                archive_path=self.archive,
                ledger_path=self.ledger,
                memory_path=self.memory,
                workspace=self.root,
                now=NOW + timedelta(minutes=1),
            )

        self.assertEqual(report["status"], "ACTIVE_TARGET_REQUIRES_RECOVERY_OR_REVIEW")
        self.assertFalse(report["active_available"])
        self.assertEqual(updated["target_queue"]["active"]["target_date"], active["target_date"])
        self.assertEqual(updated["target_queue"]["completed"], [])
        self.assertEqual(len(updated["attempts"]), 1)

    def test_unrecognized_preflight_failure_fails_closed_without_mutating_input(self):
        rows = synthetic_rows(90)
        write_archive(self.archive, rows)
        staged, _ = queue.capture_new_targets(
            queue.initial_queue(),
            archive_path=self.archive,
            attempts=[],
            events=[],
            now=NOW,
        )
        state = auto.initial_state()
        state["target_queue"] = staged
        before = copy.deepcopy(state)
        failure = sealed.SealedEvaluationError("Offline development memory changed during sealed preflight.")
        with patch.object(admission.sealed, "preflight", side_effect=failure):
            with self.assertRaisesRegex(admission.TargetAdmissionError, "not an approved"):
                admission.maintain_targets(
                    state,
                    archive_path=self.archive,
                    ledger_path=self.ledger,
                    memory_path=self.memory,
                    workspace=self.root,
                    now=NOW + timedelta(minutes=1),
                )
        self.assertEqual(state, before)


if __name__ == "__main__":
    unittest.main()
