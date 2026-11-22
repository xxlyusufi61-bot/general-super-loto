import base64
import unittest
from unittest.mock import patch

import general_writer_auth as auth


class WriterAuthTests(unittest.TestCase):
    def test_writer_roles_map_to_three_distinct_environment_names(self):
        self.assertEqual(
            set(auth.ROLE_ENVIRONMENT.values()),
            {
                "GENERAL_MAIN_WRITER_TOKEN",
                "GENERAL_AUTOMATION_STATE_WRITER_TOKEN",
                "GENERAL_NVIDIA_BUDGET_WRITER_TOKEN",
            },
        )

    def test_each_role_requires_its_own_token_and_never_falls_back(self):
        for role in auth.ROLE_ENVIRONMENT:
            with self.subTest(role=role):
                with self.assertRaises(auth.WriterAuthorizationError):
                    auth.resolve_writer_token(role, {"GITHUB_TOKEN": "broad-token"})

    def test_dedicated_role_token_is_selected(self):
        env = {
            "GITHUB_TOKEN": "read-only-token",
            "GENERAL_MAIN_WRITER_TOKEN": "main-only-token",
        }
        self.assertEqual(auth.resolve_writer_token("main", env), "main-only-token")

    def test_git_auth_header_uses_only_the_requested_role_token(self):
        env = {
            "GITHUB_TOKEN": "read-only-token",
            "GENERAL_NVIDIA_BUDGET_WRITER_TOKEN": "budget-only-token",
            "GIT_CONFIG_COUNT": "2",
            "GIT_CONFIG_KEY_0": "safe.key",
            "GIT_CONFIG_VALUE_0": "safe-value",
        }
        result = auth.git_auth_environment("nvidia_budget", env)
        self.assertEqual(result["GIT_CONFIG_COUNT"], "3")
        self.assertEqual(result["GIT_CONFIG_KEY_0"], "safe.key")
        self.assertEqual(result["GIT_CONFIG_VALUE_0"], "safe-value")
        expected = base64.b64encode(b"x-access-token:budget-only-token").decode("ascii")
        self.assertEqual(
            result["GIT_CONFIG_VALUE_2"],
            "AUTHORIZATION: basic " + expected,
        )
        self.assertEqual(env["GIT_CONFIG_COUNT"], "2")

    def test_invalid_role_or_token_fails_closed(self):
        with self.assertRaises(auth.WriterAuthorizationError):
            auth.resolve_writer_token("unrecognized", {})
        with self.assertRaises(auth.WriterAuthorizationError):
            auth.resolve_writer_token("main", {"GENERAL_MAIN_WRITER_TOKEN": "bad token"})

    def test_main_git_push_allows_only_main_and_agent_memory_proposals(self):
        with patch.dict(
            "os.environ",
            {"GENERAL_MAIN_WRITER_TOKEN": "offline-main-writer-token"},
            clear=True,
        ), patch.object(auth.subprocess, "run") as run:
            auth.run_git_push(
                "main",
                "origin",
                "general-agent-memory-12345-1",
            )
            args, kwargs = run.call_args
            self.assertEqual(args[0], ["git", "push", "origin", "general-agent-memory-12345-1"])
            self.assertIn("GENERAL_MAIN_WRITER_TOKEN", kwargs["env"])
            auth.run_git_push("main", "origin", "general-memory-proposal-12345-1")
            args, _ = run.call_args
            self.assertEqual(args[0], ["git", "push", "origin", "general-memory-proposal-12345-1"])
        for remote, ref in (
            ("other", "HEAD:main"),
            ("origin", "HEAD:general-nvidia-rate-limit-state"),
            ("origin", "general-coordinator-memory-12345-1"),
            ("origin", "--force"),
        ):
            with self.subTest(remote=remote, ref=ref):
                with self.assertRaises(auth.WriterAuthorizationError):
                    auth.run_git_push("main", remote, ref)

    def test_git_push_fails_when_the_main_identity_is_missing(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(auth.WriterAuthorizationError):
                auth.run_git_push("main", "origin", "HEAD:main")


if __name__ == "__main__":
    unittest.main()
