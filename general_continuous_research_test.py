import copy
import unittest
from datetime import datetime, timedelta, timezone

import general_continuous_research as continuous
import general_nvidia_guard as nvidia


NOW = datetime(2026, 11, 23, 16, 30, tzinfo=timezone.utc)


def rate_state_with(*, forward=0, reverse=0, retry=0, total_other=0, minute=0):
    state = nvidia.initial_rate_limit_state()
    categories = (
        ["forward_discovery"] * forward
        + ["reverse_diagnostic"] * reverse
        + ["retry_reserve"] * retry
        + ["between_draws"] * total_other
    )
    stamp = (NOW - timedelta(seconds=5)).isoformat()
    for index, category in enumerate(categories):
        state["daily_requests"].append({
            "reserved_at_utc": stamp,
            "source": "synthetic-test",
            "category": category,
            "package_id": "test-package",
            "reservation_id": f"{index + 1:032x}",
        })
    for index in range(minute):
        state["requests"].append({
            "reserved_at_utc": stamp,
            "source": "synthetic-test",
        })
    return nvidia.validate_rate_limit_state(state)


class ContinuousSessionContextTests(unittest.TestCase):
    def setUp(self):
        self.context = continuous.build_session_context(
            event_name="schedule",
            action="",
            actor="scheduler",
            ref="refs/heads/main",
            run_id="45150000001",
            now=NOW,
        )

    def test_schedule_creates_exactly_five_hour_session(self):
        self.assertEqual(self.context["session_id"], "45150000001")
        self.assertEqual(
            continuous._parse_utc(self.context["deadline_at_utc"], "deadline")
            - continuous._parse_utc(self.context["started_at_utc"], "start"),
            continuous.SESSION_WINDOW,
        )
        self.assertEqual(self.context["iteration"], 1)

    def test_schedule_rejects_inherited_session_inputs(self):
        with self.assertRaises(continuous.ContinuousResearchError):
            continuous.build_session_context(
                event_name="schedule",
                action="",
                actor="scheduler",
                ref="refs/heads/main",
                run_id="45150000001",
                session_id="45150000001",
                now=NOW,
            )

    def test_non_default_branch_is_rejected(self):
        with self.assertRaises(continuous.ContinuousResearchError):
            continuous.build_session_context(
                event_name="schedule",
                action="",
                actor="scheduler",
                ref="refs/heads/feature",
                run_id="45150000001",
                now=NOW,
            )

    def test_successor_requires_bot_actor_and_same_session(self):
        args = {
            "event_name": "workflow_dispatch",
            "action": "continue",
            "actor": continuous.BOT_ACTOR,
            "ref": "refs/heads/main",
            "run_id": "45150000002",
            "session_id": self.context["session_id"],
            "started_at_utc": self.context["started_at_utc"],
            "deadline_at_utc": self.context["deadline_at_utc"],
            "iteration": "2",
            "now": NOW + timedelta(minutes=3),
        }
        successor = continuous.build_session_context(**args)
        self.assertEqual(successor["iteration"], 2)
        self.assertEqual(successor["session_id"], self.context["session_id"])

        args["actor"] = "human-user"
        with self.assertRaises(continuous.ContinuousResearchError):
            continuous.build_session_context(**args)

    def test_successor_rejects_wrong_action_same_run_expiry_and_iteration_overflow(self):
        args = {
            "event_name": "workflow_dispatch",
            "action": "continue",
            "actor": continuous.BOT_ACTOR,
            "ref": "refs/heads/main",
            "run_id": "45150000002",
            "session_id": self.context["session_id"],
            "started_at_utc": self.context["started_at_utc"],
            "deadline_at_utc": self.context["deadline_at_utc"],
            "iteration": "2",
            "now": NOW + timedelta(minutes=3),
        }
        for changes in (
            {"action": "pause"},
            {"run_id": self.context["session_id"]},
            {"iteration": str(continuous.MAX_CONTINUOUS_ITERATIONS + 1)},
            {"now": NOW + continuous.SESSION_WINDOW},
            {"ref": "refs/heads/feature"},
        ):
            trial = dict(args)
            trial.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(continuous.ContinuousResearchError):
                    continuous.build_session_context(**trial)

    def test_plan_revalidates_scheduled_session_without_resetting_its_clock(self):
        checked = continuous.validate_current_session_context(
            event_name="schedule",
            action="",
            actor="scheduler",
            ref="refs/heads/main",
            run_id="45150000001",
            session_id=self.context["session_id"],
            started_at_utc=self.context["started_at_utc"],
            deadline_at_utc=self.context["deadline_at_utc"],
            iteration="1",
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(checked["started_at_utc"], self.context["started_at_utc"])
        self.assertEqual(checked["deadline_at_utc"], self.context["deadline_at_utc"])

    def test_plan_requires_report_probe_and_remaining_session_budget(self):
        current = NOW + timedelta(minutes=2)
        no_report = continuous.build_continuation_plan(
            context=self.context,
            current_run_id="45150000001",
            report_delivered=False,
            probe_result=(True, "NEXT_CYCLE_ELIGIBLE"),
            now=current,
        )
        self.assertFalse(no_report["continue_enabled"])
        self.assertEqual(no_report["stop_reason"], "TELEGRAM_REPORT_NOT_CONFIRMED")

        no_work = continuous.build_continuation_plan(
            context=self.context,
            current_run_id="45150000001",
            report_delivered=True,
            probe_result=(False, "NO_EXECUTABLE_HYPOTHESIS"),
            now=current,
        )
        self.assertFalse(no_work["continue_enabled"])
        self.assertEqual(no_work["stop_reason"], "NO_EXECUTABLE_HYPOTHESIS")

        ready = continuous.build_continuation_plan(
            context=self.context,
            current_run_id="45150000001",
            report_delivered=True,
            probe_result=(True, "NEXT_CYCLE_ELIGIBLE"),
            now=current,
        )
        self.assertTrue(ready["continue_enabled"])
        self.assertEqual(ready["next_iteration"], 2)
        self.assertFalse(ready["session_values_rendered"])

    def test_iteration_cap_stops_without_dispatch(self):
        capped = dict(self.context, iteration=continuous.MAX_CONTINUOUS_ITERATIONS)
        plan = continuous.build_continuation_plan(
            context=capped,
            current_run_id="45150000001",
            report_delivered=True,
            probe_result=(True, "NEXT_CYCLE_ELIGIBLE"),
            now=NOW + timedelta(minutes=1),
        )
        self.assertFalse(plan["continue_enabled"])
        self.assertEqual(plan["stop_reason"], "ITERATION_SAFETY_LIMIT_REACHED")

    def test_dispatch_rejects_non_bot_before_network_access(self):
        with self.assertRaises(continuous.ContinuousResearchError):
            continuous._dispatch_workflow(
                repository=continuous.REPOSITORY,
                token="synthetic-token",
                actor="human-user",
                ref="refs/heads/main",
                session_id=self.context["session_id"],
                started_at_utc=self.context["started_at_utc"],
                deadline_at_utc=self.context["deadline_at_utc"],
                next_iteration=2,
                now=NOW + timedelta(minutes=1),
            )


class ContinuousBudgetPreflightTests(unittest.TestCase):
    def test_two_directional_slots_are_required(self):
        state = rate_state_with(forward=749, reverse=149)
        ready, reason = continuous.budget_has_next_turn_capacity(state, now=NOW)
        self.assertTrue(ready)
        self.assertEqual(reason, "BUDGET_AVAILABLE")

        blocked = rate_state_with(forward=750, reverse=149)
        ready, reason = continuous.budget_has_next_turn_capacity(blocked, now=NOW)
        self.assertFalse(ready)
        self.assertEqual(reason, "FORWARD_DISCOVERY_BUDGET_EXHAUSTED")

        blocked = rate_state_with(forward=749, reverse=150)
        ready, reason = continuous.budget_has_next_turn_capacity(blocked, now=NOW)
        self.assertFalse(ready)
        self.assertEqual(reason, "REVERSE_DIAGNOSTIC_BUDGET_EXHAUSTED")

    def test_retry_mode_requires_two_retry_reservations(self):
        state = rate_state_with(retry=148)
        ready, reason = continuous.budget_has_next_turn_capacity(
            state, retry_mode=True, now=NOW
        )
        self.assertTrue(ready)
        self.assertEqual(reason, "BUDGET_AVAILABLE")

        state = rate_state_with(retry=149)
        ready, reason = continuous.budget_has_next_turn_capacity(
            state, retry_mode=True, now=NOW
        )
        self.assertFalse(ready)
        self.assertEqual(reason, "RETRY_RESERVE_INSUFFICIENT_FOR_DIRECTIONAL_PAIR")

    def test_daily_and_hard_minute_limits_stop_preflight(self):
        state = rate_state_with(total_other=1499)
        ready, reason = continuous.budget_has_next_turn_capacity(state, now=NOW)
        self.assertFalse(ready)
        self.assertEqual(reason, "DAILY_BUDGET_INSUFFICIENT_FOR_DIRECTIONAL_PAIR")

        state = rate_state_with(minute=40)
        ready, reason = continuous.budget_has_next_turn_capacity(state, now=NOW)
        self.assertFalse(ready)
        self.assertEqual(reason, "HARD_RATE_LIMIT_EXHAUSTED")

    def test_known_schema_v2_is_only_migrated_in_memory(self):
        state = rate_state_with()
        legacy = copy.deepcopy(state)
        legacy["schema_version"] = 2
        legacy["package_max_requests"] = 4
        legacy["daily_max_requests"] = nvidia.LEGACY_NVIDIA_V2_200_DAILY_BUDGET
        legacy["category_limits"] = dict(nvidia.LEGACY_NVIDIA_V2_200_CATEGORY_LIMITS)

        ready, reason = continuous.budget_has_next_turn_capacity(legacy, now=NOW)
        self.assertTrue(ready)
        self.assertEqual(reason, "BUDGET_AVAILABLE")
        self.assertEqual(legacy["schema_version"], 2)
        self.assertEqual(legacy["daily_max_requests"], 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
