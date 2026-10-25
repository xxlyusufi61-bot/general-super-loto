import json
import unittest
import general_rule_change as rules

VALID_REQUEST = {
    "rule_id": "RULE-EXAMPLE",
    "current_rule": "Current wording",
    "proposed_rule": "Proposed wording",
    "reason": "A reason",
    "evidence": "Evidence to verify",
    "risk_assessment": "Potential risk",
    "tests_required": "Required tests",
    "rollback_plan": "Restore prior wording",
}

class RuleChangeRequestTests(unittest.TestCase):
    def make_report(self, payload=None):
        value = VALID_REQUEST if payload is None else payload
        return "Report\n<RULE_CHANGE_REQUEST_JSON>\n" + json.dumps(value) + "\n</RULE_CHANGE_REQUEST_JSON>\n"

    def test_no_request_block_means_no_request(self):
        self.assertIsNone(rules.extract_request("No change proposed."))

    def test_valid_request_is_extracted(self):
        self.assertEqual(rules.extract_request(self.make_report()), VALID_REQUEST)

    def test_malformed_request_fails_closed(self):
        with self.assertRaises(ValueError):
            rules.extract_request("<RULE_CHANGE_REQUEST_JSON>{bad}</RULE_CHANGE_REQUEST_JSON>")

    def test_missing_required_field_fails_closed(self):
        data = dict(VALID_REQUEST)
        del data["rollback_plan"]
        with self.assertRaises(ValueError):
            rules.extract_request(self.make_report(data))

    def test_same_current_and_proposed_rule_is_rejected(self):
        data = dict(VALID_REQUEST)
        data["proposed_rule"] = data["current_rule"]
        with self.assertRaises(ValueError):
            rules.extract_request(self.make_report(data))

    def test_callback_parser_accepts_only_expected_format(self):
        self.assertEqual(rules.parse_callback("g:a:18"), ("APPROVED", 18))
        self.assertEqual(rules.parse_callback("g:r:19"), ("REJECTED", 19))
        self.assertIsNone(rules.parse_callback("approve:18"))
        self.assertIsNone(rules.parse_callback("g:a:0"))
        self.assertIsNone(rules.parse_callback("g:a:18;evil"))

    def test_private_chat_authorization_requires_same_user(self):
        callback = {"from": {"id": 123456, "username": "approver"},
                    "message": {"chat": {"id": 123456, "type": "private"}}}
        self.assertTrue(rules.is_authorized(callback, "123456"))
        self.assertFalse(rules.is_authorized(callback, "123456", "999999"))
        self.assertFalse(rules.is_authorized(callback, "999999"))

    def test_group_chat_fails_closed_without_explicit_approver(self):
        callback = {"from": {"id": 123456},
                    "message": {"chat": {"id": -456, "type": "group"}}}
        self.assertFalse(rules.is_authorized(callback, "-456"))
        self.assertTrue(rules.is_authorized(callback, "-456", "123456"))
        self.assertFalse(rules.is_authorized(callback, "-456", "999999"))

    def test_decision_requires_pending_state_and_records_audit(self):
        body = rules.build_issue_body(VALID_REQUEST, {
            "workflow": "test", "run_id": "1", "sha": "abc123",
            "submitted_at": "2026-01-01T00:00:00+00:00",
        })
        approved = rules.decision_status(body, "APPROVED", 123456, "approver", "cb-1")
        self.assertEqual(rules.status_from_body(approved), "APPROVED_PENDING_IMPLEMENTATION")
        self.assertIn("Callback ID: cb-1", approved)
        self.assertIn("does not modify binding rules", approved)
        rejected = rules.decision_status(body, "REJECTED", 123456, "approver", "cb-2")
        self.assertEqual(rules.status_from_body(rejected), "REJECTED")
        with self.assertRaises(ValueError):
            rules.decision_status(approved, "APPROVED", 123456, "approver", "cb-3")

if __name__ == "__main__":
    unittest.main()
