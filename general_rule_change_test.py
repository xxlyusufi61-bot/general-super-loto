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

    def test_command_parser_accepts_only_explicit_commands(self):
        self.assertEqual(rules.parse_decision_command("/approve 18"), ("APPROVED", 18))
        self.assertEqual(rules.parse_decision_command("/reject #19"), ("REJECTED", 19))
        self.assertEqual(rules.parse_decision_command("/approve@GeneralBot 20"), ("APPROVED", 20))
        self.assertIsNone(rules.parse_decision_command("approve 18"))
        self.assertIsNone(rules.parse_decision_command("/approve 0"))
        self.assertIsNone(rules.parse_decision_command("/approve 18; malicious"))
        self.assertIsNone(rules.parse_decision_command("/approve 18 extra"))

    def test_private_chat_authorization_requires_same_user(self):
        message = {"chat": {"id": 123456, "type": "private"}, "from": {"id": 123456}}
        self.assertTrue(rules.is_authorized({"message": message, "from": message["from"]}, "123456"))
        self.assertFalse(rules.is_authorized({"message": message, "from": message["from"]}, "123456", "999999"))
        self.assertFalse(rules.is_authorized({"message": message, "from": message["from"]}, "999999"))

    def test_group_chat_fails_closed_without_explicit_approver(self):
        message = {"chat": {"id": -456, "type": "group"}, "from": {"id": 123456}}
        context = {"message": message, "from": message["from"]}
        self.assertFalse(rules.is_authorized(context, "-456"))
        self.assertTrue(rules.is_authorized(context, "-456", "123456"))
        self.assertFalse(rules.is_authorized(context, "-456", "999999"))

    def test_decision_requires_pending_state_and_records_audit(self):
        body = rules.build_issue_body(VALID_REQUEST, {
            "workflow": "test", "run_id": "1", "sha": "abc123",
            "submitted_at": "2026-01-01T00:00:00+00:00",
        })
        approved = rules.decision_status(body, "APPROVED", 123456, "approver", "message-1")
        self.assertEqual(rules.status_from_body(approved), "APPROVED_PENDING_IMPLEMENTATION")
        self.assertIn("Telegram command reference: message-1", approved)
        self.assertIn("does not modify binding rules", approved)
        rejected = rules.decision_status(body, "REJECTED", 123456, "approver", "message-2")
        self.assertEqual(rules.status_from_body(rejected), "REJECTED")
        with self.assertRaises(ValueError):
            rules.decision_status(approved, "APPROVED", 123456, "approver", "message-3")

    def test_unauthorized_command_never_queries_github(self):
        class FakeGitHub:
            def request(self, *args, **kwargs):
                raise AssertionError("GitHub must not be called for unauthorized command")
        class FakeTelegram:
            def __init__(self):
                self.calls = []
            def call(self, method, payload):
                self.calls.append((method, payload))
                return {"ok": True, "result": {}}
        message = {
            "message_id": 5, "text": "/approve 42",
            "from": {"id": 777777},
            "chat": {"id": 123456, "type": "private"},
        }
        telegram = FakeTelegram()
        outcome = rules.handle_message(message, FakeGitHub(), telegram, "123456", "", "/repos/example/repo")
        self.assertEqual(outcome, "unauthorized")
        self.assertEqual(len(telegram.calls), 1)
        self.assertEqual(telegram.calls[0][0], "sendMessage")

    def test_authorized_command_records_decision_and_closes_issue(self):
        body = rules.build_issue_body(VALID_REQUEST, {
            "workflow": "test", "run_id": "1", "sha": "abc123",
            "submitted_at": "2026-01-01T00:00:00+00:00",
        })
        class FakeGitHub:
            def __init__(self):
                self.issue = {"title": "[RULE CHANGE REQUEST] RULE-EXAMPLE", "body": body, "state": "open"}
                self.patch = None
            def request(self, method, path, payload=None):
                if method == "GET":
                    return self.issue
                if method == "PATCH":
                    self.patch = payload
                    self.issue = {"title": self.issue["title"], "body": payload["body"], "state": "closed"}
                    return {}
                raise AssertionError("Unexpected GitHub method")
        class FakeTelegram:
            def __init__(self):
                self.calls = []
            def call(self, method, payload):
                self.calls.append((method, payload))
                return {"ok": True, "result": {}}
        message = {
            "message_id": 5, "text": "/approve 42",
            "from": {"id": 123456, "username": "approver"},
            "chat": {"id": 123456, "type": "private"},
        }
        github, telegram = FakeGitHub(), FakeTelegram()
        outcome = rules.handle_message(message, github, telegram, "123456", "", "/repos/example/repo")
        self.assertEqual(outcome, "approved")
        self.assertEqual(github.patch["state"], "closed")
        self.assertEqual(github.patch["state_reason"], "completed")
        self.assertIn("Decision: **APPROVED_PENDING_IMPLEMENTATION**", github.patch["body"])
        self.assertEqual(rules.status_from_body(github.patch["body"]), "APPROVED_PENDING_IMPLEMENTATION")
        self.assertEqual(telegram.calls[-1][0], "sendMessage")

if __name__ == "__main__":
    unittest.main()
