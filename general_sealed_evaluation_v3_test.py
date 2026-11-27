import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import general_research_windows as windows
import general_sealed_evaluation as sealed
from general_sealed_evaluation_test import make_rows, write_candidate_documents


class V3SealedPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.archive_path = self.root / "archive.json"
        self.archive_path.write_text(json.dumps({"draws": make_rows()}), encoding="utf-8")
        self.archive = sealed.load_archive(self.archive_path)
        self.ledger_path = self.root / "ledger.json"
        self.snapshot_dir = self.root / "snapshots"
        self.proposals, self.specs = write_candidate_documents(self.root)
        self.attempt_id = hashlib.sha256(b"v3-package-attempt").hexdigest()

        offline_dates, offline_digest = sealed._offline_development_consumption(
            self.root / "missing-memory.json"
        )
        self.assertEqual(offline_dates, [])
        self.package = windows.build_research_packages(
            self.archive["rows"],
            archive_sha256=self.archive["archive_sha256"],
            unavailable=set(),
            offline_memory_sha256=offline_digest,
        )[0]
        ledger = sealed._ledger_empty()
        exposure = windows.reserve_research_package(
            ledger,
            self.package,
            attempt_id=self.attempt_id,
            created_at_utc="2026-11-27T10:00:00+00:00",
        )
        self.ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        self.exposure_id = exposure["exposure_id"]

    def tearDown(self):
        self.temp.cleanup()

    def test_v3_preflight_claim_resume_and_evaluate_exact_reserved_package(self):
        plan = sealed.preflight(
            self.archive_path,
            self.ledger_path,
            offline_memory_path=self.root / "missing-memory.json",
            partition_version=sealed.UNSEEN_PARTITION_VERSION,
            package_id=self.package["package_id"],
            attempt_id=self.attempt_id,
        )
        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["partition_version"], 3)
        self.assertEqual(plan["research_package_id"], self.package["package_id"])
        self.assertFalse(plan["target_numbers_exposed"])

        first = sealed.create_claim(
            self.archive_path,
            self.ledger_path,
            self.proposals,
            self.specs,
            snapshot_directory=self.snapshot_dir,
            offline_memory_path=self.root / "missing-memory.json",
            sealed_plan_path=self.root / "sealed-plan.json",
            research_package_id=self.package["package_id"],
            scheduler_attempt_id=self.attempt_id,
        )
        self.assertTrue(first["claim_created"])
        self.assertFalse(first["claim_resumed"])
        self.assertEqual(first["research_package_id"], self.package["package_id"])

        pending = sealed.preflight(
            self.archive_path,
            self.ledger_path,
            offline_memory_path=self.root / "missing-memory.json",
            partition_version=3,
            package_id=self.package["package_id"],
            attempt_id=self.attempt_id,
        )
        self.assertTrue(pending["pending_claim_resume_available"])
        self.assertEqual(pending["pending_claim_id"], first["claim_id"])
        self.assertEqual(
            pending["forward_validation_dates"],
            self.package["validation_dates"],
        )
        self.assertEqual(
            pending["reverse_validation_dates"],
            self.package["reverse_validation_dates"],
        )

        resumed = sealed.create_claim(
            self.archive_path,
            self.ledger_path,
            self.proposals,
            self.specs,
            snapshot_directory=self.snapshot_dir,
            offline_memory_path=self.root / "missing-memory.json",
            research_package_id=self.package["package_id"],
            scheduler_attempt_id=self.attempt_id,
        )
        self.assertTrue(resumed["claim_resumed"])
        self.assertEqual(resumed["claim_id"], first["claim_id"])

        evaluated = sealed.evaluate_claim(
            first["claim_id"],
            self.ledger_path,
            self.proposals,
            self.specs,
            output_path=self.root / "evaluation.json",
            offline_memory_path=self.root / "missing-memory.json",
        )
        self.assertEqual(evaluated["status"], "EVALUATED_EXPLORATORY_FIXED_SPEC")
        ledger = sealed.load_ledger(self.ledger_path)
        claim = ledger["entries"][0]
        self.assertEqual(claim["partition_version"], 3)
        self.assertEqual(claim["state"], "EVALUATED")
        exposure = windows.package_exposure_for_attempt(
            ledger, package_id=self.package["package_id"], attempt_id=self.attempt_id
        )
        self.assertEqual(windows.package_exposure_status(ledger, exposure), "EVALUATED")
        self.assertTrue(set(self.package["dates"]).issubset(windows.exposure_dates(ledger)))

    def test_v3_package_cannot_be_claimed_under_another_attempt_or_hash(self):
        wrong_attempt = hashlib.sha256(b"wrong-attempt").hexdigest()
        with self.assertRaises(sealed.SealedEvaluationError):
            sealed.preflight(
                self.archive_path,
                self.ledger_path,
                offline_memory_path=self.root / "missing-memory.json",
                partition_version=3,
                package_id=self.package["package_id"],
                attempt_id=wrong_attempt,
            )
        changed = dict(self.package)
        changed["package_end_date"] = "2021-01-01"
        with self.assertRaises(windows.ResearchWindowError):
            windows.validate_package(
                changed,
                self.archive["rows"],
                archive_sha256=self.archive["archive_sha256"],
                unavailable=set(),
            )

    def test_claimed_or_evaluated_exposure_must_match_exact_claim_entry(self):
        ledger = sealed.load_ledger(self.ledger_path)
        exposure = windows.package_exposure_for_attempt(
            ledger, package_id=self.package["package_id"], attempt_id=self.attempt_id
        )
        windows.append_package_status(
            ledger,
            exposure,
            state="CLAIMED",
            recorded_at_utc="2026-11-27T10:01:00+00:00",
            reason="test claim without evaluation ledger entry",
        )
        self.ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        with self.assertRaises(sealed.SealedEvaluationError):
            sealed.load_ledger(self.ledger_path)

if __name__ == "__main__":
    unittest.main(verbosity=2)
