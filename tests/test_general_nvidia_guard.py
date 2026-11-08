import json
import os
import tempfile
import unittest
from pathlib import Path
import textwrap
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import general_core as core
import general_nvidia_guard as nvidia_guard
from general_live_consent import CONFIRMATION_PHRASE

from general_nvidia_guard import (
    NVIDIA_REQUESTS_PER_MINUTE,
    RATE_LIMIT_BRANCH,
    DIAGNOSTIC_REPOSITORY,
    DIAGNOSTIC_WORKFLOW_ID,
    DIAGNOSTIC_WORKFLOW_PATH,
    DIAGNOSTIC_APPROVAL_PHRASE,
    DIAGNOSTIC_REQUEST_SOURCE,
    DIAGNOSTIC_AUTHORIZED_ACTOR_LOGIN,
    DIAGNOSTIC_AUTHORIZED_ACTOR_ID,
    SINGLE_REQUEST_WORKFLOW_PATH,
    SINGLE_REQUEST_APPROVAL_PHRASE,
    SINGLE_REQUEST_SOURCE,
    SINGLE_REQUEST_AUTHORIZED_ACTOR_LOGIN,
    SINGLE_REQUEST_AUTHORIZED_ACTOR_ID,
    initial_lock_state,
    initial_rate_limit_state,
    make_locked_state,
    make_unlocked_state,
    reserve_rate_limit_slot,
    validate_rate_limit_state,
    should_activate_after_failure,
    validate_lock_state,
)


class GlobalNvidiaLockTests(unittest.TestCase):
    def test_initial_state_is_clear(self):
        state = validate_lock_state(initial_lock_state())
        self.assertIs(state["locked"], False)
        self.assertEqual(state["reason"], "")

    def test_locked_state_is_valid_and_has_reason(self):
        state = make_locked_state(
            initial_lock_state(),
            "API error: quota or billing state uncertain",
            stamp="2026-10-26T10:00:00+00:00",
            locked_by="test-run",
        )
        self.assertIs(state["locked"], True)
        self.assertIn("quota", state["reason"])
        self.assertEqual(state["locked_at"], "2026-10-26T10:00:00+00:00")
        self.assertEqual(validate_lock_state(state), state)

    def test_unlock_preserves_last_failure_reason(self):
        locked = make_locked_state(
            initial_lock_state(),
            "API response uncertain",
            stamp="2026-10-26T10:00:00+00:00",
            locked_by="test-run",
        )
        unlocked = make_unlocked_state(locked, stamp="2026-10-26T11:00:00+00:00")
        self.assertIs(unlocked["locked"], False)
        self.assertEqual(unlocked["reason"], "")
        self.assertEqual(unlocked["last_lock_reason"], "API response uncertain")
        self.assertEqual(unlocked["last_unlocked_at"], "2026-10-26T11:00:00+00:00")
        self.assertEqual(validate_lock_state(unlocked), unlocked)

    def test_invalid_state_fails_closed(self):
        invalid_states = [
            None,
            [],
            {"schema_version": 2, "locked": False, "reason": "", "last_lock_reason": ""},
            {
                "schema_version": 1,
                "locked": "false",
                "reason": "",
                "last_lock_reason": "",
            },
            {
                "schema_version": 1,
                "locked": True,
                "reason": "",
                "locked_at": None,
                "last_lock_reason": "",
            },
        ]
        for state in invalid_states:
            with self.subTest(state=state):
                with self.assertRaises(ValueError):
                    validate_lock_state(state)

    def test_api_failure_locks_only_after_attempt_started_and_not_successful(self):
        self.assertTrue(should_activate_after_failure(True, False))
        self.assertFalse(should_activate_after_failure(False, False))
        self.assertFalse(should_activate_after_failure(True, True))
        self.assertFalse(should_activate_after_failure(False, True))

    def test_reason_is_bounded(self):
        state = make_locked_state(
            initial_lock_state(),
            "x" * 1000,
            stamp="2026-10-26T10:00:00+00:00",
        )
        self.assertLessEqual(len(state["reason"]), 320)



    def _diagnostic_environment(self, run_id="987654321"):
        return {
            "GITHUB_REPOSITORY": DIAGNOSTIC_REPOSITORY,
            "GITHUB_RUN_ID": run_id,
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_REF": (
                DIAGNOSTIC_REPOSITORY + "/" + DIAGNOSTIC_WORKFLOW_PATH + "@refs/heads/main"
            ),
            "GITHUB_SHA": "a" * 40,
            "GENERAL_CONFIRM_LIVE_CALLS": DIAGNOSTIC_APPROVAL_PHRASE,
        }

    def _diagnostic_run(self, env=None):
        env = env or self._diagnostic_environment()
        return {
            "id": int(env["GITHUB_RUN_ID"]),
            "workflow_id": DIAGNOSTIC_WORKFLOW_ID,
            "path": DIAGNOSTIC_WORKFLOW_PATH,
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": env["GITHUB_SHA"],
            "run_attempt": 1,
            "status": "in_progress",
            "conclusion": None,
            "actor": {
                "login": DIAGNOSTIC_AUTHORIZED_ACTOR_LOGIN,
                "id": DIAGNOSTIC_AUTHORIZED_ACTOR_ID,
            },
            "repository": {"full_name": DIAGNOSTIC_REPOSITORY},
            "head_repository": {"full_name": DIAGNOSTIC_REPOSITORY},
        }

    def test_diagnostic_workflow_identity_requires_every_exact_github_boundary(self):
        env = self._diagnostic_environment()
        valid = self._diagnostic_run(env)
        self.assertEqual(
            nvidia_guard.validate_diagnostic_workflow_run(valid, env),
            env["GITHUB_RUN_ID"],
        )

        invalid_runs = [
            ("wrong run ID", {"id": int(env["GITHUB_RUN_ID"]) + 1}),
            ("wrong workflow ID", {"workflow_id": DIAGNOSTIC_WORKFLOW_ID + 1}),
            ("wrong workflow path", {"path": ".github/workflows/nvidia-test.yml"}),
            ("wrong event", {"event": "push"}),
            ("wrong branch", {"head_branch": "feature/test"}),
            ("wrong commit", {"head_sha": "b" * 40}),
            ("rerun", {"run_attempt": 2}),
            ("completed run", {"status": "completed"}),
            ("completed conclusion", {"conclusion": "success"}),
            ("unauthorized actor ID", {"actor": {"login": DIAGNOSTIC_AUTHORIZED_ACTOR_LOGIN, "id": 123}}),
            ("lookalike actor login", {"actor": {"login": "another-account", "id": DIAGNOSTIC_AUTHORIZED_ACTOR_ID}}),
            ("missing actor metadata", {"actor": None}),
            ("wrong repository", {"repository": {"full_name": "someone/else"}}),
            ("wrong head repository", {"head_repository": {"full_name": "someone/else"}}),
        ]
        for label, change in invalid_runs:
            candidate = dict(valid)
            candidate.update(change)
            with self.subTest(label=label):
                with self.assertRaises(RuntimeError):
                    nvidia_guard.validate_diagnostic_workflow_run(candidate, env)

        invalid_envs = [
            ("wrong repository", {"GITHUB_REPOSITORY": "someone/else"}),
            ("wrong event", {"GITHUB_EVENT_NAME": "push"}),
            ("wrong ref", {"GITHUB_REF": "refs/heads/feature"}),
            ("wrong workflow ref", {"GITHUB_WORKFLOW_REF": "someone/else/workflow.yml@refs/heads/main"}),
            ("rerun attempt", {"GITHUB_RUN_ATTEMPT": "2"}),
            ("wrong approval", {"GENERAL_CONFIRM_LIVE_CALLS": "YES"}),
            ("missing run ID", {"GITHUB_RUN_ID": ""}),
            ("noncanonical run ID", {"GITHUB_RUN_ID": "0987654321"}),
        ]
        for label, change in invalid_envs:
            candidate_env = env | change
            with self.subTest(label=label):
                with self.assertRaises(RuntimeError):
                    nvidia_guard.validate_diagnostic_workflow_run(valid, candidate_env)

    def test_runner_supplied_actor_values_cannot_override_github_api_actor(self):
        env = self._diagnostic_environment()
        env["GITHUB_ACTOR"] = DIAGNOSTIC_AUTHORIZED_ACTOR_LOGIN
        env["GITHUB_ACTOR_ID"] = str(DIAGNOSTIC_AUTHORIZED_ACTOR_ID)
        unauthorized = self._diagnostic_run(env)
        unauthorized["actor"] = {"login": "untrusted-writer", "id": 987654}
        with self.assertRaisesRegex(RuntimeError, "explicitly authorized GitHub account"):
            nvidia_guard.validate_diagnostic_workflow_run(unauthorized, env)

    def test_current_diagnostic_run_is_verified_through_github_api(self):
        env = self._diagnostic_environment()
        expected_run = self._diagnostic_run(env)
        with patch.dict(os.environ, env | {
            "GITHUB_TOKEN": "offline-test-token",
            "GITHUB_API_URL": "https://api.github.com",
        }, clear=True), patch.object(
            nvidia_guard, "_api_request", return_value=expected_run
        ) as mocked_api:
            run_id = nvidia_guard.read_current_diagnostic_workflow_run()
        self.assertEqual(run_id, env["GITHUB_RUN_ID"])
        mocked_api.assert_called_once_with(
            "GET",
            "https://api.github.com/repos/"
            + DIAGNOSTIC_REPOSITORY
            + "/actions/runs/"
            + env["GITHUB_RUN_ID"],
        )

    def test_diagnostic_one_shot_id_survives_rate_window_expiry(self):
        now = datetime(2026, 11, 2, 12, 0, 0, tzinfo=timezone.utc)
        state, wait = reserve_rate_limit_slot(
            initial_rate_limit_state(),
            DIAGNOSTIC_REQUEST_SOURCE,
            now=now,
            diagnostic_run_id="123456789",
        )
        self.assertEqual(wait, 0)
        self.assertEqual(state["diagnostic_run_ids"], ["123456789"])

        next_state, wait = reserve_rate_limit_slot(
            state,
            "ordinary-agent-after-window",
            now=now + timedelta(seconds=62),
        )
        self.assertEqual(wait, 0)
        self.assertEqual(len(next_state["requests"]), 1)
        self.assertEqual(next_state["diagnostic_run_ids"], ["123456789"])
        with self.assertRaisesRegex(ValueError, "already reserved"):
            reserve_rate_limit_slot(
                next_state,
                DIAGNOSTIC_REQUEST_SOURCE,
                now=now + timedelta(seconds=63),
                diagnostic_run_id="123456789",
            )

    def test_diagnostic_run_id_is_validated_and_legacy_rate_state_is_accepted(self):
        legacy_state = {
            "schema_version": 1,
            "window_seconds": 60,
            "max_requests": 40,
            "requests": [],
            "updated_at_utc": None,
        }
        validate_rate_limit_state(legacy_state)
        for invalid_id in ("0", "01", "-1", "12x", "１2", "", "1" * 33):
            with self.subTest(invalid_id=invalid_id):
                with self.assertRaises(ValueError):
                    reserve_rate_limit_slot(
                        legacy_state,
                        DIAGNOSTIC_REQUEST_SOURCE,
                        diagnostic_run_id=invalid_id,
                    )

        with self.assertRaises(ValueError):
            validate_rate_limit_state(legacy_state | {"diagnostic_run_ids": ["123", "123"]})

    def test_locked_diagnostic_reservation_is_single_use_and_uses_shared_rate_limit(self):
        active_lock = make_locked_state(
            initial_lock_state(),
            "offline diagnostic exception test",
            stamp="2026-11-02T12:00:00+00:00",
            locked_by="offline-test",
        )
        rate_state = initial_rate_limit_state()
        with patch.object(
            nvidia_guard,
            "read_current_diagnostic_workflow_run",
            return_value="987654321",
        ) as mocked_auth, patch.object(
            nvidia_guard,
            "read_remote_lock",
            return_value=(active_lock, "locked-sha"),
        ) as mocked_lock, patch.object(
            nvidia_guard,
            "read_remote_rate_limit",
            return_value=(rate_state, "rate-limit-head"),
        ) as mocked_read_rate, patch.object(
            nvidia_guard,
            "write_remote_rate_limit",
            return_value="rate-limit-head-after",
        ) as mocked_write_rate, patch.object(
            nvidia_guard, "write_remote_lock"
        ) as mocked_write_lock:
            reservation = nvidia_guard.reserve_nvidia_request(
                DIAGNOSTIC_REQUEST_SOURCE,
                diagnostic_exception=True,
            )

        mocked_write_lock.assert_not_called()
        self.assertEqual(reservation["source"], DIAGNOSTIC_REQUEST_SOURCE)
        mocked_auth.assert_called_once_with()
        mocked_lock.assert_called_once_with()
        mocked_read_rate.assert_called_once_with()
        written_state = mocked_write_rate.call_args.args[0]
        self.assertEqual(written_state["diagnostic_run_ids"], ["987654321"])
        self.assertEqual(len(written_state["requests"]), 1)
        self.assertIs(validate_lock_state(active_lock)["locked"], True)

    def test_active_global_lock_still_blocks_every_normal_request(self):
        active_lock = make_locked_state(
            initial_lock_state(),
            "offline normal-request lock test",
            stamp="2026-11-02T12:00:00+00:00",
            locked_by="offline-test",
        )
        with patch.dict(
            os.environ,
            {
                "GENERAL_NVIDIA_GUARD_PASSED": "1",
                "GENERAL_DIAGNOSTIC_MODE": "true",
                "GENERAL_ALLOW_NVIDIA_CALL": "true",
            },
            clear=True,
        ), patch.object(
            nvidia_guard,
            "read_remote_lock",
            return_value=(active_lock, "locked-sha"),
        ), patch.object(nvidia_guard, "read_remote_rate_limit") as read_rate:
            with self.assertRaisesRegex(RuntimeError, "Global NVIDIA safety lock is active"):
                nvidia_guard.reserve_nvidia_request("general-core")
        read_rate.assert_not_called()

    def test_diagnostic_exception_cannot_be_used_for_other_request_sources(self):
        with patch.object(nvidia_guard, "read_current_diagnostic_workflow_run") as auth, patch.object(
            nvidia_guard, "read_remote_lock"
        ) as read_lock:
            with self.assertRaisesRegex(RuntimeError, "non-diagnostic request source"):
                nvidia_guard.reserve_nvidia_request(
                    "general-core",
                    diagnostic_exception=True,
                )
        auth.assert_not_called()
        read_lock.assert_not_called()

    def test_diagnostic_exception_requires_global_lock_to_remain_active(self):
        with patch.object(
            nvidia_guard,
            "read_current_diagnostic_workflow_run",
            return_value="987654321",
        ), patch.object(
            nvidia_guard,
            "read_remote_lock",
            return_value=(initial_lock_state(), "clear-sha"),
        ), patch.object(nvidia_guard, "read_remote_rate_limit") as read_rate:
            with self.assertRaisesRegex(RuntimeError, "requires the global safety lock to remain active"):
                nvidia_guard.reserve_nvidia_request(
                    DIAGNOSTIC_REQUEST_SOURCE,
                    diagnostic_exception=True,
                )
        read_rate.assert_not_called()

    def test_ordinary_check_still_fails_closed_while_diagnostic_check_is_separate(self):
        active_lock = make_locked_state(
            initial_lock_state(),
            "offline ordinary check lock test",
            stamp="2026-11-02T12:00:00+00:00",
            locked_by="offline-test",
        )
        with patch.object(
            nvidia_guard, "read_remote_lock", return_value=(active_lock, "locked-sha")
        ):
            self.assertEqual(nvidia_guard.command_check(), 2)

    def test_diagnostic_check_requires_verified_context_and_active_global_lock(self):
        active_lock = make_locked_state(
            initial_lock_state(),
            "offline diagnostic check test",
            stamp="2026-11-02T12:00:00+00:00",
            locked_by="offline-test",
        )
        with patch.object(
            nvidia_guard, "read_current_diagnostic_workflow_run", return_value="987654321"
        ), patch.object(
            nvidia_guard, "read_remote_lock", return_value=(active_lock, "locked-sha")
        ):
            self.assertEqual(nvidia_guard.command_diagnostic_check(), 0)

        with patch.object(
            nvidia_guard, "read_current_diagnostic_workflow_run", return_value="987654321"
        ), patch.object(
            nvidia_guard, "read_remote_lock", return_value=(initial_lock_state(), "clear-sha")
        ):
            self.assertEqual(nvidia_guard.command_diagnostic_check(), 2)

    def test_diagnostic_workflow_preflight_is_separate_and_scoped(self):
        workflow = Path(".github/workflows/nvidia-diagnostic.yml").read_text(encoding="utf-8")
        self.assertIn("      actions: read", workflow)
        self.assertIn("GENERAL_CONFIRM_LIVE_CALLS: ${{ inputs.confirm_single_request }}", workflow)
        self.assertIn("python3 general_nvidia_guard.py diagnostic-check", workflow)
        self.assertNotIn("python3 general_nvidia_guard.py check", workflow)
        self.assertIn("GENERAL_DIAGNOSTIC_MODE: \"true\"", workflow)
        self.assertIn("group: general-nvidia-api", workflow)

    def test_shared_rate_limiter_allows_40_and_blocks_the_41st_request(self):
        now = datetime(2026, 11, 1, 1, 0, 0, tzinfo=timezone.utc)
        state = initial_rate_limit_state()
        for index in range(NVIDIA_REQUESTS_PER_MINUTE):
            state, wait = reserve_rate_limit_slot(
                state, "offline-test-agent-" + str(index), now=now
            )
            self.assertEqual(wait, 0)
            self.assertIsNotNone(state)
        blocked, wait = reserve_rate_limit_slot(
            state, "offline-test-agent-41", now=now
        )
        self.assertIsNone(blocked)
        self.assertGreater(wait, 0)
        self.assertEqual(len(state["requests"]), 40)

    def test_shared_rate_limiter_releases_a_slot_after_the_rolling_window(self):
        now = datetime(2026, 11, 1, 1, 0, 0, tzinfo=timezone.utc)
        state = initial_rate_limit_state()
        for index in range(40):
            state, _ = reserve_rate_limit_slot(state, "agent-" + str(index), now=now)
        next_state, wait = reserve_rate_limit_slot(
            state, "next-agent", now=now + timedelta(seconds=62)
        )
        self.assertEqual(wait, 0)
        self.assertIsNotNone(next_state)
        self.assertEqual(len(next_state["requests"]), 1)

    def test_shared_rate_limiter_rejects_corrupt_or_non_chronological_state(self):
        now = datetime(2026, 11, 1, 1, 0, 0, tzinfo=timezone.utc)
        invalid = [
            None,
            [],
            {"schema_version": 2, "window_seconds": 60, "max_requests": 40, "requests": []},
            {
                "schema_version": 1,
                "window_seconds": 60,
                "max_requests": 41,
                "requests": [],
            },
            {
                "schema_version": 1,
                "window_seconds": 60,
                "max_requests": 40,
                "requests": [{"reserved_at_utc": "not-a-date", "source": "agent"}],
            },
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_rate_limit_state(value)

        first = now.isoformat()
        second = (now - timedelta(seconds=1)).isoformat()
        state = {
            "schema_version": 1,
            "window_seconds": 60,
            "max_requests": 40,
            "requests": [
                {"reserved_at_utc": first, "source": "agent-a"},
                {"reserved_at_utc": second, "source": "agent-b"},
            ],
            "updated_at_utc": now.isoformat(),
        }
        with self.assertRaisesRegex(ValueError, "not chronological"):
            validate_rate_limit_state(state)


    def test_shared_rate_limit_persistence_uses_git_push_not_rest_contents_api(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            worktree = Path(temporary_directory)
            ledger = worktree / "general_nvidia_rate_limit.json"
            ledger.write_text(
                json.dumps(initial_rate_limit_state(), indent=2) + "\n",
                encoding="utf-8",
            )
            git_calls = []
            rev_calls = {"count": 0}

            def fake_git(arguments, *, cwd=None):
                git_calls.append(list(arguments))
                if arguments[:2] == ["rev-parse", "HEAD"]:
                    rev_calls["count"] += 1
                    return "head-before" if rev_calls["count"] <= 2 else "head-after"
                return ""

            with patch.object(
                nvidia_guard, "_ensure_rate_limit_worktree", return_value=worktree
            ), patch.object(nvidia_guard, "_run_git", side_effect=fake_git), patch.object(
                nvidia_guard, "_api_request"
            ) as mocked_api:
                state, head = nvidia_guard.read_remote_rate_limit()
                updated_state, wait = reserve_rate_limit_slot(
                    state,
                    "offline-git-persistence-test",
                    now=datetime(2026, 11, 1, 1, 0, 0, tzinfo=timezone.utc),
                )
                self.assertEqual(wait, 0)
                new_head = nvidia_guard.write_remote_rate_limit(updated_state, head)

            self.assertEqual(new_head, "head-after")
            self.assertIn(
                ["push", "origin", "HEAD:refs/heads/" + nvidia_guard.RATE_LIMIT_BRANCH],
                git_calls,
            )
            self.assertIn(["commit", "-m", "chore: reserve shared NVIDIA request slot"], git_calls)
            mocked_api.assert_not_called()

    def test_rate_limit_state_uses_a_separate_persistent_branch(self):
        self.assertEqual(RATE_LIMIT_BRANCH, "general-nvidia-rate-limit-state")

    def test_shared_reservation_rechecks_global_lock_before_each_request(self):
        clear_lock = initial_lock_state()
        active_lock = make_locked_state(
            initial_lock_state(),
            "lock activated between request reservations",
            stamp="2026-11-02T10:00:00+00:00",
            locked_by="offline-regression-test",
        )
        rate_state = initial_rate_limit_state()

        with patch.object(
            nvidia_guard,
            "read_remote_lock",
            side_effect=[(clear_lock, "clear-lock-sha"), (active_lock, "active-lock-sha")],
        ) as mocked_lock, patch.object(
            nvidia_guard,
            "read_remote_rate_limit",
            return_value=(rate_state, "rate-limit-head"),
        ) as mocked_read_rate, patch.object(
            nvidia_guard,
            "write_remote_rate_limit",
            return_value="rate-limit-head-after",
        ) as mocked_write_rate:
            nvidia_guard.reserve_nvidia_request("offline-first-request")

            with self.assertRaisesRegex(
                RuntimeError, "Global NVIDIA safety lock is active"
            ):
                nvidia_guard.reserve_nvidia_request("offline-second-request")

        self.assertEqual(mocked_lock.call_count, 2)
        self.assertEqual(mocked_read_rate.call_count, 1)
        self.assertEqual(mocked_write_rate.call_count, 1)

    def test_all_live_nvidia_workflows_share_the_repository_concurrency_group(self):
        workflow_names = (
            "general-agent.yml",
            "general-coordinator-execute.yml",
            "general-autonomous-research.yml",
            "open-research.yml",
            "nvidia-chat-test.yml",
            "nvidia-test.yml",
            "nvidia-diagnostic.yml",
        )
        for workflow_name in workflow_names:
            path = Path(".github/workflows") / workflow_name
            source = path.read_text(encoding="utf-8")
            with self.subTest(workflow=workflow_name):
                self.assertIn("group: general-nvidia-api", source)
                self.assertIn("GITHUB_TOKEN: ${{ github.token }}", source)
                self.assertIn("GITHUB_REPOSITORY: ${{ github.repository }}", source)


    def test_read_remote_lock_returns_blob_sha_for_contents_api_updates(self):
        locked_state = make_locked_state(
            initial_lock_state(),
            "offline lock SHA regression test",
            stamp="2026-11-01T01:00:00+00:00",
            locked_by="offline-test",
        )
        expected_ref = "refs/remotes/origin/main:general_nvidia_lock.json"
        commands = []

        def fake_git(arguments, *, cwd=None):
            commands.append(list(arguments))
            if arguments == ["show", expected_ref]:
                return json.dumps(locked_state)
            if arguments == ["rev-parse", expected_ref]:
                return "expected-blob-sha"
            raise AssertionError("Unexpected Git command: " + repr(arguments))

        with patch.dict(os.environ, {"GENERAL_DEFAULT_BRANCH": "main"}), patch.object(
            nvidia_guard, "_fetch_remote_branch"
        ) as mocked_fetch, patch.object(
            nvidia_guard, "_run_git", side_effect=fake_git
        ):
            observed_state, observed_sha = nvidia_guard.read_remote_lock()

        mocked_fetch.assert_called_once_with("main")
        self.assertEqual(observed_state, locked_state)
        self.assertEqual(observed_sha, "expected-blob-sha")
        self.assertIn(["rev-parse", expected_ref], commands)

    def _single_request_environment(self, run_id="987654321"):
        return {
            "GITHUB_REPOSITORY": DIAGNOSTIC_REPOSITORY,
            "GITHUB_RUN_ID": run_id,
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_REF": (
                DIAGNOSTIC_REPOSITORY + "/" + SINGLE_REQUEST_WORKFLOW_PATH + "@refs/heads/main"
            ),
            "GITHUB_SHA": "a" * 40,
            "GENERAL_CONFIRM_LIVE_CALLS": SINGLE_REQUEST_APPROVAL_PHRASE,
        }

    def _single_request_run(self, env=None):
        env = env or self._single_request_environment()
        return {
            "id": int(env["GITHUB_RUN_ID"]),
            "workflow_id": 509999999,
            "path": SINGLE_REQUEST_WORKFLOW_PATH,
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": env["GITHUB_SHA"],
            "run_attempt": 1,
            "status": "in_progress",
            "conclusion": None,
            "actor": {
                "login": SINGLE_REQUEST_AUTHORIZED_ACTOR_LOGIN,
                "id": SINGLE_REQUEST_AUTHORIZED_ACTOR_ID,
            },
            "repository": {"full_name": DIAGNOSTIC_REPOSITORY},
            "head_repository": {"full_name": DIAGNOSTIC_REPOSITORY},
        }

    def test_single_request_workflow_identity_requires_all_exact_boundaries(self):
        env = self._single_request_environment()
        valid = self._single_request_run(env)
        self.assertEqual(
            nvidia_guard.validate_single_request_workflow_run(valid, env),
            env["GITHUB_RUN_ID"],
        )
        invalid_runs = [
            ("wrong run ID", {"id": int(env["GITHUB_RUN_ID"]) + 1}),
            ("wrong workflow path", {"path": ".github/workflows/open-research.yml"}),
            ("wrong event", {"event": "push"}),
            ("wrong branch", {"head_branch": "feature/test"}),
            ("wrong commit", {"head_sha": "b" * 40}),
            ("rerun", {"run_attempt": 2}),
            ("completed run", {"status": "completed"}),
            ("completed conclusion", {"conclusion": "success"}),
            ("unauthorized actor ID", {"actor": {"login": SINGLE_REQUEST_AUTHORIZED_ACTOR_LOGIN, "id": 123}}),
            ("lookalike actor login", {"actor": {"login": "another-account", "id": SINGLE_REQUEST_AUTHORIZED_ACTOR_ID}}),
            ("wrong repository", {"repository": {"full_name": "someone/else"}}),
            ("wrong head repository", {"head_repository": {"full_name": "someone/else"}}),
        ]
        for label, change in invalid_runs:
            candidate = dict(valid)
            candidate.update(change)
            with self.subTest(label=label):
                with self.assertRaises(RuntimeError):
                    nvidia_guard.validate_single_request_workflow_run(candidate, env)

        invalid_envs = [
            ("wrong repository", {"GITHUB_REPOSITORY": "someone/else"}),
            ("wrong event", {"GITHUB_EVENT_NAME": "push"}),
            ("wrong ref", {"GITHUB_REF": "refs/heads/feature"}),
            ("wrong workflow ref", {"GITHUB_WORKFLOW_REF": "someone/else/workflow.yml@refs/heads/main"}),
            ("rerun attempt", {"GITHUB_RUN_ATTEMPT": "2"}),
            ("wrong phrase", {"GENERAL_CONFIRM_LIVE_CALLS": "YES"}),
            ("missing run ID", {"GITHUB_RUN_ID": ""}),
            ("noncanonical run ID", {"GITHUB_RUN_ID": "0987654321"}),
        ]
        for label, change in invalid_envs:
            with self.subTest(label=label):
                with self.assertRaises(RuntimeError):
                    nvidia_guard.validate_single_request_workflow_run(valid, env | change)

    def test_current_single_request_run_is_verified_through_github_api(self):
        env = self._single_request_environment()
        expected_run = self._single_request_run(env)
        with patch.dict(os.environ, env | {
            "GITHUB_TOKEN": "offline-test-token",
            "GITHUB_API_URL": "https://api.github.com",
        }, clear=True), patch.object(
            nvidia_guard, "_api_request", return_value=expected_run
        ) as mocked_api:
            run_id = nvidia_guard.read_current_single_request_workflow_run()
        self.assertEqual(run_id, env["GITHUB_RUN_ID"])
        mocked_api.assert_called_once_with(
            "GET",
            "https://api.github.com/repos/"
            + DIAGNOSTIC_REPOSITORY
            + "/actions/runs/"
            + env["GITHUB_RUN_ID"],
        )

    def test_single_request_check_requires_lock_active_and_does_not_unlock(self):
        env = self._single_request_environment()
        locked = make_locked_state(initial_lock_state(), "test lock")
        with patch.dict(os.environ, env, clear=True), patch.object(
            nvidia_guard, "read_current_single_request_workflow_run", return_value=env["GITHUB_RUN_ID"]
        ), patch.object(nvidia_guard, "read_remote_lock", return_value=(locked, "lock-sha")):
            self.assertEqual(nvidia_guard.command_single_request_check(), 0)

        unlocked = make_unlocked_state(locked)
        with patch.dict(os.environ, env, clear=True), patch.object(
            nvidia_guard, "read_current_single_request_workflow_run", return_value=env["GITHUB_RUN_ID"]
        ), patch.object(nvidia_guard, "read_remote_lock", return_value=(unlocked, "lock-sha")):
            self.assertEqual(nvidia_guard.command_single_request_check(), 1)

    def test_single_request_unlock_is_bound_to_authorized_run_and_active_lock(self):
        env = self._single_request_environment()
        locked = make_locked_state(initial_lock_state(), "test lock")
        with patch.dict(os.environ, env, clear=True), patch.object(
            nvidia_guard, "read_current_single_request_workflow_run", return_value=env["GITHUB_RUN_ID"]
        ), patch.object(
            nvidia_guard, "read_remote_lock", return_value=(locked, "lock-sha")
        ), patch.object(
            nvidia_guard, "write_remote_lock", return_value="unlock-commit"
        ) as write_lock, patch.object(
            nvidia_guard, "send_telegram", return_value=True
        ):
            self.assertEqual(nvidia_guard.command_single_request_unlock(), 0)
        written_state = write_lock.call_args.args[0]
        self.assertFalse(written_state["locked"])
        write_lock.assert_called_once()

        unlocked = make_unlocked_state(locked)
        with patch.dict(os.environ, env, clear=True), patch.object(
            nvidia_guard, "read_current_single_request_workflow_run", return_value=env["GITHUB_RUN_ID"]
        ), patch.object(
            nvidia_guard, "read_remote_lock", return_value=(unlocked, "lock-sha")
        ), patch.object(nvidia_guard, "write_remote_lock") as write_lock:
            self.assertEqual(nvidia_guard.command_single_request_unlock(), 1)
            write_lock.assert_not_called()

    def test_single_request_reservation_rechecks_run_lock_and_shared_rate_limit(self):
        env = self._single_request_environment()
        unlocked = make_unlocked_state(make_locked_state(initial_lock_state(), "test lock"))
        with patch.dict(os.environ, env, clear=True), patch.object(
            nvidia_guard, "read_current_single_request_workflow_run", return_value=env["GITHUB_RUN_ID"]
        ) as verify_run, patch.object(
            nvidia_guard, "read_remote_lock", return_value=(unlocked, "lock-sha")
        ) as read_lock, patch.object(
            nvidia_guard, "read_remote_rate_limit", return_value=(initial_rate_limit_state(), "rate-sha")
        ), patch.object(
            nvidia_guard, "write_remote_rate_limit", return_value="rate-commit"
        ) as write_rate:
            reserved = nvidia_guard.reserve_nvidia_request(
                SINGLE_REQUEST_SOURCE,
                single_request_exception=True,
            )
        verify_run.assert_called_once()
        read_lock.assert_called_once()
        write_rate.assert_called_once()
        self.assertEqual(reserved["source"], SINGLE_REQUEST_SOURCE)

    def test_single_request_reservation_rejects_wrong_source_or_active_lock(self):
        env = self._single_request_environment()
        with self.assertRaisesRegex(RuntimeError, "cannot authorize another request source"):
            nvidia_guard.reserve_nvidia_request("open-research-forward", single_request_exception=True)

        locked = make_locked_state(initial_lock_state(), "test lock")
        with patch.dict(os.environ, env, clear=True), patch.object(
            nvidia_guard, "read_current_single_request_workflow_run"
        ), patch.object(
            nvidia_guard, "read_remote_lock", return_value=(locked, "lock-sha")
        ), patch.object(nvidia_guard, "read_remote_rate_limit") as read_rate:
            with self.assertRaisesRegex(RuntimeError, "global safety lock is active"):
                nvidia_guard.reserve_nvidia_request(
                    SINGLE_REQUEST_SOURCE,
                    single_request_exception=True,
                )
            read_rate.assert_not_called()

    def test_general_core_python_syntax(self):
        source = Path("general_core.py").read_text(encoding="utf-8")
        compile(source, "general_core.py", "exec")

    def test_nvidia_workflow_inline_python_syntax(self):
        for workflow_name in ("nvidia-test.yml", "nvidia-chat-test.yml"):
            workflow_path = Path(".github/workflows") / workflow_name
            workflow = workflow_path.read_text(encoding="utf-8")
            marker = "python3 - <<'PY'\n"
            self.assertIn(marker, workflow, workflow_name + " missing inline Python script")
            script = workflow.split(marker, 1)[1].split("\n          PY", 1)[0]
            script = textwrap.dedent(script)
            with self.subTest(workflow=workflow_name):
                compile(script, str(workflow_path), "exec")

    def _assert_timeout_does_not_retry(self, failure):
        """Exercise the real timeout branch while replacing network I/O with a mock."""
        with tempfile.TemporaryDirectory() as temporary_directory:
            marker_root = Path(temporary_directory)

            def temporary_marker_path(original_path):
                return marker_root / Path(original_path).name

            with patch("general_core.reserve_nvidia_request") as mocked_reserve, patch.dict(
                os.environ,
                {
                    "NVIDIA_API_KEY": "offline-unit-test-key",
                    "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
                    "GENERAL_NVIDIA_GUARD_PASSED": "1",
                    "GENERAL_NVIDIA_FREE_USAGE_VERIFIED": "true",
                },
            ):
                with patch("general_core.Path", side_effect=temporary_marker_path):
                    with patch(
                        "general_core.urllib.request.urlopen",
                        side_effect=failure,
                    ) as mocked_urlopen:
                        with self.assertRaisesRegex(RuntimeError, "timed out after"):
                            core.ask_nvidia(
                                [{"role": "user", "content": "offline timeout test"}]
                            )

            mocked_reserve.assert_called_once_with("general-core")
            self.assertEqual(
                mocked_urlopen.call_count,
                1,
                "A failed request must not be retried automatically.",
            )
            self.assertTrue(
                (marker_root / "general_nvidia_api_attempted").is_file(),
                "The attempted-request marker must be present after a timeout.",
            )
            self.assertFalse(
                (marker_root / "general_nvidia_api_succeeded").exists(),
                "A timed-out request must never receive a success marker.",
            )

    def test_timeout_fails_closed_without_retry(self):
        self._assert_timeout_does_not_retry(TimeoutError("simulated timeout"))

    def test_urlerror_timeout_fails_closed_without_retry(self):
        self._assert_timeout_does_not_retry(
            urllib.error.URLError(TimeoutError("simulated socket timeout"))
        )


if __name__ == "__main__":
    unittest.main()
