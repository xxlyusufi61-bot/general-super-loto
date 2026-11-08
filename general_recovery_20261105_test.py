import hashlib
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import general_recovery_20261105 as recovery
import general_autonomous_research as autonomous
import general_open_research as research
import general_sealed_evaluation as sealed


def synthetic_rows():
    first = date(2026, 5, 11)
    rows = []
    for index in range(90):
        day = (first + timedelta(days=index * 2)).isoformat()
        base = (index * 7) % 60 + 1
        numbers = sorted({((base + offset * 11 - 1) % 60) + 1 for offset in range(6)})
        if len(numbers) != 6:
            raise AssertionError("invalid synthetic 6-of-60 row")
        rows.append({"date": day, "numbers": numbers})
    if rows[-1]["date"] != recovery.RECOVERY_TARGET_DATE:
        raise AssertionError("synthetic archive does not end on the fixed target")
    return rows


def archive_bytes(rows):
    return (
        json.dumps(
            {"source": "test", "draws": rows},
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n"
    ).encode("utf-8")


def active_lock_and_ledger(root):
    lock = root / "lock.json"
    lock.write_text(
        json.dumps({"schema_version": 1, "locked": False, "reason": "", "locked_at": None,
                    "locked_by": None, "last_lock_reason": "", "last_unlocked_at": None}),
        encoding="utf-8",
    )
    ledger = root / "ledger.json"
    ledger.write_text(
        json.dumps({"schema_version": 1, "purpose": "test append-only ledger", "entries": []}),
        encoding="utf-8",
    )
    return lock, ledger


class RecoveryArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = synthetic_rows()
        self.source_raw = archive_bytes(self.rows)
        self.current_rows = self.rows + [{
            "date": "2026-11-08",
            "numbers": [2, 12, 22, 32, 42, 52],
        }]
        self.current_raw = archive_bytes(self.current_rows)
        self.source = self.root / "pinned.json"
        self.current = self.root / "current.json"
        self.output = self.root / "recovery.json"
        self.metadata = self.root / "metadata.json"
        self.lock, self.ledger = active_lock_and_ledger(self.root)
        self.autonomous_state = self.root / "autonomous-state.json"
        self._write_scheduler_state()
        self.source.write_bytes(self.source_raw)
        self.current.write_bytes(self.current_raw)

    def _write_scheduler_state(self, archive_sha=None, record_count=None, later_target=False, include_event=True):
        state = autonomous.initial_state()
        attempt = {
            "attempt_id": recovery.PREVIOUS_FAILED_ATTEMPT_ID,
            "target_date": recovery.RECOVERY_TARGET_DATE,
            "archive_sha256": archive_sha or recovery.EXPECTED_ARCHIVE_SHA256,
            "archive_record_count": record_count if record_count is not None else recovery.EXPECTED_ARCHIVE_RECORDS,
            "claimed_at_utc": "2026-11-06T01:33:01.662567+00:00",
            "github_run_id": recovery.PREVIOUS_FAILED_RUN_ID,
        }
        state["attempts"].append(attempt)
        if include_event:
            state["events"].append({
                "attempt_id": recovery.PREVIOUS_FAILED_ATTEMPT_ID,
                "outcome": "FAILED",
                "recorded_at_utc": "2026-11-06T01:37:11.337681+00:00",
                "reason_code": "WORKFLOW_FAILED",
            })
        if later_target:
            state["attempts"].append({
                "attempt_id": "f" * 64,
                "target_date": "2026-11-08",
                "archive_sha256": "e" * 64,
                "archive_record_count": recovery.EXPECTED_ARCHIVE_RECORDS + 1,
                "claimed_at_utc": "2026-11-08T01:00:00+00:00",
                "github_run_id": "42424242425",
            })
        autonomous.validate_state(state)
        self.autonomous_state.write_text(json.dumps(state), encoding="utf-8")

    def _run_prepare(self):
        self._write_scheduler_state(
            archive_sha=hashlib.sha256(self.source_raw).hexdigest(),
            record_count=len(self.rows),
        )
        with (
            patch.object(recovery, "RECOVERY_SOURCE_COMMIT", "d" * 40),
            patch.object(recovery, "EXPECTED_ARCHIVE_SHA256", hashlib.sha256(self.source_raw).hexdigest()),
            patch.object(recovery, "EXPECTED_ARCHIVE_BYTES", len(self.source_raw)),
            patch.object(recovery, "EXPECTED_ARCHIVE_RECORDS", len(self.rows)),
            patch.object(recovery, "EXPECTED_FIRST_DATE", self.rows[0]["date"]),
        ):
            return recovery.prepare_recovery_archive(
                source_archive_path=self.source,
                current_archive_path=self.current,
                ledger_path=self.ledger,
                autonomous_state_path=self.autonomous_state,
                output_path=self.output,
                source_commit="d" * 40,
                metadata_path=self.metadata,
            )

    def test_prepare_pins_original_target_and_allows_later_rows_only_in_current_archive(self):
        result = self._run_prepare()
        self.assertEqual(result["status"], "RECOVERY_ARCHIVE_READY")
        self.assertEqual(result["target_date"], "2026-11-05")
        self.assertEqual(result["archive_sha256"], hashlib.sha256(self.source_raw).hexdigest())
        self.assertEqual(result["archive_record_count"], 90)
        self.assertEqual(result["current_archive_record_count"], 91)
        self.assertGreaterEqual(result["forward_validation_count"], sealed.MIN_VALIDATION_DRAWS)
        self.assertGreaterEqual(result["reverse_validation_count"], sealed.MIN_VALIDATION_DRAWS)
        self.assertTrue(result["forward_reverse_holdouts_disjoint"])
        self.assertFalse(result["target_numbers_exposed"])
        self.assertEqual(self.output.read_bytes(), self.source_raw)
        saved_metadata = json.loads(self.metadata.read_text(encoding="utf-8"))
        self.assertNotIn('"numbers":', json.dumps(saved_metadata))

    def test_rejects_wrong_source_commit(self):
        with self.assertRaisesRegex(recovery.RecoveryError, "source commit"):
            recovery.prepare_recovery_archive(
                source_archive_path=self.source,
                current_archive_path=self.current,
                ledger_path=self.ledger,
                autonomous_state_path=self.autonomous_state,
                output_path=self.output,
                source_commit="e" * 40,
            )

    def test_rejects_archive_bytes_that_do_not_match_original_attempt(self):
        self.source.write_bytes(self.source_raw + b" ")
        with patch.object(recovery, "RECOVERY_SOURCE_COMMIT", "d" * 40):
            with self.assertRaisesRegex(recovery.RecoveryError, "SHA-256"):
                recovery.prepare_recovery_archive(
                    source_archive_path=self.source,
                    current_archive_path=self.current,
                    ledger_path=self.ledger,
                    autonomous_state_path=self.autonomous_state,
                    output_path=self.output,
                    source_commit="d" * 40,
                )

    def test_rejects_changed_historical_prefix_in_current_archive(self):
        changed = [dict(row) for row in self.current_rows]
        changed[20] = {"date": changed[20]["date"], "numbers": [1, 2, 3, 4, 5, 6]}
        self.current.write_bytes(archive_bytes(changed))
        self._write_scheduler_state(
            archive_sha=hashlib.sha256(self.source_raw).hexdigest(),
            record_count=len(self.rows),
        )
        with patch.object(recovery, "RECOVERY_SOURCE_COMMIT", "d" * 40):
            with patch.object(recovery, "EXPECTED_ARCHIVE_SHA256", hashlib.sha256(self.source_raw).hexdigest()):
                with patch.object(recovery, "EXPECTED_ARCHIVE_BYTES", len(self.source_raw)):
                    with patch.object(recovery, "EXPECTED_ARCHIVE_RECORDS", len(self.rows)):
                        with patch.object(recovery, "EXPECTED_FIRST_DATE", self.rows[0]["date"]):
                            with self.assertRaisesRegex(recovery.RecoveryError, "history through 2026-11-05"):
                                recovery.prepare_recovery_archive(
                                    source_archive_path=self.source,
                                    current_archive_path=self.current,
                                    ledger_path=self.ledger,
                                    autonomous_state_path=self.autonomous_state,
                                    output_path=self.output,
                                    source_commit="d" * 40,
                                )

    def test_rejects_wrong_target_date_even_when_hash_is_patched(self):
        wrong_rows = [dict(row) for row in self.rows]
        wrong_rows[-1] = {"date": "2026-11-04", "numbers": wrong_rows[-1]["numbers"]}
        raw = archive_bytes(wrong_rows)
        self.source.write_bytes(raw)
        self._write_scheduler_state(
            archive_sha=hashlib.sha256(raw).hexdigest(),
            record_count=len(wrong_rows),
        )
        with (
            patch.object(recovery, "RECOVERY_SOURCE_COMMIT", "d" * 40),
            patch.object(recovery, "EXPECTED_ARCHIVE_SHA256", hashlib.sha256(raw).hexdigest()),
            patch.object(recovery, "EXPECTED_ARCHIVE_BYTES", len(raw)),
            patch.object(recovery, "EXPECTED_ARCHIVE_RECORDS", len(wrong_rows)),
            patch.object(recovery, "EXPECTED_FIRST_DATE", wrong_rows[0]["date"]),
        ):
            with self.assertRaisesRegex(recovery.RecoveryError, "exactly the original"):
                recovery.prepare_recovery_archive(
                    source_archive_path=self.source,
                    current_archive_path=self.current,
                    ledger_path=self.ledger,
                    autonomous_state_path=self.autonomous_state,
                    output_path=self.output,
                    source_commit="d" * 40,
                )

    def test_rejects_missing_original_failure_event_and_later_scheduler_target(self):
        self._write_scheduler_state(include_event=False)
        with self.assertRaisesRegex(recovery.RecoveryError, "expected failed cycle"):
            recovery.prepare_recovery_archive(
                source_archive_path=self.source,
                current_archive_path=self.current,
                ledger_path=self.ledger,
                autonomous_state_path=self.autonomous_state,
                output_path=self.output,
                source_commit=recovery.RECOVERY_SOURCE_COMMIT,
            )

        self._write_scheduler_state(later_target=True)
        with self.assertRaisesRegex(recovery.RecoveryError, "advanced beyond"):
            recovery.prepare_recovery_archive(
                source_archive_path=self.source,
                current_archive_path=self.current,
                ledger_path=self.ledger,
                autonomous_state_path=self.autonomous_state,
                output_path=self.output,
                source_commit=recovery.RECOVERY_SOURCE_COMMIT,
            )

    def test_model_discovery_messages_exclude_fixed_target_and_later_draws(self):
        self.output.write_bytes(self.source_raw)
        target, discovery, plan = research.isolate_discovery_only(
            self.rows,
            ledger_path=self.ledger,
        )
        self.assertEqual(target, recovery.RECOVERY_TARGET_DATE)
        messages = research.build_messages(target, discovery, direction="forward")
        payload = json.loads(messages[1]["content"])
        dates = [
            row["date"]
            for regime_rows in payload["discovery_history_by_regime"].values()
            for row in regime_rows
        ]
        self.assertNotIn(recovery.RECOVERY_TARGET_DATE, dates)
        self.assertTrue(all(day < recovery.RECOVERY_TARGET_DATE for day in dates))
        self.assertNotIn(json.dumps(self.rows[-1]["numbers"]), messages[1]["content"])
        self.assertNotIn(json.dumps(self.current_rows[-1]["numbers"]), messages[1]["content"])
        self.assertFalse(payload["target_numbers_included"])
        self.assertFalse(payload["post_target_records_included"])
        self.assertFalse(plan["target_numbers_exposed"])


class OneShotAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.run_id = "42424242424"
        self.head_sha = "a" * 40
        self.run = {
            "id": int(self.run_id),
            "run_attempt": 1,
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": self.head_sha,
            "path": recovery.RECOVERY_WORKFLOW_PATH,
            "status": "in_progress",
            "actor": {
                "login": recovery.ALLOWED_ACTOR_LOGIN,
                "id": recovery.ALLOWED_ACTOR_ID,
            },
        }
        self.history = {"total_count": 1, "workflow_runs": [self.run]}

    def _authorize(self, **overrides):
        values = {
            "repository": recovery.PROJECT_REPOSITORY,
            "run_id": self.run_id,
            "run_attempt": "1",
            "event": "workflow_dispatch",
            "ref": "refs/heads/main",
            "head_sha": self.head_sha,
            "confirmation": recovery.RECOVERY_CONFIRMATION_PHRASE,
            "token": "test-token",
            "request_json": lambda _url, _token: self.history,
        }
        values.update(overrides)
        return recovery.verify_one_shot_dispatch(**values)

    def test_accepts_only_the_first_authorized_manual_main_run(self):
        result = self._authorize()
        self.assertEqual(result["status"], "ONE_TIME_RECOVERY_DISPATCH_AUTHORIZED")
        self.assertEqual(result["maximum_new_provider_requests"], 2)
        self.assertEqual(result["maximum_total_target_provider_requests"], 3)
        self.assertEqual(result["previous_provider_request_count"], 1)

    def test_rejects_any_second_dispatch_or_rerun(self):
        self.history = {"total_count": 2, "workflow_runs": [self.run, dict(self.run, id=42424242425)]}
        with self.assertRaisesRegex(recovery.RecoveryError, "previous or duplicate"):
            self._authorize()
        with self.assertRaisesRegex(recovery.RecoveryError, "reruns are forbidden"):
            self._authorize(run_attempt="2")

    def test_rejects_wrong_actor_event_branch_commit_or_phrase(self):
        with self.assertRaisesRegex(recovery.RecoveryError, "canonical GENERAL repository"):
            self._authorize(repository="someone/else")
        with self.assertRaisesRegex(recovery.RecoveryError, "manual dispatch from main"):
            self._authorize(event="schedule")
        with self.assertRaisesRegex(recovery.RecoveryError, "manual dispatch from main"):
            self._authorize(ref="refs/heads/feature")
        with self.assertRaisesRegex(recovery.RecoveryError, "exact one-time"):
            self._authorize(confirmation="NO")
        with self.assertRaisesRegex(recovery.RecoveryError, "fixed one-shot"):
            self._authorize(head_sha="b" * 40)
        bad_actor = dict(self.run, actor={"login": "someone-else", "id": 7})
        self.history = {"total_count": 1, "workflow_runs": [bad_actor]}
        with self.assertRaisesRegex(recovery.RecoveryError, "fixed one-shot"):
            self._authorize()

    def test_rejects_incomplete_or_unavailable_github_run_history(self):
        self.history = {"total_count": 0, "workflow_runs": []}
        with self.assertRaisesRegex(recovery.RecoveryError, "previous or duplicate"):
            self._authorize()
        with self.assertRaisesRegex(recovery.RecoveryError, "token"):
            self._authorize(token="")


if __name__ == "__main__":
    unittest.main()
