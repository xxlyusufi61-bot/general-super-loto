import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "legacy_source_audit.json"
EXPECTED_SOURCES = {
    "legacy_sources/Super_Loto_V8_6_Donusum_Kesif.py",
    "legacy_sources/Super_Loto_Sifre_Kesif_Motoru_v1.py",
    "legacy_sources/Super_Loto_NEAR_STATE_Yapi_Dogrulama_Motoru_v5_0.py",
}

class LegacySourceAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(REPORT.read_text(encoding="utf-8"))

    def test_audit_does_not_change_binding_rules_or_require_official_verification(self):
        self.assertIs(self.report["binding_rules_changed"], False)
        self.assertIs(self.report["official_source_verification_required"], False)

    def test_all_preserved_sources_are_accounted_for_and_present(self):
        self.assertEqual(set(self.report["sources"]), EXPECTED_SOURCES)
        for path in EXPECTED_SOURCES:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file())
                self.assertTrue(self.report["sources"][path]["findings"])

    def test_v86_leakage_remains_a_blocker(self):
        findings = self.report["sources"]["legacy_sources/Super_Loto_V8_6_Donusum_Kesif.py"]["findings"]
        self.assertIn("V86-TARGET-B-LEAKAGE", {item["id"] for item in findings})
        self.assertEqual(self.report["sources"]["legacy_sources/Super_Loto_V8_6_Donusum_Kesif.py"]["status"], "DO_NOT_USE_AS_TRUSTED_EVALUATOR")

    def test_v1_silent_failures_and_fallback_are_recorded(self):
        findings = self.report["sources"]["legacy_sources/Super_Loto_Sifre_Kesif_Motoru_v1.py"]["findings"]
        ids = {item["id"] for item in findings}
        self.assertIn("V1-SILENT-GENERATOR-FAILURES", ids)
        self.assertIn("V1-FALLBACK-NUMBERS", ids)
        self.assertIn("V1-EMBEDDED-ARCHIVE", ids)

    def test_near_state_old_index_splits_are_not_treated_as_current_evidence(self):
        entry = self.report["sources"]["legacy_sources/Super_Loto_NEAR_STATE_Yapi_Dogrulama_Motoru_v5_0.py"]
        self.assertEqual(entry["status"], "LEGACY_RESULT_NOT_REPRODUCED")
        self.assertIn("NEAR-STATE-HISTORICAL-INDEX-BOUNDARIES", {item["id"] for item in entry["findings"]})

    def test_next_gate_preserves_leakage_and_regime_constraints(self):
        requirements = self.report["next_gate"]["requirements"]
        joined = " ".join(requirements).lower()
        for phrase in ("target numbers", "r54", "r60", "chronological", "random 6-of-60", "open_definition"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, joined)

if __name__ == "__main__":
    unittest.main(verbosity=2)
