import copy
import hashlib
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import general_autonomous_research as autonomous
import general_nvidia_guard
import general_target_queue as target_queue
import general_cycle55_recovery as recovery


STAMP = "2026-11-13T12:00:00+00:00"


def synthetic_rows(count, end_date):
    end = date.fromisoformat(end_date)
    start = end - timedelta(days=2 * (count - 1))
    rows = []
    for index in range(count):
        values = sorted({((index * 7 + offset * 11) % 60) + 1 for offset in range(6)})
        rows.append({
            "date": (start + timedelta(days=2 * index)).isoformat(),
            "numbers": values,
        })
    return rows


def _attempt(target_item, run_id, suffix, claimed_at):
    return {
        "attempt_id": hashlib.sha256(suffix.encode("utf-8")).hexdigest(),
        "target_date": target_item["target_date"],
        "archive_sha256": target_item["archive_sha256"],
        "archive_record_count": target_item["archive_record_count"],
        "claimed_at_utc": claimed_at,
        "github_run_id": run_id,
    }


def _recover_preview(
    current_state,
    snapshot_source_state,
    *,
    recorded_at_utc=STAMP,
    current_state_provenance=None,
    snapshot_source_provenance=None,
):
    return recovery._recover_cycle55_state_from_verified_documents(
        current_state,
        snapshot_source_state,
        recorded_at_utc=recorded_at_utc,
        current_state_provenance=(
            recovery.CURRENT_STATE_PROVENANCE
            if current_state_provenance is None else current_state_provenance
        ),
        snapshot_source_provenance=(
            recovery.SNAPSHOT_SOURCE_PROVENANCE
            if snapshot_source_provenance is None else snapshot_source_provenance
        ),
    )


class Cycle55RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.original_target_hash = recovery.TARGET_ARCHIVE_SHA256
        self.original_autonomous_target_hash = autonomous.CYCLE55_RECOVERY_ARCHIVE_SHA256
        rows_10 = synthetic_rows(983, recovery.TARGET_DATE)
        rows_12 = rows_10 + synthetic_rows(
            1, "2026-11-12"
        )
        self.head_item = target_queue._make_item(
            rows_10,
            source_archive_sha256="b" * 64,
            enqueued_at_utc="2026-11-11T01:27:46.542801+00:00",
        )
        self.tail_item = target_queue._make_item(
            rows_12,
            source_archive_sha256="c" * 64,
            enqueued_at_utc="2026-11-12T19:17:54.192421+00:00",
        )
        recovery.TARGET_ARCHIVE_SHA256 = self.head_item["archive_sha256"]
        autonomous.CYCLE55_RECOVERY_ARCHIVE_SHA256 = self.head_item["archive_sha256"]

        state = autonomous.initial_state()
        state.pop("recovery_history")
        state["schema_version"] = 2
        state["authorization"] = {
            "enabled": True,
            "authorized_at_utc": "2026-11-12T15:31:49.551665+00:00",
            "expires_at_utc": "2026-12-12T15:31:49.551665+00:00",
            "authorization_days": 30,
            "free_usage_verified": True,
            "max_requests_per_target": None,
            "last_paused_at_utc": None,
        }
        first = _attempt(
            self.head_item, "42980959550", "cycle55-attempt-1",
            "2026-11-11T07:39:09.707259+00:00",
        )
        second = _attempt(
            self.head_item, "43177096023", "cycle55-attempt-2",
            "2026-11-12T07:42:13.075530+00:00",
        )
        third = _attempt(
            self.head_item, recovery.TARGET_GITHUB_RUN_ID, "cycle55-attempt-3",
            "2026-11-12T19:18:00.576351+00:00",
        )
        state["attempts"] = [first, second, third]
        state["events"] = [
            {
                "attempt_id": first["attempt_id"],
                "outcome": "FAILED",
                "recorded_at_utc": "2026-11-11T07:44:21.270524+00:00",
                "reason_code": "PROVIDER_DELIVERY_UNCERTAIN",
            },
            {
                "attempt_id": second["attempt_id"],
                "outcome": "FAILED",
                "recorded_at_utc": "2026-11-12T07:46:56.797495+00:00",
                "reason_code": "PROVIDER_DELIVERY_UNCERTAIN",
            },
        ]
        state["target_queue"] = {
            "schema_version": 1,
            "observed_through_date": "2026-11-12",
            "items": [copy.deepcopy(self.head_item), copy.deepcopy(self.tail_item)],
            "completed": [],
        }
        self.source_state = copy.deepcopy(state)

        self.wrong_event = {
            "attempt_id": third["attempt_id"],
            "outcome": "NO_ELIGIBLE_CANDIDATE",
            "recorded_at_utc": "2026-11-12T19:29:11.146938+00:00",
            "reason_code": "NO_FROZEN_DIRECTIONAL_CANDIDATE",
        }
        self.wrong_completed = {
            "target_date": recovery.TARGET_DATE,
            "archive_sha256": recovery.TARGET_ARCHIVE_SHA256,
            "archive_record_count": recovery.TARGET_ARCHIVE_RECORD_COUNT,
            "completed_at_utc": "2026-11-12T19:29:11.160384+00:00",
            "outcome": "NO_ELIGIBLE_CANDIDATE",
        }
        self.current_state = copy.deepcopy(state)
        self.current_state["events"].append(copy.deepcopy(self.wrong_event))
        self.current_state["target_queue"]["items"] = [copy.deepcopy(self.tail_item)]
        self.current_state["target_queue"]["completed"] = [copy.deepcopy(self.wrong_completed)]

    def tearDown(self):
        recovery.TARGET_ARCHIVE_SHA256 = self.original_target_hash
        autonomous.CYCLE55_RECOVERY_ARCHIVE_SHA256 = self.original_autonomous_target_hash

    def recover(self):
        return _recover_preview(
            self.current_state,
            self.source_state,
            recorded_at_utc=STAMP,
        )

    def test_recovery_archives_false_terminal_records_and_restores_exact_fifo_snapshot(self):
        recovered = self.recover()
        self.assertEqual(recovered["schema_version"], 3)
        self.assertEqual(recovered["events"][-1]["outcome"], "FAILED")
        self.assertEqual(recovered["events"][-1]["reason_code"], "WORKFLOW_FAILED")
        self.assertEqual(
            recovered["events"][-1]["recorded_at_utc"],
            recovery.PROVIDER_FAILURE_AT_UTC,
        )
        self.assertEqual(recovered["recovery_history"][0]["archived_terminal_event"], self.wrong_event)
        self.assertEqual(recovered["recovery_history"][0]["archived_completed_record"], self.wrong_completed)
        self.assertEqual(recovered["recovery_history"][0]["source_state"], recovery.CURRENT_STATE_PROVENANCE)
        self.assertEqual(recovered["recovery_history"][0]["snapshot_source"], recovery.SNAPSHOT_SOURCE_PROVENANCE)
        self.assertEqual(recovered["target_queue"]["items"][0], self.head_item)
        self.assertEqual(recovered["target_queue"]["items"][1], self.tail_item)
        self.assertEqual(recovered["target_queue"]["completed"], [])
        self.assertEqual(
            recovered["recovery_history"][0]["restored_snapshot_item_sha256"],
            recovery._canonical_sha256(self.head_item),
        )
        autonomous.validate_state(recovered)

    def test_inputs_are_not_mutated(self):
        current_before = copy.deepcopy(self.current_state)
        source_before = copy.deepcopy(self.source_state)
        self.recover()
        self.assertEqual(self.current_state, current_before)
        self.assertEqual(self.source_state, source_before)

    def test_provider_post_response_failure_blocks_same_target_retry(self):
        recovered = self.recover()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "target-10.json"
            ledger = root / "ledger.json"
            lock = root / "lock.json"
            raw, _ = target_queue._decode_item(recovered["target_queue"]["items"][0])
            archive.write_bytes(raw)
            ledger.write_text(
                json.dumps({"schema_version": 1, "purpose": "test", "entries": []}),
                encoding="utf-8",
            )
            lock.write_text(json.dumps(general_nvidia_guard.initial_lock_state()), encoding="utf-8")
            result = autonomous.prepare_cycle(
                recovered,
                archive_path=archive,
                ledger_path=ledger,
                global_lock_path=lock,
                now=datetime.fromisoformat("2026-11-13T12:05:00+00:00"),
            )
        self.assertFalse(result["eligible"])
        self.assertEqual(result["status"], "WORKFLOW_FAILED_NO_AUTOMATIC_RETRY")

    def test_newer_target_cannot_bypass_restored_fifo_head(self):
        recovered = self.recover()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "target-12.json"
            ledger = root / "ledger.json"
            lock = root / "lock.json"
            raw, _ = target_queue._decode_item(recovered["target_queue"]["items"][1])
            archive.write_bytes(raw)
            ledger.write_text(
                json.dumps({"schema_version": 1, "purpose": "test", "entries": []}),
                encoding="utf-8",
            )
            lock.write_text(json.dumps(general_nvidia_guard.initial_lock_state()), encoding="utf-8")
            with self.assertRaisesRegex(
                autonomous.AutonomousResearchError, "immutable FIFO head snapshot"
            ):
                autonomous.prepare_cycle(
                    recovered,
                    archive_path=archive,
                    ledger_path=ledger,
                    global_lock_path=lock,
                    now=datetime.fromisoformat("2026-11-13T12:05:00+00:00"),
                )

    def test_source_tail_mutation_is_rejected(self):
        source = copy.deepcopy(self.source_state)
        source["target_queue"]["items"][1]["enqueued_at_utc"] = "2026-11-12T19:17:55.192421+00:00"
        with self.assertRaisesRegex(recovery.Cycle55RecoveryError, "pending FIFO tail"):
            _recover_preview(
                self.current_state, source, recorded_at_utc=STAMP
            )

    def test_wrong_event_or_completed_record_must_match_exactly(self):
        current = copy.deepcopy(self.current_state)
        current["events"][-1]["reason_code"] = "WORKFLOW_FAILED"
        with self.assertRaisesRegex(recovery.Cycle55RecoveryError, "false terminal event"):
            _recover_preview(current, self.source_state, recorded_at_utc=STAMP)

        current = copy.deepcopy(self.current_state)
        current["target_queue"]["completed"][-1]["archive_record_count"] += 1
        with self.assertRaisesRegex(recovery.Cycle55RecoveryError, "false FIFO completion"):
            _recover_preview(current, self.source_state, recorded_at_utc=STAMP)

    def test_unpinned_provenance_and_non_generation_51_snapshot_are_rejected(self):
        with self.assertRaisesRegex(recovery.Cycle55RecoveryError, "pinned Git history"):
            _recover_preview(
                self.current_state,
                self.source_state,
                recorded_at_utc=STAMP,
                snapshot_source_provenance={
                    **recovery.SNAPSHOT_SOURCE_PROVENANCE,
                    "generation": 50,
                },
            )

        source = copy.deepcopy(self.source_state)
        earlier_rows = synthetic_rows(982, "2026-11-08")
        earlier_item = target_queue._make_item(
            earlier_rows,
            source_archive_sha256="a" * 64,
            enqueued_at_utc="2026-11-10T12:00:00+00:00",
        )
        source["target_queue"]["items"].insert(0, earlier_item)
        with self.assertRaisesRegex(
            recovery.Cycle55RecoveryError, "Generation 51 does not contain Cycle #55 at the FIFO head"
        ):
            _recover_preview(
                self.current_state, source, recorded_at_utc=STAMP
            )

    def test_recovery_journal_digest_is_validated_and_second_recovery_is_refused(self):
        recovered = self.recover()
        broken = copy.deepcopy(recovered)
        broken["recovery_history"][0]["archived_terminal_event"]["reason_code"] = "WORKFLOW_FAILED"
        with self.assertRaisesRegex(autonomous.AutonomousResearchError, "Archived terminal event"):
            autonomous.validate_state(broken)

        moved_head = copy.deepcopy(recovered)
        moved_head["target_queue"]["items"][0]["source_archive_sha256"] = "f" * 64
        with self.assertRaisesRegex(autonomous.AutonomousResearchError, "exact FIFO head"):
            autonomous.validate_state(moved_head)

        with self.assertRaisesRegex(recovery.Cycle55RecoveryError, "schema v2"):
            _recover_preview(recovered, self.source_state, recorded_at_utc=STAMP)

        with self.assertRaisesRegex(recovery.Cycle55RecoveryError, "pinned Git history"):
            _recover_preview(
                self.current_state,
                self.source_state,
                recorded_at_utc=STAMP,
                current_state_provenance={},
            )

    def test_local_preview_writer_checks_provenance_and_never_overwrites_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current_path = root / "current.json"
            source_path = root / "generation-51.json"
            output_path = root / "recovery-preview.json"
            current_raw = (json.dumps(self.current_state, ensure_ascii=False, indent=2) + "\n").encode()
            source_raw = (json.dumps(self.source_state, ensure_ascii=False, indent=2) + "\n").encode()

            def provenance(raw, generation, commit):
                return {
                    "generation": generation,
                    "commit_sha": commit,
                    "blob_sha": recovery._git_blob_sha(raw),
                    "content_sha256": hashlib.sha256(raw).hexdigest(),
                }

            old_current_provenance = recovery.CURRENT_STATE_PROVENANCE
            old_source_provenance = recovery.SNAPSHOT_SOURCE_PROVENANCE
            try:
                recovery.CURRENT_STATE_PROVENANCE = provenance(
                    current_raw, 52, recovery.CURRENT_STATE_PROVENANCE["commit_sha"]
                )
                recovery.SNAPSHOT_SOURCE_PROVENANCE = provenance(
                    source_raw, 51, recovery.SNAPSHOT_SOURCE_PROVENANCE["commit_sha"]
                )
                current_path.write_bytes(current_raw)
                source_path.write_bytes(source_raw)
                result = recovery.build_preview_files(
                    current_path,
                    source_path,
                    output_path,
                    recorded_at_utc=STAMP,
                )
                self.assertTrue(output_path.exists())
                self.assertEqual(result["status"], "LOCAL_RECOVERY_PREVIEW_CREATED")
                self.assertEqual(result["schema_version"], 3)
                self.assertFalse(result["production_state_written"])
                self.assertEqual(current_path.read_bytes(), current_raw)
                self.assertEqual(source_path.read_bytes(), source_raw)

                tampered_source = root / "tampered-generation-51.json"
                tampered_source.write_bytes(source_raw + b" ")
                with self.assertRaisesRegex(recovery.Cycle55RecoveryError, "content SHA-256"):
                    recovery.build_preview_files(
                        current_path,
                        tampered_source,
                        root / "tampered-preview.json",
                        recorded_at_utc=STAMP,
                    )

                with self.assertRaisesRegex(recovery.Cycle55RecoveryError, "must not overwrite"):
                    recovery.build_preview_files(
                        current_path,
                        source_path,
                        current_path,
                        recorded_at_utc=STAMP,
                    )
            finally:
                recovery.CURRENT_STATE_PROVENANCE = old_current_provenance
                recovery.SNAPSHOT_SOURCE_PROVENANCE = old_source_provenance


if __name__ == "__main__":
    unittest.main()
