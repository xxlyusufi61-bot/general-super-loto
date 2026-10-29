import unittest

from general_agent_report_guard import (
    AGENT_SECTIONS,
    COMMON_SECTIONS,
    report_contract_prompt,
    required_sections,
    validate_agent_report,
)


def valid_report(agent):
    sections = list(COMMON_SECTIONS) + list(AGENT_SECTIONS[agent])
    values = {
        "SCOPE_AND_EVIDENCE": "Supplied mock source context only.",
        "CHECKS_PERFORMED": "Only contract parsing was exercised; no repository command ran.",
        "FINDINGS": (
            "VERIFIED_FROM_SUPPLIED_EVIDENCE: The synthetic fixture contains the required headings.\n"
            "HYPOTHESIS: The real model may still return an invalid structure.\n"
            "UNVERIFIED: No model or live workflow call was made."
        ),
        "LIMITATIONS": "This structural check does not verify semantic truth.",
        "NEXT_OFFLINE_CHECK": "Run the named offline report-guard unit tests.",
        "HYPOTHESES_AND_METHODS": "Synthetic hypothesis section for contract testing.",
        "EVIDENCE_NEEDED_TO_VALIDATE_OR_FALSIFY": "Run a documented chronological experiment with a baseline.",
        "REPRODUCTION_OR_DIAGNOSTIC": "No production defect reproduced; this is a synthetic fixture.",
        "PROPOSED_FIX_AND_TEST": "Proposed contract guard; run its offline test suite.",
        "VALIDATION_MATRIX": "Synthetic fixture structure: PASS; semantic truth: UNKNOWN.",
        "PASS_FAIL_UNKNOWN": "PASS means structure only; content truth remains UNKNOWN.",
        "COMPARISON_BASIS": "No candidates were measured; synthetic fixture only.",
        "OVERFITTING_CONTROLS": "No tuning was performed; use a frozen holdout in real evaluation.",
    }
    return "\n\n".join(section + ":\n" + values[section] for section in sections)


class GeneralAgentReportGuardTests(unittest.TestCase):
    def test_valid_report_contract_for_each_specialized_agent(self):
        for agent in AGENT_SECTIONS:
            with self.subTest(agent=agent):
                report = valid_report(agent)
                result = validate_agent_report(agent, report)
                self.assertTrue(result["valid"], result)
                self.assertEqual(result["reason"], "report_contract_pass")
                self.assertFalse(result["semantic_truth_verified"])
                self.assertEqual(tuple(required_sections(agent)), tuple(
                    COMMON_SECTIONS + AGENT_SECTIONS[agent]
                ))

    def test_every_role_prompt_contains_unique_work_product_guidance(self):
        prompts = {agent: report_contract_prompt(agent) for agent in AGENT_SECTIONS}
        for agent, prompt in prompts.items():
            for section in required_sections(agent):
                self.assertIn(section + ":", prompt)
            self.assertIn("A label is not proof.", prompt)
        self.assertIn("chronological split and baseline", prompts["research"])
        self.assertIn("minimal proposed fix", prompts["debug"])
        self.assertIn("PASS, FAIL, or UNKNOWN", prompts["validation"])
        self.assertIn("untouched evaluation interval", prompts["crossover"])

    def test_missing_agent_specific_section_is_rejected(self):
        report = valid_report("research").replace(
            "EVIDENCE_NEEDED_TO_VALIDATE_OR_FALSIFY:\nRun a documented chronological experiment with a baseline.",
            "",
        )
        result = validate_agent_report("research", report)
        self.assertFalse(result["valid"])
        self.assertIn("EVIDENCE_NEEDED_TO_VALIDATE_OR_FALSIFY", result["missing_sections"])

    def test_duplicate_or_empty_required_sections_are_rejected(self):
        report = valid_report("debug")
        report += "\n\nLIMITATIONS:\n"
        result = validate_agent_report("debug", report)
        self.assertFalse(result["valid"])
        self.assertIn("LIMITATIONS", result["duplicate_sections"])
        self.assertIn("LIMITATIONS", result["empty_sections"])

    def test_unlabelled_findings_are_rejected(self):
        report = valid_report("crossover").replace(
            "HYPOTHESIS: The real model may still return an invalid structure.",
            "The real model may still return an invalid structure.",
        )
        result = validate_agent_report("crossover", report)
        self.assertFalse(result["valid"])
        self.assertTrue(result["unlabelled_finding_lines"])

    def test_blank_non_text_and_unknown_agent_reports_are_rejected(self):
        for report in ("", "  ", None, 12):
            with self.subTest(report=repr(report)):
                self.assertFalse(validate_agent_report("debug", report)["valid"])
        self.assertEqual(
            validate_agent_report("archive", valid_report("debug"))["reason"],
            "unknown_agent",
        )

    def test_error_metadata_never_echoes_report_body(self):
        marker = "PRIVATE_REPORT_CONTENT_SENTINEL"
        report = valid_report("validation").replace(
            "Synthetic fixture structure", marker
        ).replace("PASS means structure only", marker)
        report = report.replace("PASS:", marker + ":")
        result = validate_agent_report("validation", report)
        self.assertNotIn(marker, str(result))


if __name__ == "__main__":
    unittest.main(verbosity=2)
