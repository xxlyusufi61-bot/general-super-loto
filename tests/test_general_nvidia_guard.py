import os
import tempfile
import unittest
from pathlib import Path
import textwrap
import urllib.error
from unittest.mock import patch

import general_core as core
from general_live_consent import CONFIRMATION_PHRASE

from general_nvidia_guard import (
    initial_lock_state,
    make_locked_state,
    make_unlocked_state,
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

            with patch.dict(
                os.environ,
                {
                    "NVIDIA_API_KEY": "offline-unit-test-key",
                    "GENERAL_CONFIRM_LIVE_CALLS": CONFIRMATION_PHRASE,
                    "GENERAL_NVIDIA_GUARD_PASSED": "1",
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
