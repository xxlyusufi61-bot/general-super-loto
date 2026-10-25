import unittest

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


if __name__ == "__main__":
    unittest.main()
