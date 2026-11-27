import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import general_autonomous_research as auto
import general_research_memory as research_memory
import general_research_windows as research_windows
from general_nvidia_guard import initial_lock_state, make_locked_state, make_unlocked_state
from general_sealed_evaluation_test import write_candidate_documents


def append_draws(rows, count):
    next_date = (date.fromisoformat(rows[-1]["date"]) + timedelta(days=2)).isoformat()
    return rows + synthetic_rows(count, start=next_date)


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


def claim_legacy_attempt(state, plan, *, github_run_id, now=None):
    """Exercise V1/V2 scheduler-history compatibility without a V3 package binding."""
    attempt = auto.claim_attempt(
        state, plan, github_run_id=github_run_id, now=now
    )
    attempt.pop("research_package_id", None)
    attempt.pop("research_package_attempt_id", None)
    auto.validate_state(state)
    return attempt


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


def _write_evaluated_test_ledger(path, attempt):
    payload = {
        "schema_version": 1,
        "purpose": "test",
        "entries": [{
            "claim_id": "f" * 64,
            "state": "EVALUATED",
            "target_date": attempt["target_date"],
            "archive_sha256": attempt["archive_sha256"],
            "archive_record_count": attempt["archive_record_count"],
            "validation_dates": [(
                date.fromisoformat(attempt["target_date"]) - timedelta(days=4)
            ).isoformat()],
            "reverse_validation_dates": [(
                date.fromisoformat(attempt["target_date"]) - timedelta(days=2)
            ).isoformat()],
        }],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class AutonomousResearchStateTests(unittest.TestCase):
    def test_v3_evaluated_package_retry_is_limited_to_local_score_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            ledger_path = root / "ledger.json"
            lock = root / "lock.json"
            state_path = root / "general_autonomous_research_state.json"
            memory_path = root / "general_research_memory.json"
            rows = synthetic_rows(120)
            archive.write_text(json.dumps({"draws": rows}), encoding="utf-8")
            ledger_path.write_text(
                json.dumps({"schema_version": 1, "purpose": "test", "entries": []}),
                encoding="utf-8",
            )
            lock.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
            research_memory._write_memory(research_memory.empty_memory(), memory_path)
            actual_now = datetime.now(timezone.utc).replace(microsecond=0)
            source_archive = auto.sealed.load_archive(archive)
            active_item = auto.target_queue._make_item(
                rows,
                source_archive_sha256=source_archive["archive_sha256"],
                enqueued_at_utc=actual_now.isoformat(),
            )
            active_queue = {
                "schema_version": 2,
                "observed_through_date": rows[-1]["date"],
                "active": active_item,
                "latest_snapshot": active_item,
                "completed": [],
            }
            auto.target_queue.materialize_front(active_queue, archive)
            state = auto.activate_state(
                auto.initial_state(),
                confirmation=AUTO_PHRASE,
                free_usage_verified=True,
                authorization_days=7,
                global_lock=initial_lock_state(),
                now=actual_now,
            )
            state["target_queue"] = active_queue
            auto.write_state(state_path, state)

            previous_cwd = Path.cwd()
            try:
                os.chdir(root)
                args = [
                    "claim",
                    "--state", str(state_path),
                    "--archive", str(archive),
                    "--ledger", str(ledger_path),
                    "--global-lock", str(lock),
                    "--run-id", "420000100",
                ]
                self.assertEqual(auto.main(args), 0)

                first_state = auto.read_state(state_path)
                first_attempt = first_state["attempts"][-1]
                self.assertIsInstance(first_attempt.get("research_package_id"), str)
                self.assertEqual(
                    first_attempt.get("research_package_attempt_id"),
                    first_attempt["attempt_id"],
                )

                ledger = auto.sealed.load_ledger(ledger_path)
                exposure = research_windows.package_exposure_for_attempt(
                    ledger,
                    package_id=first_attempt["research_package_id"],
                    attempt_id=first_attempt["research_package_attempt_id"],
                )
                self.assertIsNotNone(exposure)
                package = research_windows.package_from_exposure(exposure)
                proposals, specs = write_candidate_documents(root)
                claim = auto.sealed.create_claim(
                    archive,
                    ledger_path,
                    proposals,
                    specs,
                    snapshot_directory=root / "snapshots",
                    research_package_id=package["package_id"],
                    scheduler_attempt_id=first_attempt["research_package_attempt_id"],
                )
                self.assertTrue(claim["claim_created"])
                auto.sealed.evaluate_claim(
                    claim["claim_id"],
                    ledger_path,
                    proposals,
                    specs,
                    output_path=root / "sealed-evaluation.json",
                )
                evaluated_ledger = auto.sealed.load_ledger(ledger_path)
                evaluated_exposure = research_windows.package_exposure_for_attempt(
                    evaluated_ledger,
                    package_id=package["package_id"],
                    attempt_id=first_attempt["research_package_attempt_id"],
                )
                self.assertEqual(
                    research_windows.package_exposure_status(evaluated_ledger, evaluated_exposure),
                    "EVALUATED",
                )

                failed_state = auto.record_outcome(
                    first_state,
                    first_attempt["attempt_id"],
                    outcome="FAILED",
                    reason_code="SEALED_EVALUATION_INCOMPLETE",
                    evaluation_ledger_path=ledger_path,
                    now=actual_now + timedelta(minutes=2),
                )
                auto.write_state(state_path, failed_state)

                with self.assertRaises(auto.sealed.SealedEvaluationError):
                    auto.sealed.preflight(
                        archive,
                        ledger_path,
                        partition_version=auto.sealed.UNSEEN_PARTITION_VERSION,
                        package_id=package["package_id"],
                        attempt_id=first_attempt["research_package_attempt_id"],
                    )

                self.assertEqual(auto.main(args), 0)

                retried_state = auto.read_state(state_path)
                self.assertEqual(len(retried_state["attempts"]), 2)
                retry_attempt = retried_state["attempts"][-1]
                self.assertNotEqual(retry_attempt["attempt_id"], first_attempt["attempt_id"])
                self.assertEqual(retry_attempt["research_package_id"], package["package_id"])
                self.assertEqual(
                    retry_attempt["research_package_attempt_id"],
                    first_attempt["research_package_attempt_id"],
                )
                final_ledger = auto.sealed.load_ledger(ledger_path)
                final_exposure = research_windows.package_exposure_for_attempt(
                    final_ledger,
                    package_id=package["package_id"],
                    attempt_id=first_attempt["research_package_attempt_id"],
                )
                self.assertEqual(
                    research_windows.package_exposure_status(final_ledger, final_exposure),
                    "EVALUATED",
                )
                self.assertEqual(len(final_ledger["entries"]), 1)
                self.assertEqual(final_ledger["entries"][0]["claim_id"], claim["claim_id"])
                self.assertEqual(final_ledger["entries"][0]["state"], "EVALUATED")
            finally:
                os.chdir(previous_cwd)

    def test_v3_no_provider_request_releases_identity_and_next_preflight_remints_it(self):
        import general_research_windows as windows

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            ledger_path = root / "ledger.json"
            lock = root / "lock.json"
            archive.write_text(json.dumps({"draws": synthetic_rows(90)}), encoding="utf-8")
            ledger_path.write_text(
                json.dumps({"schema_version": 1, "purpose": "test", "entries": []}),
                encoding="utf-8",
            )
            lock.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
            state = active_state()
            plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger_path,
                global_lock_path=lock, now=NOW,
            )
            self.assertTrue(plan["eligible"], plan)
            package = plan["research_package"]
            attempt = auto.claim_attempt(
                state, plan, github_run_id="420000100", now=NOW,
            )
            ledger = auto.sealed.load_ledger(ledger_path)
            exposure_attempt_id = attempt.get(
                "research_package_attempt_id", attempt["attempt_id"]
            )
            exposure = windows.reserve_research_package(
                ledger,
                package,
                attempt_id=exposure_attempt_id,
                created_at_utc=attempt["claimed_at_utc"],
                unavailable=set(package.get("previously_unavailable_dates", [])),
            )
            ledger_path.write_text(
                json.dumps(ledger, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            auto.record_outcome(
                state, attempt["attempt_id"], outcome="FAILED",
                reason_code="NO_PROVIDER_REQUEST_SENT",
                evaluation_ledger_path=ledger_path, now=NOW,
            )
            persisted = auto.sealed.load_ledger(ledger_path)
            persisted_exposure = windows.package_exposure_for_attempt(
                persisted,
                package_id=package["package_id"],
                attempt_id=exposure_attempt_id,
            )
            self.assertIsNotNone(persisted_exposure)
            self.assertEqual(
                windows.package_exposure_status(persisted, persisted_exposure),
                "RELEASED",
            )

            retry_plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger_path,
                global_lock_path=lock, now=NOW + timedelta(minutes=1),
            )
            self.assertTrue(retry_plan["eligible"], retry_plan)
            self.assertTrue(retry_plan["retryable"])
            self.assertNotEqual(
                retry_plan["research_package_id"], package["package_id"]
            )

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

    def test_adjacency_preflight_failure_does_not_reserve_a_target_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            lock = root / "lock.json"
            archive.write_text(json.dumps({"draws": synthetic_rows(90)}), encoding="utf-8")
            ledger.write_text(json.dumps({
                "schema_version": 1, "purpose": "test append-only ledger", "entries": []
            }), encoding="utf-8")
            lock.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
            state = active_state()
            attempts_before = json.loads(json.dumps(state["attempts"]))
            failure = auto.sealed.SealedEvaluationError(
                "No adjacency-safe bidirectional partition can preserve 40 discovery rows, "
                "two independent validation targets per direction, and both exact canonical "
                "neighbours without reusing consumed dates."
            )
            with patch.object(auto.sealed, "preflight", side_effect=failure):
                plan = auto.prepare_cycle(
                    state, archive_path=archive, ledger_path=ledger,
                    global_lock_path=lock, now=NOW,
                )
            self.assertFalse(plan["eligible"])
            self.assertEqual(plan["status"], "WAITING_FOR_FRESH_SEALED_WINDOWS")
            self.assertEqual(state["attempts"], attempts_before)
            with self.assertRaisesRegex(
                auto.AutonomousResearchError, "Only a ready sealed cycle can be claimed"
            ):
                claim_legacy_attempt(state, plan, github_run_id="445583", now=NOW)
            self.assertEqual(state["attempts"], attempts_before)

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
            attempt = claim_legacy_attempt(
                state, plan, github_run_id="445566", now=NOW,
            )
            self.assertEqual(attempt["target_date"], plan["target_date"])
            with self.assertRaisesRegex(auto.AutonomousResearchError, "retryable failure"):
                claim_legacy_attempt(state, plan, github_run_id="445567", now=NOW)
            skipped = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW,
            )
            self.assertEqual(skipped["status"], "TARGET_ATTEMPT_IN_PROGRESS")
            self.assertFalse(skipped["eligible"])

            _write_evaluated_test_ledger(ledger, attempt)
            finished = auto.record_outcome(
                state, attempt["attempt_id"], outcome="SUCCEEDED",
                reason_code="EVALUATION_COMPLETED",
                evaluation_ledger_path=ledger, now=NOW,
            )
            self.assertEqual(len(finished["events"]), 1)
            with self.assertRaisesRegex(auto.AutonomousResearchError, "terminal outcome"):
                auto.record_outcome(
                    state, attempt["attempt_id"], outcome="FAILED",
                    reason_code="WORKFLOW_FAILED", now=NOW,
                )

    def test_proven_no_send_failures_allow_same_target_retries_with_ten_attempt_cap(self):
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
            previous = claim_legacy_attempt(state, plan, github_run_id="420000001", now=NOW)
            auto.record_outcome(
                state, previous["attempt_id"], outcome="FAILED",
                reason_code="NO_PROVIDER_REQUEST_SENT", now=NOW,
            )

            for number in range(2, auto.MAX_ATTEMPTS_PER_TARGET + 1):
                at = NOW + timedelta(minutes=number - 1)
                retry_plan = auto.prepare_cycle(
                    state, archive_path=archive, ledger_path=ledger,
                    global_lock_path=lock, now=at,
                )
                self.assertTrue(retry_plan["eligible"])
                self.assertTrue(retry_plan["retryable"])
                self.assertEqual(retry_plan["retry_number"], number)
                current = claim_legacy_attempt(
                    state, retry_plan, github_run_id=str(420000000 + number), now=at,
                )
                self.assertNotEqual(previous["attempt_id"], current["attempt_id"])
                auto.record_outcome(
                    state, current["attempt_id"], outcome="FAILED",
                    reason_code="NO_PROVIDER_REQUEST_SENT",
                    now=at,
                )
                previous = current

            self.assertEqual(len(state["attempts"]), auto.MAX_ATTEMPTS_PER_TARGET)
            auto.validate_state(state)
            blocked = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(minutes=auto.MAX_ATTEMPTS_PER_TARGET),
            )
            self.assertFalse(blocked["eligible"])
            self.assertEqual(blocked["status"], "TARGET_RETRY_LIMIT_REACHED")

            last = state["attempts"][-1]
            eleventh_at = NOW + timedelta(minutes=auto.MAX_ATTEMPTS_PER_TARGET + 1)
            state["attempts"].append({
                "attempt_id": "f" * 64,
                "target_date": last["target_date"],
                "archive_sha256": last["archive_sha256"],
                "archive_record_count": last["archive_record_count"],
                "claimed_at_utc": eleventh_at.isoformat(),
                "github_run_id": "420000011",
            })
            state["events"].append({
                "attempt_id": "f" * 64,
                "outcome": "FAILED",
                "recorded_at_utc": (eleventh_at + timedelta(seconds=1)).isoformat(),
                "reason_code": "NO_PROVIDER_REQUEST_SENT",
            })
            with self.assertRaisesRegex(auto.AutonomousResearchError, "ten-failed-attempt limit"):
                auto.validate_state(state)

    def _safe_local_target_report(self, attempt):
        return {
            "status": "LOCAL_TARGET_EVALUATION_COMPLETED",
            "claim_id": "9" * 64,
            "attempt_id": attempt["attempt_id"],
            "target_date": attempt["target_date"],
            "archive_sha256": attempt["archive_sha256"],
            "archive_record_count": attempt["archive_record_count"],
            "forward_hypothesis_id": "HYP-901",
            "forward_spec_sha256": "8" * 64,
            "execution_spec_dsl_version": 2,
            "candidate_count": 6,
            "hit_count": 2,
            "uniform_random_expected_hits_out_of_6": 0.6,
            "evaluation_scope": "ONE_CURRENT_HIDDEN_TARGET",
            "prior_draws_used_only_as_conditioning_context": True,
            "prior_holdouts_reused_as_evaluation_targets": False,
            "prior_draws_reused_for_discovery_or_tuning": False,
            "target_numbers_exposed": False,
            "target_numbers_reported": False,
            "candidate_numbers_exposed": False,
            "candidate_numbers_reported": False,
            "predicted_number_values_reported": False,
            "per_number_matches_reported": False,
            "target_values_sent_to_model": False,
            "target_score_sent_to_model": False,
            "target_score_saved_to_research_memory": False,
            "reverse_spec_used_for_prediction": False,
            "live_hosted_model_request_made": False,
            "telegram_request_made": False,
            "method_registry_modified": False,
            "completed_at_utc": NOW.isoformat(),
        }

    def test_ten_successful_research_packages_do_not_consume_failed_attempt_budget(self):
        state = active_state()
        for index in range(12):
            attempt_id = format(index + 1, "x") * 64
            claimed = NOW + timedelta(minutes=index * 2)
            state["attempts"].append({
                "attempt_id": attempt_id,
                "target_date": "2026-10-29",
                "archive_sha256": "a" * 64,
                "archive_record_count": 978,
                "claimed_at_utc": claimed.isoformat(),
                "github_run_id": str(8400 + index),
            })
            state["events"].append({
                "attempt_id": attempt_id,
                "outcome": "SUCCEEDED",
                "recorded_at_utc": (claimed + timedelta(minutes=1)).isoformat(),
                "reason_code": "EVALUATION_COMPLETED",
            })
        auto.validate_state(state)
        self.assertEqual(
            sum(1 for event in state["events"] if event["outcome"] == "FAILED"),
            0,
        )

    def test_retry_limit_is_enforced_after_ten_failures_even_if_last_package_succeeded(self):
        rows = synthetic_rows(90)
        item = auto.target_queue._make_item(
            rows, source_archive_sha256="4" * 64,
            enqueued_at_utc=NOW.isoformat(),
        )
        state = active_state()
        state["target_queue"] = {
            "schema_version": 2,
            "observed_through_date": rows[-1]["date"],
            "active": item,
            "latest_snapshot": item,
            "completed": [],
        }
        for index in range(auto.MAX_ATTEMPTS_PER_TARGET + 1):
            attempt_id = format(index + 1, "x") * 64
            claimed = NOW + timedelta(minutes=index * 2)
            state["attempts"].append({
                "attempt_id": attempt_id,
                "target_date": item["target_date"],
                "archive_sha256": item["archive_sha256"],
                "archive_record_count": item["archive_record_count"],
                "claimed_at_utc": claimed.isoformat(),
                "github_run_id": str(8500 + index),
            })
            outcome = "FAILED" if index < auto.MAX_ATTEMPTS_PER_TARGET else "SUCCEEDED"
            state["events"].append({
                "attempt_id": attempt_id,
                "outcome": outcome,
                "recorded_at_utc": (claimed + timedelta(minutes=1)).isoformat(),
                "reason_code": (
                    "NO_PROVIDER_REQUEST_SENT" if outcome == "FAILED"
                    else "EVALUATION_COMPLETED"
                ),
            })
        auto.validate_state(state)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            lock = root / "lock.json"
            archive.write_bytes(auto.target_queue._snapshot_bytes(rows))
            ledger.write_text(
                json.dumps({"schema_version": 1, "purpose": "test", "entries": []}),
                encoding="utf-8",
            )
            lock.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
            result = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(hours=1),
            )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["status"], "TARGET_RETRY_LIMIT_REACHED")

    def test_private_local_target_result_is_bound_and_idempotently_preserved(self):
        rows = synthetic_rows(90)
        item = auto.target_queue._make_item(
            rows, source_archive_sha256="8" * 64,
            enqueued_at_utc=NOW.isoformat(),
        )
        state = active_state()
        state["target_queue"] = {
            "schema_version": 2,
            "observed_through_date": rows[-1]["date"],
            "active": item,
            "latest_snapshot": item,
            "completed": [],
        }
        attempt = {
            "attempt_id": "7" * 64,
            "target_date": item["target_date"],
            "archive_sha256": item["archive_sha256"],
            "archive_record_count": item["archive_record_count"],
            "claimed_at_utc": NOW.isoformat(),
            "github_run_id": "99123",
        }
        state["attempts"].append(attempt)
        report = self._safe_local_target_report(attempt)
        updated = auto.record_target_evaluation(state, report)
        self.assertEqual(len(updated["target_evaluations"]), 1)
        self.assertNotIn("hit_count", updated["target_evaluations"][0])
        self.assertNotIn("numbers", updated["target_evaluations"][0])
        self.assertNotIn("candidate_numbers", updated["target_evaluations"][0])
        again = auto.record_target_evaluation(updated, report)
        self.assertEqual(len(again["target_evaluations"]), 1)

    def test_private_target_result_can_be_recovered_under_later_attempt_without_duplicate_entry(self):
        rows = synthetic_rows(90)
        item = auto.target_queue._make_item(
            rows, source_archive_sha256="3" * 64,
            enqueued_at_utc=NOW.isoformat(),
        )
        state = active_state()
        state["target_queue"] = {
            "schema_version": 2,
            "observed_through_date": rows[-1]["date"],
            "active": item,
            "latest_snapshot": item,
            "completed": [],
        }
        first = {
            "attempt_id": "2" * 64,
            "target_date": item["target_date"],
            "archive_sha256": item["archive_sha256"],
            "archive_record_count": item["archive_record_count"],
            "claimed_at_utc": NOW.isoformat(),
            "github_run_id": "99125",
        }
        state["attempts"].append(first)
        report = self._safe_local_target_report(first)
        saved = auto.record_target_evaluation(state, report)
        # The prior evaluated package is terminal before a later attempt is
        # represented. This keeps the retry history internally consistent.
        saved["events"].append({
            "attempt_id": first["attempt_id"],
            "outcome": "SUCCEEDED",
            "recorded_at_utc": NOW.isoformat(),
            "reason_code": "EVALUATION_COMPLETED",
        })

        second = dict(first)
        second["attempt_id"] = "1" * 64
        second["claimed_at_utc"] = (NOW + timedelta(minutes=1)).isoformat()
        second["github_run_id"] = "99126"
        saved["attempts"].append(second)
        replay = self._safe_local_target_report(second)
        replay["completed_at_utc"] = (NOW + timedelta(minutes=1)).isoformat()
        recovered = auto.record_target_evaluation(saved, replay)
        self.assertEqual(len(recovered["target_evaluations"]), 1)
        self.assertEqual(recovered["target_evaluations"][0]["attempt_id"], first["attempt_id"])

        forged_replay = self._safe_local_target_report(second)
        forged_replay["attempt_id"] = "f" * 64
        with self.assertRaisesRegex(auto.AutonomousResearchError, "does not match its claimed attempt"):
            auto.record_target_evaluation(recovered, forged_replay)

    def test_private_local_target_result_rejects_target_data_and_wrong_attempt(self):
        rows = synthetic_rows(90)
        item = auto.target_queue._make_item(
            rows, source_archive_sha256="6" * 64,
            enqueued_at_utc=NOW.isoformat(),
        )
        state = active_state()
        state["target_queue"] = {
            "schema_version": 2,
            "observed_through_date": rows[-1]["date"],
            "active": item,
            "latest_snapshot": item,
            "completed": [],
        }
        attempt = {
            "attempt_id": "5" * 64,
            "target_date": item["target_date"],
            "archive_sha256": item["archive_sha256"],
            "archive_record_count": item["archive_record_count"],
            "claimed_at_utc": NOW.isoformat(),
            "github_run_id": "99124",
        }
        state["attempts"].append(attempt)
        report = self._safe_local_target_report(attempt)
        report["target_numbers_exposed"] = True
        with self.assertRaisesRegex(auto.AutonomousResearchError, "safety flags"):
            auto.record_target_evaluation(state, report)
        report = self._safe_local_target_report(attempt)
        report["attempt_id"] = "4" * 64
        with self.assertRaisesRegex(auto.AutonomousResearchError, "does not match"):
            auto.record_target_evaluation(state, report)

    def test_successful_target_cannot_be_recorded_without_exact_evaluated_ledger(self):
        state = active_state()
        attempt_id = "e" * 64
        state["attempts"].append({
            "attempt_id": attempt_id,
            "target_date": "2026-11-01",
            "archive_sha256": "a" * 64,
            "archive_record_count": 90,
            "claimed_at_utc": NOW.isoformat(),
            "github_run_id": "98765",
        })
        before = json.loads(json.dumps(state))
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.json"
            ledger.write_text(
                json.dumps({"schema_version": 1, "purpose": "test", "entries": []}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                auto.AutonomousResearchError,
                "matching sealed claim persisted as EVALUATED",
            ):
                auto.record_outcome(
                    state, attempt_id, outcome="SUCCEEDED",
                    reason_code="EVALUATION_COMPLETED",
                    evaluation_ledger_path=ledger, now=NOW + timedelta(minutes=1),
                )
        self.assertEqual(state, before)


    def test_workflow_failed_retries_only_after_a_later_manual_unlock(self):
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
            first_attempt = claim_legacy_attempt(
                state, plan, github_run_id="420000012", now=NOW,
            )
            auto.record_outcome(
                state, first_attempt["attempt_id"], outcome="FAILED",
                reason_code="WORKFLOW_FAILED", now=NOW,
            )

            blocked = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(minutes=1),
            )
            self.assertFalse(blocked["eligible"])
            self.assertEqual(blocked["status"], "WORKFLOW_FAILED_MANUAL_RECOVERY_REQUIRED")

            stale_unlock = NOW - timedelta(minutes=1)
            lock.write_text(
                json.dumps(make_unlocked_state(initial_lock_state(), stamp=stale_unlock.isoformat())),
                encoding="utf-8",
            )
            stale_blocked = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(minutes=2),
            )
            self.assertFalse(stale_blocked["eligible"])
            self.assertEqual(stale_blocked["status"], "WORKFLOW_FAILED_MANUAL_RECOVERY_REQUIRED")

            later_unlock = NOW + timedelta(minutes=3)
            lock.write_text(
                json.dumps(make_unlocked_state(initial_lock_state(), stamp=later_unlock.isoformat())),
                encoding="utf-8",
            )
            retry_plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(minutes=4),
            )
            self.assertTrue(retry_plan["eligible"])
            self.assertTrue(retry_plan["retryable"])
            self.assertTrue(retry_plan["manual_recovery_confirmed"])
            self.assertEqual(retry_plan["retry_number"], 2)
            self.assertEqual(retry_plan["manual_recovery_unlock_at_utc"], later_unlock.isoformat())

            retry_attempt = claim_legacy_attempt(
                state, retry_plan, github_run_id="420000013",
                now=NOW + timedelta(minutes=4),
            )
            self.assertEqual(retry_attempt["target_date"], first_attempt["target_date"])
            self.assertNotEqual(retry_attempt["attempt_id"], first_attempt["attempt_id"])
            self.assertEqual(len(state["workflow_retry_history"]), 1)
            auto.validate_state(state)

            invalid = json.loads(json.dumps(state))
            invalid["workflow_retry_history"][0]["manual_unlocked_at_utc"] = NOW.isoformat()
            with self.assertRaisesRegex(auto.AutonomousResearchError, "Manual unlock must postdate"):
                auto.validate_state(invalid)

            missing_journal = json.loads(json.dumps(state))
            missing_journal["workflow_retry_history"] = []
            with self.assertRaisesRegex(auto.AutonomousResearchError, "Repeated target attempts"):
                auto.validate_state(missing_journal)


    def test_failed_target_with_pending_sealed_claim_retries_same_formula_and_holdouts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            lock = root / "lock.json"
            rows = synthetic_rows(90)
            archive.write_text(json.dumps({"draws": rows}), encoding="utf-8")
            lock.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
            state = active_state()
            first_plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW,
            )
            self.assertTrue(first_plan["eligible"])
            self.assertEqual(first_plan["status"], "READY")
            first_windows = auto.sealed.preflight(archive, ledger)
            first_forward = list(first_windows["forward_validation_dates"])
            first_reverse = list(first_windows["reverse_validation_dates"])

            attempt = claim_legacy_attempt(
                state, first_plan, github_run_id="420000007", now=NOW,
            )
            proposals, specs = write_candidate_documents(root)
            auto.sealed.create_claim(
                archive,
                ledger,
                proposals,
                specs,
                snapshot_directory=root / "snapshots",
            )
            auto.record_outcome(
                state, attempt["attempt_id"], outcome="FAILED",
                reason_code="SEALED_EVALUATION_INCOMPLETE", now=NOW,
            )

            retry_plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(minutes=1),
            )
            self.assertTrue(retry_plan["eligible"])
            self.assertTrue(retry_plan["retryable"])
            self.assertTrue(retry_plan["pending_claim_resume_available"])
            self.assertEqual(retry_plan["pending_claim_id"], auto.sealed.load_ledger(ledger)["entries"][0]["claim_id"])
            resumed_windows = auto.sealed.preflight(archive, ledger)
            self.assertEqual(resumed_windows["forward_validation_dates"], first_forward)
            self.assertEqual(resumed_windows["reverse_validation_dates"], first_reverse)
            retry_attempt = claim_legacy_attempt(
                state, retry_plan, github_run_id="420000010", now=NOW + timedelta(minutes=2),
            )
            self.assertEqual(retry_attempt["target_date"], attempt["target_date"])
            auto.validate_state(state)


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
            attempt = claim_legacy_attempt(state, plan, github_run_id="420000004", now=NOW)
            auto.record_outcome(
                state, attempt["attempt_id"], outcome="FAILED",
                reason_code="PROVIDER_DELIVERY_UNCERTAIN", now=NOW,
            )
            blocked = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(minutes=1),
            )
            self.assertFalse(blocked["eligible"])
            self.assertEqual(blocked["status"], "PROVIDER_DELIVERY_UNCERTAIN_MANUAL_RECOVERY_REQUIRED")

    def test_provider_delivery_uncertainty_retries_only_after_later_manual_unlock(self):
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
            first_plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW,
            )
            first_attempt = claim_legacy_attempt(
                state, first_plan, github_run_id="420000008", now=NOW,
            )
            auto.record_outcome(
                state, first_attempt["attempt_id"], outcome="FAILED",
                reason_code="PROVIDER_DELIVERY_UNCERTAIN", now=NOW,
            )

            blocked = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(minutes=1),
            )
            self.assertFalse(blocked["eligible"])
            self.assertEqual(blocked["status"], "PROVIDER_DELIVERY_UNCERTAIN_MANUAL_RECOVERY_REQUIRED")

            stale_unlock_time = NOW - timedelta(minutes=1)
            lock.write_text(
                json.dumps(make_unlocked_state(initial_lock_state(), stamp=stale_unlock_time.isoformat())),
                encoding="utf-8",
            )
            stale_unlock_plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(minutes=1, seconds=30),
            )
            self.assertFalse(stale_unlock_plan["eligible"])
            self.assertEqual(
                stale_unlock_plan["status"],
                "PROVIDER_DELIVERY_UNCERTAIN_MANUAL_RECOVERY_REQUIRED",
            )

            unlock_time = NOW + timedelta(minutes=2)
            lock.write_text(
                json.dumps(make_unlocked_state(initial_lock_state(), stamp=unlock_time.isoformat())),
                encoding="utf-8",
            )
            retry_plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(minutes=3),
            )
            self.assertTrue(retry_plan["eligible"])
            self.assertTrue(retry_plan["retryable"])
            self.assertTrue(retry_plan["manual_recovery_confirmed"])
            retry_attempt = claim_legacy_attempt(
                state, retry_plan, github_run_id="420000009",
                now=NOW + timedelta(minutes=3),
            )
            self.assertEqual(retry_attempt["target_date"], first_attempt["target_date"])
            self.assertNotEqual(retry_attempt["attempt_id"], first_attempt["attempt_id"])
            auto.validate_state(state)

    def test_http_402_429_rejection_is_not_retryable_even_after_manual_unlock(self):
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
            attempt = claim_legacy_attempt(
                state, plan, github_run_id="420000011", now=NOW,
            )
            auto.record_outcome(
                state, attempt["attempt_id"], outcome="FAILED",
                reason_code="PROVIDER_HTTP_REJECTION_NO_RETRY", now=NOW,
            )

            later_unlock = NOW + timedelta(minutes=1)
            lock.write_text(
                json.dumps(make_unlocked_state(initial_lock_state(), stamp=later_unlock.isoformat())),
                encoding="utf-8",
            )
            blocked = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(minutes=2),
            )
            self.assertFalse(blocked["eligible"])
            self.assertEqual(blocked["status"], "PROVIDER_HTTP_REJECTION_NO_RETRY")

    def test_explicit_http_5xx_and_incomplete_candidate_pair_retry_without_manual_unlock(self):
        for reason_code in (
            "PROVIDER_HTTP_5XX_RETRYABLE",
            "DIRECTIONAL_CANDIDATE_SET_INCOMPLETE",
            "SEALED_EVALUATION_INCOMPLETE",
        ):
            with self.subTest(reason_code=reason_code), tempfile.TemporaryDirectory() as directory:
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
                first_plan = auto.prepare_cycle(
                    state, archive_path=archive, ledger_path=ledger,
                    global_lock_path=lock, now=NOW,
                )
                first_attempt = claim_legacy_attempt(
                    state, first_plan, github_run_id="420000020", now=NOW,
                )
                auto.record_outcome(
                    state, first_attempt["attempt_id"], outcome="FAILED",
                    reason_code=reason_code, now=NOW,
                )

                retry_plan = auto.prepare_cycle(
                    state, archive_path=archive, ledger_path=ledger,
                    global_lock_path=lock, now=NOW + timedelta(minutes=1),
                )
                self.assertTrue(retry_plan["eligible"])
                self.assertTrue(retry_plan["retryable"])
                self.assertFalse(retry_plan["manual_recovery_confirmed"])
                retry_attempt = claim_legacy_attempt(
                    state, retry_plan, github_run_id="420000021",
                    now=NOW + timedelta(minutes=2),
                )
                self.assertEqual(retry_attempt["target_date"], first_attempt["target_date"])
                self.assertNotEqual(retry_attempt["attempt_id"], first_attempt["attempt_id"])
                auto.validate_state(state)


    def test_new_draw_is_staged_without_skipping_previous_failed_active_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            active_archive = root / "active-archive.json"
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
            attempt = claim_legacy_attempt(state, plan, github_run_id="420000005", now=NOW)
            auto.record_outcome(
                state, attempt["attempt_id"], outcome="FAILED",
                reason_code="NO_PROVIDER_REQUEST_SENT", now=NOW,
            )
            original_target = attempt["target_date"]
            rows.append({
                "date": (date.fromisoformat(rows[-1]["date"]) + timedelta(days=2)).isoformat(),
                "numbers": [1, 2, 3, 4, 5, 6],
            })
            archive.write_text(json.dumps({"draws": rows}), encoding="utf-8")
            updated_queue, capture = auto.target_queue.capture_new_targets(
                state["target_queue"], archive_path=archive,
                attempts=state["attempts"], events=state["events"],
                now=NOW + timedelta(days=1),
            )
            state["target_queue"] = updated_queue
            self.assertEqual(updated_queue["active"]["target_date"], original_target)
            self.assertEqual(updated_queue["latest_snapshot"]["target_date"], rows[-1]["date"])
            self.assertEqual(capture["pending_target_count"], 1)
            self.assertEqual(capture["intermediate_draws_promoted_as_targets"], False)

            auto.target_queue.materialize_front(updated_queue, active_archive)
            retry = auto.prepare_cycle(
                state, archive_path=active_archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(days=1),
            )
            self.assertTrue(retry["eligible"])
            self.assertTrue(retry["retryable"])
            self.assertEqual(retry["target_date"], original_target)

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
            claim_legacy_attempt(state, plan, github_run_id="1", now=NOW)
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




    def test_legacy_state_schema_is_upgraded_in_memory_without_losing_history(self):
        state = auto.initial_state()
        state.pop("target_queue")
        state.pop("recovery_history")
        state.pop("manual_retry_history")
        state.pop("workflow_retry_history")
        state.pop("target_evaluations")
        state["schema_version"] = 1
        state["attempts"].append({
            "attempt_id": "e" * 64,
            "target_date": "2026-10-29",
            "archive_sha256": "f" * 64,
            "archive_record_count": 978,
            "claimed_at_utc": NOW.isoformat(),
            "github_run_id": "98765",
        })
        upgraded = auto.validate_state(state)
        self.assertEqual(upgraded["schema_version"], 6)
        self.assertEqual(upgraded["workflow_retry_history"], [])
        self.assertEqual(upgraded["target_evaluations"], [])
        self.assertEqual(upgraded["target_queue"], auto.target_queue.initial_queue())
        self.assertEqual(upgraded["recovery_history"], [])
        self.assertEqual(upgraded["attempts"][0]["attempt_id"], "e" * 64)

    def test_prepare_requires_the_persisted_fifo_head_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            lock = root / "lock.json"
            rows = synthetic_rows(90)
            raw = auto.target_queue._snapshot_bytes(rows)
            archive.write_bytes(raw)
            ledger.write_text(
                json.dumps({"schema_version": 1, "purpose": "test", "entries": []}),
                encoding="utf-8",
            )
            lock.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
            state = active_state()
            item = auto.target_queue._make_item(
                rows, source_archive_sha256="a" * 64,
                enqueued_at_utc=NOW.isoformat(),
            )
            state["target_queue"] = {
                "schema_version": 1,
                "observed_through_date": rows[-1]["date"],
                "items": [item],
                "completed": [],
            }
            plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW,
            )
            self.assertTrue(plan["eligible"])
            self.assertEqual(plan["target_date"], rows[-1]["date"])

            later_rows = rows + synthetic_rows(
                1, start=(date.fromisoformat(rows[-1]["date"]) + timedelta(days=2)).isoformat()
            )
            archive.write_bytes(auto.target_queue._snapshot_bytes(later_rows))
            with self.assertRaisesRegex(auto.AutonomousResearchError, "immutable active-target snapshot"):
                auto.prepare_cycle(
                    state, archive_path=archive, ledger_path=ledger,
                    global_lock_path=lock, now=NOW,
                )

    def test_successful_package_keeps_active_target_until_a_new_draw_is_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            lock = root / "lock.json"
            rows = synthetic_rows(90)
            archive.write_bytes(auto.target_queue._snapshot_bytes(rows))
            ledger.write_text(
                json.dumps({"schema_version": 1, "purpose": "test", "entries": []}),
                encoding="utf-8",
            )
            lock.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
            state = active_state()
            item = auto.target_queue._make_item(
                rows, source_archive_sha256="b" * 64,
                enqueued_at_utc=NOW.isoformat(),
            )
            state["target_queue"] = {
                "schema_version": 1,
                "observed_through_date": rows[-1]["date"],
                "items": [item],
                "completed": [],
            }
            plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW,
            )
            attempt = claim_legacy_attempt(state, plan, github_run_id="98766", now=NOW)
            _write_evaluated_test_ledger(ledger, attempt)
            auto.record_outcome(
                state, attempt["attempt_id"], outcome="SUCCEEDED",
                reason_code="EVALUATION_COMPLETED",
                evaluation_ledger_path=ledger, now=NOW,
            )
            self.assertEqual(state["target_queue"]["items"][0]["target_date"], rows[-1]["date"])
            self.assertEqual(state["target_queue"]["completed"], [])

            # A later draw is staged independently. Only once the existing
            # package is terminal can the newest draw become the next target.
            later_rows = append_draws(rows, 2)
            archive.write_bytes(auto.target_queue._snapshot_bytes(later_rows))
            updated_queue, summary = auto.target_queue.capture_new_targets(
                state["target_queue"], archive_path=archive,
                attempts=state["attempts"], events=state["events"],
                now=NOW + timedelta(hours=1),
            )
            self.assertEqual(summary["pending_target_count"], 1)
            self.assertEqual(summary["intermediate_draws_promoted_as_targets"], False)
            self.assertEqual(updated_queue["active"]["target_date"], later_rows[-1]["date"])
            self.assertEqual(
                [item["target_date"] for item in updated_queue["completed"]],
                [rows[-1]["date"]],
            )

    def test_no_eligible_candidate_keeps_fifo_head_and_allows_same_target_retry(self):
        rows = synthetic_rows(90)
        item = auto.target_queue._make_item(
            rows, source_archive_sha256="d" * 64,
            enqueued_at_utc=NOW.isoformat(),
        )
        state = active_state()
        state["target_queue"] = {
            "schema_version": 1,
            "observed_through_date": rows[-1]["date"],
            "items": [item],
            "completed": [],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive.json"
            ledger = root / "ledger.json"
            lock = root / "lock.json"
            archive.write_text(
                json.dumps({"draws": rows}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            ledger.write_text(
                json.dumps({"schema_version": 1, "purpose": "test", "entries": []}),
                encoding="utf-8",
            )
            lock.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
            plan = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW,
            )
            first = claim_legacy_attempt(state, plan, github_run_id="420000091", now=NOW)
            auto.record_outcome(
                state, first["attempt_id"], outcome="NO_ELIGIBLE_CANDIDATE",
                reason_code="NO_FROZEN_DIRECTIONAL_CANDIDATE", now=NOW,
            )
            self.assertEqual(state["target_queue"]["items"][0]["target_date"], rows[-1]["date"])
            self.assertEqual(state["target_queue"]["completed"], [])
            auto.validate_state(state)

            retry = auto.prepare_cycle(
                state, archive_path=archive, ledger_path=ledger,
                global_lock_path=lock, now=NOW + timedelta(minutes=1),
            )
            self.assertTrue(retry["eligible"])
            self.assertTrue(retry["retryable"])
            self.assertEqual(retry["retry_number"], 2)
            second = claim_legacy_attempt(
                state, retry, github_run_id="420000092", now=NOW + timedelta(minutes=1),
            )
            self.assertNotEqual(first["attempt_id"], second["attempt_id"])
            self.assertEqual(state["target_queue"]["items"][0]["target_date"], rows[-1]["date"])


    def test_no_send_failure_keeps_fifo_head_for_safe_same_target_retry(self):
        rows = synthetic_rows(90)
        item = auto.target_queue._make_item(
            rows, source_archive_sha256="c" * 64,
            enqueued_at_utc=NOW.isoformat(),
        )
        state = active_state()
        state["target_queue"] = {
            "schema_version": 1,
            "observed_through_date": rows[-1]["date"],
            "items": [item],
            "completed": [],
        }
        state["attempts"].append({
            "attempt_id": "d" * 64,
            "target_date": rows[-1]["date"],
            "archive_sha256": item["archive_sha256"],
            "archive_record_count": len(rows),
            "claimed_at_utc": NOW.isoformat(),
            "github_run_id": "98767",
        })
        auto.record_outcome(
            state, "d" * 64, outcome="FAILED",
            reason_code="NO_PROVIDER_REQUEST_SENT", now=NOW,
        )
        self.assertEqual(len(state["target_queue"]["items"]), 1)
        self.assertEqual(state["target_queue"]["completed"], [])


    def _exhausted_target_fixture(self, *, omit_last_event=False):
        rows_target = [
            {"date": "2026-11-08", "numbers": [1, 2, 3, 4, 5, 6]},
            {"date": "2026-11-10", "numbers": [1, 2, 3, 4, 5, 7]},
        ]
        rows_next = rows_target + [
            {"date": "2026-11-12", "numbers": [1, 2, 3, 4, 5, 8]},
        ]
        item_target = auto.target_queue._make_item(
            rows_target, source_archive_sha256="a" * 64,
            enqueued_at_utc=NOW.isoformat(),
        )
        item_next = auto.target_queue._make_item(
            rows_next, source_archive_sha256="b" * 64,
            enqueued_at_utc=NOW.isoformat(),
        )
        state = auto.initial_state()
        state["target_queue"] = {
            "schema_version": 1,
            "observed_through_date": "2026-11-12",
            "items": [item_target, item_next],
            "completed": [],
        }
        for index in range(auto.MAX_ATTEMPTS_PER_TARGET):
            attempt_id = format(index + 1, "x") * 64
            claimed = NOW + timedelta(minutes=index * 10)
            state["attempts"].append({
                "attempt_id": attempt_id,
                "target_date": "2026-11-10",
                "archive_sha256": item_target["archive_sha256"],
                "archive_record_count": len(rows_target),
                "claimed_at_utc": claimed.isoformat(),
                "github_run_id": str(7000 + index),
            })
            if not (omit_last_event and index == auto.MAX_ATTEMPTS_PER_TARGET - 1):
                state["events"].append({
                    "attempt_id": attempt_id,
                    "outcome": "FAILED",
                    "recorded_at_utc": (claimed + timedelta(minutes=2)).isoformat(),
                    "reason_code": "PROVIDER_DELIVERY_UNCERTAIN",
                })
        return state, item_target, item_next

    def test_exhausted_retry_target_is_closed_without_rewriting_attempt_history(self):
        state, item_target, item_next = self._exhausted_target_fixture()
        auto.validate_state(state)
        attempts_before = json.loads(json.dumps(state["attempts"]))
        events_before = json.loads(json.dumps(state["events"]))

        with self.assertRaisesRegex(auto.AutonomousResearchError, "exactly ten failed attempts"):
            auto.retire_exhausted_target(
                state,
                target_date="2026-11-10",
                archive_sha256=item_target["archive_sha256"],
                archive_record_count=item_target["archive_record_count"],
                expected_attempt_count=auto.MAX_ATTEMPTS_PER_TARGET - 1,
                expected_next_target_date="2026-11-12",
                expected_next_archive_sha256=item_next["archive_sha256"],
                expected_next_archive_record_count=item_next["archive_record_count"],
                now=NOW,
            )

        updated, report = auto.retire_exhausted_target(
            state,
            target_date="2026-11-10",
            archive_sha256=item_target["archive_sha256"],
            archive_record_count=item_target["archive_record_count"],
            expected_attempt_count=auto.MAX_ATTEMPTS_PER_TARGET,
            expected_next_target_date="2026-11-12",
            expected_next_archive_sha256=item_next["archive_sha256"],
            expected_next_archive_record_count=item_next["archive_record_count"],
            now=NOW,
        )

        self.assertEqual(report["outcome"], "EXHAUSTED_RETRY_BUDGET")
        self.assertEqual(report["attempts_preserved_count"], auto.MAX_ATTEMPTS_PER_TARGET)
        self.assertEqual(report["next_target_date"], "2026-11-12")
        self.assertEqual(updated["target_queue"]["items"][0]["target_date"], "2026-11-12")
        self.assertEqual(updated["target_queue"]["completed"][0]["outcome"], "EXHAUSTED_RETRY_BUDGET")
        self.assertEqual(updated["attempts"], attempts_before)
        self.assertEqual(updated["events"], events_before)
        self.assertEqual(state["target_queue"]["items"][0]["target_date"], "2026-11-10")
        self.assertNotIn("numbers", report)
        self.assertIs(report["target_values_rendered"], False)

    def test_exhaustion_limit_counts_failed_attempts_not_total_attempts(self):
        state, item_target, item_next = self._exhausted_target_fixture()

        # A valid no-candidate package remains in the immutable history but
        # must not consume one of the ten failed-attempt slots.
        state["events"][4]["outcome"] = "NO_ELIGIBLE_CANDIDATE"
        state["events"][4]["reason_code"] = "NO_FROZEN_DIRECTIONAL_CANDIDATE"

        extra_attempt_id = "c" * 64
        extra_claimed_at = NOW + timedelta(minutes=100)
        state["attempts"].append({
            "attempt_id": extra_attempt_id,
            "target_date": item_target["target_date"],
            "archive_sha256": item_target["archive_sha256"],
            "archive_record_count": item_target["archive_record_count"],
            "claimed_at_utc": extra_claimed_at.isoformat(),
            "github_run_id": "7010",
        })
        state["events"].append({
            "attempt_id": extra_attempt_id,
            "outcome": "FAILED",
            "recorded_at_utc": (extra_claimed_at + timedelta(minutes=2)).isoformat(),
            "reason_code": "PROVIDER_DELIVERY_UNCERTAIN",
        })

        updated, report = auto.retire_exhausted_target(
            state,
            target_date=item_target["target_date"],
            archive_sha256=item_target["archive_sha256"],
            archive_record_count=item_target["archive_record_count"],
            expected_failed_attempt_count=auto.MAX_ATTEMPTS_PER_TARGET,
            expected_next_target_date=item_next["target_date"],
            expected_next_archive_sha256=item_next["archive_sha256"],
            expected_next_archive_record_count=item_next["archive_record_count"],
            now=NOW,
        )

        self.assertEqual(len(updated["attempts"]), 11)
        self.assertEqual(len(updated["events"]), 11)
        self.assertEqual(report["attempts_preserved_count"], 11)
        self.assertEqual(report["failed_attempt_count"], 10)
        self.assertEqual(
            updated["target_queue"]["items"][0]["target_date"],
            item_next["target_date"],
        )

    def test_v2_exhausted_active_target_can_be_retired_without_a_newer_draw(self):
        state, item_target, _ = self._exhausted_target_fixture()
        state["target_queue"] = {
            "schema_version": 2,
            "observed_through_date": item_target["target_date"],
            "active": json.loads(json.dumps(item_target)),
            "latest_snapshot": json.loads(json.dumps(item_target)),
            "completed": [],
        }

        updated, report = auto.retire_exhausted_target(
            state,
            target_date=item_target["target_date"],
            archive_sha256=item_target["archive_sha256"],
            archive_record_count=item_target["archive_record_count"],
            expected_failed_attempt_count=auto.MAX_ATTEMPTS_PER_TARGET,
            now=NOW,
        )

        self.assertIsNone(updated["target_queue"]["active"])
        self.assertEqual(
            updated["target_queue"]["latest_snapshot"]["target_date"],
            item_target["target_date"],
        )
        self.assertIsNone(report["next_target_date"])
        self.assertEqual(
            updated["target_queue"]["completed"][0]["outcome"],
            "EXHAUSTED_RETRY_BUDGET",
        )

    def test_v2_exhausted_target_requires_confirmation_of_staged_latest_successor(self):
        state, item_target, item_next = self._exhausted_target_fixture()
        state["target_queue"] = {
            "schema_version": 2,
            "observed_through_date": item_next["target_date"],
            "active": json.loads(json.dumps(item_target)),
            "latest_snapshot": json.loads(json.dumps(item_next)),
            "completed": [],
        }

        with self.assertRaisesRegex(
            auto.AutonomousResearchError,
            "must confirm the exact staged latest archive snapshot",
        ):
            auto.retire_exhausted_target(
                state,
                target_date=item_target["target_date"],
                archive_sha256=item_target["archive_sha256"],
                archive_record_count=item_target["archive_record_count"],
                expected_failed_attempt_count=auto.MAX_ATTEMPTS_PER_TARGET,
                now=NOW,
            )

    def test_exhausted_retry_target_refuses_missing_terminal_failure_record(self):
        state, item_target, item_next = self._exhausted_target_fixture(omit_last_event=True)
        with self.assertRaisesRegex(auto.AutonomousResearchError, "terminal FAILED"):
            auto.retire_exhausted_target(
                state,
                target_date="2026-11-10",
                archive_sha256=item_target["archive_sha256"],
                archive_record_count=item_target["archive_record_count"],
                expected_attempt_count=auto.MAX_ATTEMPTS_PER_TARGET,
                expected_next_target_date="2026-11-12",
                expected_next_archive_sha256=item_next["archive_sha256"],
                expected_next_archive_record_count=item_next["archive_record_count"],
                now=NOW,
            )
        self.assertEqual(state["target_queue"]["items"][0]["target_date"], "2026-11-10")



    def _retired_exhausted_state_for_prepare(self):
        state, item_target, item_next = self._exhausted_target_fixture()
        updated, _ = auto.retire_exhausted_target(
            state,
            target_date="2026-11-10",
            archive_sha256=item_target["archive_sha256"],
            archive_record_count=item_target["archive_record_count"],
            expected_attempt_count=auto.MAX_ATTEMPTS_PER_TARGET,
            expected_next_target_date="2026-11-12",
            expected_next_archive_sha256=item_next["archive_sha256"],
            expected_next_archive_record_count=item_next["archive_record_count"],
            now=NOW,
        )
        updated["authorization"] = active_state()["authorization"]
        auto.validate_state(updated)
        return updated, item_next

    def _prepare_next_fifo_target(self, state, item_next, root):
        lock_path = root / "lock.json"
        lock_path.write_text(json.dumps(initial_lock_state()), encoding="utf-8")
        validation_count = max(2, auto.sealed.MIN_VALIDATION_DRAWS)
        forward_dates = [
            (date(2026, 9, 1) + timedelta(days=index)).isoformat()
            for index in range(validation_count)
        ]
        reverse_dates = [
            (date(2026, 10, 1) + timedelta(days=index)).isoformat()
            for index in range(validation_count)
        ]
        discovery_count = max(40, auto.sealed.DEFAULT_MIN_TRAIN_DRAWS)
        archive = {
            "last_date": "2026-11-12",
            "archive_sha256": item_next["archive_sha256"],
            "record_count": item_next["archive_record_count"],
        }
        plan = {
            "target_date": "2026-11-12",
            "archive_sha256": item_next["archive_sha256"],
            "target_numbers_exposed": False,
            "target_in_discovery": False,
            "target_in_validation": False,
            "regimes": {
                "R60": {
                    "status": "READY",
                    "discovery_count": discovery_count,
                },
            },
            "forward_validation_dates": forward_dates,
            "reverse_validation_dates": reverse_dates,
            "pre_target_draw_count": discovery_count + 2 * validation_count,
        }
        with patch.object(auto.sealed, "load_archive", return_value=archive), patch.object(
            auto.sealed, "preflight", return_value=plan
        ):
            return auto.prepare_cycle(
                state,
                archive_path=root / "archive.json",
                ledger_path=root / "ledger.json",
                global_lock_path=lock_path,
                now=NOW,
            )

    def test_prepare_advances_after_exact_completed_exhausted_fifo_target(self):
        state, item_next = self._retired_exhausted_state_for_prepare()
        attempts_before = json.loads(json.dumps(state["attempts"]))
        events_before = json.loads(json.dumps(state["events"]))

        with tempfile.TemporaryDirectory() as directory:
            result = self._prepare_next_fifo_target(state, item_next, Path(directory))

        self.assertTrue(result["eligible"])
        self.assertEqual(result["status"], "READY")
        self.assertEqual(result["target_date"], "2026-11-12")
        self.assertIs(result["target_numbers_exposed"], False)
        self.assertFalse(result["retryable"])
        self.assertEqual(result["retry_number"], 1)
        self.assertEqual(state["attempts"], attempts_before)
        self.assertEqual(state["events"], events_before)

    def test_prepare_fails_closed_when_completed_predecessor_archive_identity_differs(self):
        state, item_next = self._retired_exhausted_state_for_prepare()
        state["target_queue"]["completed"][0]["archive_sha256"] = "f" * 64

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                auto.AutonomousResearchError,
                "Completed previous FIFO target does not match its archived attempt identity",
            ):
                self._prepare_next_fifo_target(state, item_next, Path(directory))

    def test_prepare_does_not_skip_an_uncompleted_previous_target(self):
        state, _, item_next = self._exhausted_target_fixture()
        state["authorization"] = active_state()["authorization"]
        state["target_queue"]["items"] = [item_next]
        auto.validate_state(state)

        with tempfile.TemporaryDirectory() as directory:
            result = self._prepare_next_fifo_target(state, item_next, Path(directory))

        self.assertFalse(result["eligible"])
        self.assertEqual(result["status"], "PREVIOUS_TARGET_MANUAL_RECOVERY_REQUIRED")
        self.assertEqual(result["pending_target_date"], "2026-11-10")

    def _ready_claim_plan_for_item(self, item):
        return {
            "status": "READY",
            "eligible": True,
            "target_date": item["target_date"],
            "archive_sha256": item["archive_sha256"],
            "archive_record_count": item["archive_record_count"],
            "manual_recovery_confirmed": False,
        }

    def test_claim_advances_after_exact_completed_exhausted_fifo_target(self):
        state, item_next = self._retired_exhausted_state_for_prepare()
        plan = self._ready_claim_plan_for_item(item_next)
        attempts_before = json.loads(json.dumps(state["attempts"]))
        events_before = json.loads(json.dumps(state["events"]))

        attempt = claim_legacy_attempt(
            state, plan, github_run_id="445578", now=NOW + timedelta(hours=1),
        )

        self.assertEqual(attempt["target_date"], "2026-11-12")
        self.assertEqual(attempt["archive_sha256"], item_next["archive_sha256"])
        self.assertEqual(attempt["archive_record_count"], item_next["archive_record_count"])
        self.assertEqual(state["attempts"][:-1], attempts_before)
        self.assertEqual(state["events"], events_before)
        self.assertEqual(state["attempts"][-1], attempt)

    def test_claim_fails_closed_when_completed_predecessor_archive_hash_differs(self):
        state, item_next = self._retired_exhausted_state_for_prepare()
        state["target_queue"]["completed"][0]["archive_sha256"] = "f" * 64
        plan = self._ready_claim_plan_for_item(item_next)
        attempts_before = json.loads(json.dumps(state["attempts"]))

        with self.assertRaisesRegex(
            auto.AutonomousResearchError,
            "Completed previous FIFO target does not match its archived attempt identity",
        ):
            claim_legacy_attempt(state, plan, github_run_id="445579", now=NOW + timedelta(hours=1))

        self.assertEqual(state["attempts"], attempts_before)

    def test_claim_fails_closed_when_completed_predecessor_record_count_differs(self):
        state, item_next = self._retired_exhausted_state_for_prepare()
        state["target_queue"]["completed"][0]["archive_record_count"] += 1
        plan = self._ready_claim_plan_for_item(item_next)
        attempts_before = json.loads(json.dumps(state["attempts"]))

        with self.assertRaisesRegex(
            auto.AutonomousResearchError,
            "Completed previous FIFO target does not match its archived attempt identity",
        ):
            claim_legacy_attempt(state, plan, github_run_id="445580", now=NOW + timedelta(hours=1))

        self.assertEqual(state["attempts"], attempts_before)

    def test_claim_does_not_skip_an_uncompleted_failed_previous_target(self):
        state, _, item_next = self._exhausted_target_fixture()
        state["authorization"] = active_state()["authorization"]
        state["target_queue"]["items"] = [item_next]
        auto.validate_state(state)
        plan = self._ready_claim_plan_for_item(item_next)

        with self.assertRaisesRegex(
            auto.AutonomousResearchError,
            "The previous target must be resolved before a new target is activated",
        ):
            claim_legacy_attempt(state, plan, github_run_id="445581", now=NOW + timedelta(hours=1))

    def test_claim_does_not_skip_an_uncompleted_previous_target_without_terminal_event(self):
        state, _, item_next = self._exhausted_target_fixture(omit_last_event=True)
        state["authorization"] = active_state()["authorization"]
        state["target_queue"]["items"] = [item_next]
        auto.validate_state(state)
        plan = self._ready_claim_plan_for_item(item_next)

        with self.assertRaisesRegex(
            auto.AutonomousResearchError,
            "The previous target is incomplete; new target activation is blocked",
        ):
            claim_legacy_attempt(state, plan, github_run_id="445582", now=NOW + timedelta(hours=1))

if __name__ == "__main__":
    unittest.main()
