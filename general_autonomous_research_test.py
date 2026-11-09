import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import general_autonomous_research as auto
from general_nvidia_guard import initial_lock_state, make_locked_state


AUTO_PHRASE = auto.AUTO_CONFIRMATION_PHRASE
NOW = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)


def active_state(*, days=7, lock=None):
    return auto.activate_state(
        auto.initial_state(),
        confirmation=AUTO_PHRASE,
        free_usage_verified=True,
        authorization_days=days,
        global_lock=lock or initial_lock_state(),
        now=NOW,
    )


def synthetic_rows(count, start="2021-01-01"):
    first = date.fromisoformat(start)
    rows = []
    for index in range(count):
        day = (first + timedelta(days=2 * index)).isoformat()
        base = (index * 7) % 60 + 1
        numbers = sorted({((base + offset * 11 - 1) % 60) + 1 for offset in range(6)})
        if len(numbers) != 6:
            raise AssertionError("synthetic fixture did not produce six distinct values")
        rows.append({"date": day, "numbers": numbers})
    return rows


class AutonomousResearchStateTests(unittest.TestCase):
    def test_initial_state_is_disabled_and_has_no_attempts(self):
        state = auto.initial_state()
        self.assertFalse(auto.authorization_status(state, now=NOW)["authorized"])
        self.assertEqual(state["attempts"], [])
        self.assertEqual(state["events"], [])

    def test_activation_requires_exact_phrase_free_verification_and_clear_lock(self):
        with self.assertRaisesRegex(auto.AutonomousResearchError, "Exact autonomous"):
            auto.activate_state(
                auto.initial_state(), confirmation="YES", free_usage_verified=True,
                authorization_days=7, global_lock=initial_lock_state(), now=NOW,
            )
        with self.assertRaisesRegex(auto.AutonomousResearchError, "independently verified"):
            auto.activate_state(
                auto.initial_state(), confirmation=AUTO_PHRASE, free_usage_verified=False,
                authorization_days=7, global_lock=initial_lock_state(), now=NOW,
            )
        locked = make_locked_state(initial_lock_state(), "test lock", stamp=NOW.isoformat())
        with self.assertRaisesRegex(auto.AutonomousResearchError, "separate manual unlock"):
            auto.activate_state(
                auto.initial_state(), confirmation=AUTO_PHRASE, free_usage_verified=True,
                authorization_days=7, global_lock=locked, now=NOW,
            )

    def test_activation_is_expiring_and_target_request_cap_is_removed(self):
        state = active_state(days=7)
        auth = state["authorization"]
        self.assertTrue(auto.authorization_status(state, now=NOW)["authorized"])
        self.assertIsNone(auth["max_requests_per_target"])
        self.assertEqual(
            auth["expires_at_utc"],
            (NOW + timedelta(days=7)).isoformat(),
        )
        after_expiry = NOW + timedelta(days=7, seconds=1)
        self.assertFalse(auto.authorization_status(state, now=after_expiry)["authorized"])

    def test_pause_is_immediate_and_preserves_attempt_history(self):
        state = active_state()
        state["attempts"].append({
            "attempt_id": "a" * 64,
            "target_date": "2026-10-29",
            "archive_sha256": "b" * 64,
            "archive_record_count": 978,
            "claimed_at_utc": NOW.isoformat(),
            "github_run_id": "12345",
        })
        auto.validate_state(state)
        paused = auto.pause_state(state, now=NOW)
        self.assertFalse(auto.authorization_status(paused, now=NOW)["authorized"])
        self.assertEqual(len(paused["attempts"]), 1)

    def test_state_rejects_number_sets_and_duplicate_or_unsorted_attempt_dates(self):
        state = auto.initial_state()
        state["numbers"] = [1, 2, 3, 4, 5, 6]
        with self.assertRaises(auto.AutonomousResearchError):
            auto.validate_state(state)

        state = auto.initial_state()
        for day in ("2026-10-29", "2026-10-28"):
            state["attempts"].append({
                "attempt_id": (day.replace("-", "") + "0" * 56)[:64],
                "target_date": day,
                "archive_sha256": "c" * 64,
                "archive_record_count": 978,
                "claimed_at_utc": NOW.isoformat(),
                "github_run_id": None,
            })
        with self.assertRaises(auto.AutonomousResearchError):
            auto.validate_state(state)

    def test_disabled_scheduler_skips_without_loading_archive_or_calling_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = auto.prepare_cycle(
                auto.initial_state(),
                archive_path=root / "does-not-exist.json",
                ledger_path=root / "does-not-exist-ledger.json",
                global_lock_path=root / "does-not-exist-lock.json",
                now=NOW,
            )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["status"], "AUTOMATION_NOT_AUTHORIZED")

    def test_prepare_returns_only_number_free_ready_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            lock = root / "lock.json"
            archive.write_text(
                json.dumps({"draws": synthetic_rows(90)}),
                encoding="utf-8",
            )
            ledger.write_text(
                json.dumps({
                    "schema_version": 1,
                    "purpose": "test append-only ledger",
                    "entries": [],
                }),
                encoding="utf-8",
            )
            lock.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
            result = auto.prepare_cycle(
                active_state(),
                archive_path=archive,
                ledger_path=ledger,
                global_lock_path=lock,
                now=NOW,
            )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["archive_record_count"], 90)
        self.assertGreaterEqual(result["discovery_count"], 40)
        self.assertGreaterEqual(result["forward_validation_count"], 2)
        self.assertGreaterEqual(result["reverse_validation_count"], 2)
        self.assertIs(result["target_numbers_exposed"], False)
        self.assertNotIn("numbers", result)
        self.assertNotIn("forward_validation_dates", result)

    def test_insufficient_fresh_sealed_windows_return_a_wait_state(self):
        messages = (
            "No sufficiently sized forward R60 validation interval is available.",
            "No sufficiently sized reverse R60 validation interval is available.",
            "R60 discovery history is shorter than the default minimum training window.",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            lock = root / "lock.json"
            archive.write_text(json.dumps({"draws": synthetic_rows(90)}), encoding="utf-8")
            ledger.write_text(
                json.dumps({"schema_version": 1, "purpose": "test", "entries": []}),
                encoding="utf-8",
            )
            lock.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
            for message in messages:
                with self.subTest(message=message), patch.object(
                    auto.sealed,
                    "preflight",
                    side_effect=auto.sealed.SealedEvaluationError(message),
                ):
                    result = auto.prepare_cycle(
                        active_state(),
                        archive_path=archive,
                        ledger_path=ledger,
                        global_lock_path=lock,
                        now=NOW,
                    )
                    self.assertFalse(result["eligible"])
                    self.assertEqual(result["status"], "WAITING_FOR_FRESH_SEALED_WINDOWS")

    def test_active_global_lock_blocks_prepare_before_archive_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lock = make_locked_state(initial_lock_state(), "manual safety pause", stamp=NOW.isoformat())
            lock_path = root / "lock.json"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            result = auto.prepare_cycle(
                active_state(),
                archive_path=root / "missing-archive.json",
                ledger_path=root / "missing-ledger.json",
                global_lock_path=lock_path,
                now=NOW,
            )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["status"], "GLOBAL_NVIDIA_LOCK_ACTIVE")

    def test_claim_is_once_per_target_and_outcome_is_terminal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            lock = root / "lock.json"
            archive.write_text(json.dumps({"draws": synthetic_rows(90)}), encoding="utf-8")
            ledger.write_text(
                json.dumps({"schema_version": 1, "purpose": "test", "entries": []}),
                encoding="utf-8",
            )
            lock.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
            state = active_state()
            plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW,
            )
            attempt = auto.claim_attempt(
                state, plan, github_run_id="445566", now=NOW,
            )
            self.assertEqual(attempt["target_date"], plan["target_date"])
            with self.assertRaisesRegex(auto.AutonomousResearchError, "retry requires proof"):
                auto.claim_attempt(state, plan, github_run_id="445567", now=NOW)
            skipped = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW,
            )
            self.assertEqual(skipped["status"], "TARGET_ATTEMPT_IN_PROGRESS")
            self.assertFalse(skipped["eligible"])

            finished = auto.record_outcome(
                state, attempt["attempt_id"], outcome="SUCCEEDED",
                reason_code="EVALUATION_COMPLETED", now=NOW,
            )
            self.assertEqual(len(finished["events"]), 1)
            with self.assertRaisesRegex(auto.AutonomousResearchError, "terminal outcome"):
                auto.record_outcome(
                    state, attempt["attempt_id"], outcome="FAILED",
                    reason_code="WORKFLOW_FAILED", now=NOW,
                )

    def test_only_proven_no_send_failures_allow_bounded_same_target_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            lock = root / "lock.json"
            archive.write_text(json.dumps({"draws": synthetic_rows(90)}), encoding="utf-8")
            ledger.write_text(
                json.dumps({"schema_version": 1, "purpose": "test", "entries": []}),
                encoding="utf-8",
            )
            lock.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
            state = active_state()
            plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW,
            )
            first = auto.claim_attempt(state, plan, github_run_id="420000001", now=NOW)
            auto.record_outcome(
                state, first["attempt_id"], outcome="FAILED",
                reason_code="NO_PROVIDER_REQUEST_SENT", now=NOW,
            )

            retry_plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(minutes=1),
            )
            self.assertTrue(retry_plan["eligible"])
            self.assertTrue(retry_plan["retryable"])
            self.assertEqual(retry_plan["retry_number"], 2)
            second = auto.claim_attempt(
                state, retry_plan, github_run_id="420000002", now=NOW + timedelta(minutes=1),
            )
            self.assertNotEqual(first["attempt_id"], second["attempt_id"])
            auto.record_outcome(
                state, second["attempt_id"], outcome="FAILED",
                reason_code="NO_PROVIDER_REQUEST_SENT", now=NOW + timedelta(minutes=1),
            )

            third_plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(minutes=2),
            )
            self.assertTrue(third_plan["eligible"])
            third = auto.claim_attempt(
                state, third_plan, github_run_id="420000003", now=NOW + timedelta(minutes=2),
            )
            auto.record_outcome(
                state, third["attempt_id"], outcome="FAILED",
                reason_code="NO_PROVIDER_REQUEST_SENT", now=NOW + timedelta(minutes=2),
            )
            blocked = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(minutes=3),
            )
            self.assertFalse(blocked["eligible"])
            self.assertEqual(blocked["status"], "TARGET_RETRY_LIMIT_REACHED")

    def test_provider_delivery_uncertainty_never_allows_target_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            lock = root / "lock.json"
            archive.write_text(json.dumps({"draws": synthetic_rows(90)}), encoding="utf-8")
            ledger.write_text(
                json.dumps({"schema_version": 1, "purpose": "test", "entries": []}),
                encoding="utf-8",
            )
            lock.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
            state = active_state()
            plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW,
            )
            attempt = auto.claim_attempt(state, plan, github_run_id="420000004", now=NOW)
            auto.record_outcome(
                state, attempt["attempt_id"], outcome="FAILED",
                reason_code="PROVIDER_DELIVERY_UNCERTAIN", now=NOW,
            )
            blocked = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(minutes=1),
            )
            self.assertFalse(blocked["eligible"])
            self.assertEqual(blocked["status"], "NO_NEW_TARGET_DRAW")

    def test_new_target_cannot_skip_a_previous_proven_no_send_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            lock = root / "lock.json"
            rows = synthetic_rows(90)
            archive.write_text(json.dumps({"draws": rows}), encoding="utf-8")
            ledger.write_text(
                json.dumps({"schema_version": 1, "purpose": "test", "entries": []}),
                encoding="utf-8",
            )
            lock.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
            state = active_state()
            plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW,
            )
            attempt = auto.claim_attempt(state, plan, github_run_id="420000005", now=NOW)
            auto.record_outcome(
                state, attempt["attempt_id"], outcome="FAILED",
                reason_code="NO_PROVIDER_REQUEST_SENT", now=NOW,
            )
            rows.append({
                "date": (date.fromisoformat(rows[-1]["date"]) + timedelta(days=2)).isoformat(),
                "numbers": [1, 2, 3, 4, 5, 6],
            })
            archive.write_text(json.dumps({"draws": rows}), encoding="utf-8")
            blocked = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(days=1),
            )
            self.assertFalse(blocked["eligible"])
            self.assertEqual(blocked["status"], "PREVIOUS_TARGET_RETRY_REQUIRED")

    def test_target_date_regression_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            lock = root / "lock.json"
            rows = synthetic_rows(90)
            archive.write_text(json.dumps({"draws": rows}), encoding="utf-8")
            ledger.write_text(
                json.dumps({"schema_version": 1, "purpose": "test", "entries": []}),
                encoding="utf-8",
            )
            lock.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
            state = active_state()
            plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW,
            )
            auto.claim_attempt(state, plan, github_run_id="1", now=NOW)
            archive.write_text(
                json.dumps({"draws": rows[:-1]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(auto.AutonomousResearchError, "moved backwards"):
                auto.prepare_cycle(
                    state, archive_path=archive, ledger_path=ledger,
                    global_lock_path=lock, now=NOW,
                )

    def test_bad_global_lock_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad-lock.json"
            path.write_text('{"locked": false}', encoding="utf-8")
            with self.assertRaisesRegex(auto.AutonomousResearchError, "failed validation"):
                auto.read_global_lock(path)


if __name__ == "__main__":
    unittest.main()
