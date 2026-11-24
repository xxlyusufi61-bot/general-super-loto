import os
import unittest
from unittest.mock import patch

from general_nvidia_writer_preflight import (
    EXPECTED_APP_ID,
    EXPECTED_APP_PERMISSIONS,
    EXPECTED_BRANCH,
    EXPECTED_REPOSITORY,
    EXPECTED_RULESET_ID,
    EXPECTED_RULESET_NAME,
    run_preflight,
    validate_preflight_payloads,
)


def payloads():
    app = {
        "id": EXPECTED_APP_ID,
        "slug": "general-nvidia-budget-writer",
        "permissions": dict(EXPECTED_APP_PERMISSIONS),
    }
    repositories = {
        "total_count": 1,
        "repositories": [{"full_name": EXPECTED_REPOSITORY}],
    }
    ruleset = {
        "id": EXPECTED_RULESET_ID,
        "name": EXPECTED_RULESET_NAME,
        "target": "branch",
        "enforcement": "active",
        "source_type": "Repository",
        "source": EXPECTED_REPOSITORY,
        "conditions": {"ref_name": {"include": [EXPECTED_BRANCH], "exclude": []}},
        "rules": [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {"type": "update"},
        ],
    }
    return app, repositories, ruleset


class NVIDIAWriterPreflightTests(unittest.TestCase):
    def test_exact_budget_writer_identity_scope_and_ruleset_pass(self):
        app, repositories, ruleset = payloads()
        result = validate_preflight_payloads(app, repositories, ruleset)
        self.assertEqual(result["app_id"], EXPECTED_APP_ID)
        self.assertEqual(result["token_repository_scope"], EXPECTED_REPOSITORY)

    def test_wrong_app_id_fails_closed(self):
        app, repositories, ruleset = payloads()
        app["id"] = 123
        with self.assertRaises(ValueError):
            validate_preflight_payloads(app, repositories, ruleset)

    def test_extra_app_permission_fails_closed(self):
        app, repositories, ruleset = payloads()
        app["permissions"]["issues"] = "write"
        with self.assertRaises(ValueError):
            validate_preflight_payloads(app, repositories, ruleset)

    def test_missing_contents_write_fails_closed(self):
        app, repositories, ruleset = payloads()
        app["permissions"] = {"metadata": "read"}
        with self.assertRaises(ValueError):
            validate_preflight_payloads(app, repositories, ruleset)

    def test_wrong_repository_scope_fails_closed(self):
        app, repositories, ruleset = payloads()
        repositories["repositories"][0]["full_name"] = "someone/else"
        with self.assertRaises(ValueError):
            validate_preflight_payloads(app, repositories, ruleset)

    def test_multiple_repository_scope_fails_closed(self):
        app, repositories, ruleset = payloads()
        repositories["total_count"] = 2
        repositories["repositories"].append({"full_name": "someone/else"})
        with self.assertRaises(ValueError):
            validate_preflight_payloads(app, repositories, ruleset)

    def test_ruleset_response_does_not_need_hidden_bypass_actors(self):
        app, repositories, ruleset = payloads()
        self.assertNotIn("bypass_actors", ruleset)
        result = validate_preflight_payloads(app, repositories, ruleset)
        self.assertEqual(result["app_id"], EXPECTED_APP_ID)

    def test_wrong_branch_or_inactive_ruleset_fails_closed(self):
        app, repositories, ruleset = payloads()
        ruleset["conditions"]["ref_name"]["include"] = ["refs/heads/main"]
        with self.assertRaises(ValueError):
            validate_preflight_payloads(app, repositories, ruleset)
        app, repositories, ruleset = payloads()
        ruleset["enforcement"] = "disabled"
        with self.assertRaises(ValueError):
            validate_preflight_payloads(app, repositories, ruleset)

    def test_changed_protection_rules_fail_closed(self):
        app, repositories, ruleset = payloads()
        ruleset["rules"].append({"type": "pull_request"})
        with self.assertRaises(ValueError):
            validate_preflight_payloads(app, repositories, ruleset)


    def test_ruleset_read_uses_scoped_writer_token(self):
        app, repositories, ruleset = payloads()
        calls = []

        def fake_request_json(path, token="", *, jwt=False):
            calls.append((path, token, jwt))
            if path.startswith("/repos/"):
                return ruleset
            if path == "/app":
                return app
            if path == "/installation/repositories":
                return repositories
            raise AssertionError(f"Unexpected API path: {path}")

        with patch.dict(os.environ, {
            "GENERAL_NVIDIA_BUDGET_WRITER_TOKEN": "scoped-token",
            "GENERAL_NVIDIA_BUDGET_WRITER_APP_PRIVATE_KEY": "test-private-key",
            "GITHUB_REPOSITORY": EXPECTED_REPOSITORY,
        }), patch("general_nvidia_writer_preflight.request_json", side_effect=fake_request_json), patch(
            "general_nvidia_writer_preflight.create_app_jwt", return_value="app-jwt"
        ):
            run_preflight()

        self.assertEqual(calls, [
            (f"/repos/{EXPECTED_REPOSITORY}/rulesets/{EXPECTED_RULESET_ID}", "scoped-token", False),
            ("/app", "app-jwt", True),
            ("/installation/repositories", "scoped-token", False),
        ])


if __name__ == "__main__":
    unittest.main()
