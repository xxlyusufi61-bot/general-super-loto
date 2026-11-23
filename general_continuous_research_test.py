import copy
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

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

    def test_controller_has_no_session_or_iteration_ceiling(self):
        self.assertFalse(hasattr(continuous, "SESSION_WINDOW"))
        self.assertFalse(hasattr(continuous, "MAX_CONTINUOUS_ITERATIONS"))

    def test_schedule_creates_open_ended_session_identity(self):
        self.assertEqual(self.context["session_id"], "45150000001")
        self.assertEqual(self.context["started_at_utc"], "2026-11-23T16:30:00Z")
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
            "iteration": "2",
            "parent_run_id": self.context["session_id"],
            "now": NOW + timedelta(minutes=3),
        }
        successor = continuous.build_session_context(**args)
        self.assertEqual(successor["iteration"], 2)
        self.assertEqual(successor["session_id"], self.context["session_id"])

        args["actor"] = "human-user"
        with self.assertRaises(continuous.ContinuousResearchError):
            continuous.build_session_context(**args)

    def test_successor_requires_a_distinct_parent_run_id(self):
        args = {
            "event_name": "workflow_dispatch",
            "action": "continue",
            "actor": continuous.BOT_ACTOR,
            "ref": "refs/heads/main",
            "run_id": "45150000002",
            "session_id": self.context["session_id"],
            "started_at_utc": self.context["started_at_utc"],
            "iteration": "2",
            "now": NOW + timedelta(minutes=3),
        }
        with self.assertRaises(continuous.ContinuousResearchError):
            continuous.build_session_context(**args)

        args["parent_run_id"] = "45150000002"
        with self.assertRaises(continuous.ContinuousResearchError):
            continuous.build_session_context(**args)

    def test_successor_rejects_wrong_action_and_same_run(self):
        args = {
            "event_name": "workflow_dispatch",
            "action": "continue",
            "actor": continuous.BOT_ACTOR,
            "ref": "refs/heads/main",
            "run_id": "45150000002",
            "session_id": self.context["session_id"],
            "started_at_utc": self.context["started_at_utc"],
            "iteration": "2",
            "parent_run_id": self.context["session_id"],
            "now": NOW + timedelta(days=3650),
        }
        for changes in (
            {"action": "pause"},
            {"run_id": self.context["session_id"]},
            {"iteration": "01"},
            {"ref": "refs/heads/feature"},
        ):
            trial = dict(args)
            trial.update(changes)
            with self.subTest(changes=changes):
                with self.assertRaises(continuous.ContinuousResearchError):
                    continuous.build_session_context(**trial)

    def test_successor_accepts_more_than_150_turns_after_many_years(self):
        successor = continuous.build_session_context(
            event_name="workflow_dispatch",
            action="continue",
            actor=continuous.BOT_ACTOR,
            ref="refs/heads/main",
            run_id="45150000002",
            session_id=self.context["session_id"],
            started_at_utc=self.context["started_at_utc"],
            iteration="151",
            parent_run_id=self.context["session_id"],
            now=NOW + timedelta(days=3650),
        )
        self.assertEqual(successor["iteration"], 151)
        self.assertEqual(successor["session_id"], self.context["session_id"])

    def test_plan_revalidates_scheduled_session_without_resetting_its_clock(self):
        checked = continuous.validate_current_session_context(
            event_name="schedule",
            action="",
            actor="scheduler",
            ref="refs/heads/main",
            run_id="45150000001",
            session_id=self.context["session_id"],
            started_at_utc=self.context["started_at_utc"],
            iteration="1",
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(checked["started_at_utc"], self.context["started_at_utc"])

    def test_plan_requires_report_probe_and_eligibility(self):
        current = NOW + timedelta(minutes=2)
        no_report = continuous.build_continuation_plan(
            context=self.context,
            current_run_id="45150000001",
            report_delivered=False,
            research_attempt_made=True,
            probe_result=(True, "NEXT_CYCLE_ELIGIBLE"),
            now=current,
        )
        self.assertFalse(no_report["continue_enabled"])
        self.assertEqual(no_report["stop_reason"], "TELEGRAM_REPORT_NOT_CONFIRMED")

        no_work = continuous.build_continuation_plan(
            context=self.context,
            current_run_id="45150000001",
            report_delivered=True,
            research_attempt_made=True,
            probe_result=(False, "NO_EXECUTABLE_HYPOTHESIS"),
            now=current,
        )
        self.assertFalse(no_work["continue_enabled"])
        self.assertEqual(no_work["stop_reason"], "NO_EXECUTABLE_HYPOTHESIS")

        no_new_target = continuous.build_continuation_plan(
            context=self.context,
            current_run_id="45150000001",
            report_delivered=False,
            research_attempt_made=False,
            probe_result=(False, "WAITING_FOR_NEW_LATEST_DRAW"),
            now=current,
        )
        self.assertFalse(no_new_target["continue_enabled"])
        self.assertEqual(no_new_target["stop_reason"], "WAITING_FOR_NEW_LATEST_DRAW")

        ready = continuous.build_continuation_plan(
            context=self.context,
            current_run_id="45150000001",
            report_delivered=True,
            research_attempt_made=True,
            probe_result=(True, "NEXT_CYCLE_ELIGIBLE"),
            now=current,
        )
        self.assertTrue(ready["continue_enabled"])
        self.assertEqual(ready["next_iteration"], 2)
        self.assertFalse(ready["session_values_rendered"])

    def test_session_does_not_expire_or_stop_at_150_iterations(self):
        long_running = dict(self.context, iteration=150)
        plan = continuous.build_continuation_plan(
            context=long_running,
            current_run_id="45150000001",
            report_delivered=True,
            research_attempt_made=True,
            probe_result=(True, "NEXT_CYCLE_ELIGIBLE"),
            now=NOW + timedelta(days=3650),
        )
        self.assertTrue(plan["continue_enabled"])
        self.assertEqual(plan["next_iteration"], 151)

    def test_dispatch_rejects_non_bot_before_network_access(self):
        with self.assertRaises(continuous.ContinuousResearchError):
            continuous._dispatch_workflow(
                repository=continuous.REPOSITORY,
                token="synthetic-token",
                actor="human-user",
                event_name="workflow_dispatch",
                action="continue",
                ref="refs/heads/main",
                session_id=self.context["session_id"],
                started_at_utc=self.context["started_at_utc"],
                next_iteration=2,
                parent_run_id=self.context["session_id"],
            )




    def test_workflow_continuation_is_bot_only_and_scoped(self):
        workflow_path = (
            Path(__file__).resolve().parent
            / ".github"
            / "workflows"
            / "general-autonomous-research.yml"
        )
        workflow = workflow_path.read_text(encoding="utf-8")
        self.assertIn("github.actor == 'github-actions[bot]'", workflow)
        self.assertIn("vars.GENERAL_SCOPED_WRITERS_VALIDATED == 'true'", workflow)
        self.assertIn("continuous_parent_run_id", workflow)
        self.assertIn("actions: write", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("steps.attempt_reservation.outputs.attempt_id", workflow)
        self.assertIn("steps.telegram_success.outcome", workflow)
        self.assertIn("steps.telegram_no_candidate.outcome", workflow)
        self.assertIn("steps.telegram_failure.outcome", workflow)
        self.assertNotIn("GENERAL_SCOPED_WRITERS_VALIDATED: true", workflow)
        self.assertNotIn("continuous_deadline_at_utc", workflow)
        self.assertNotIn("--deadline-at-utc", workflow)

    def test_scheduled_owner_actor_can_dispatch_one_successor(self):
        class Response:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

        with patch.object(
            continuous.urllib.request,
            "urlopen",
            return_value=Response(),
        ) as send:
            continuous._dispatch_workflow(
                repository=continuous.REPOSITORY,
                token="synthetic-token",
                actor="xxlyusufi61-bot",
                event_name="schedule",
                action="",
                ref="refs/heads/main",
                session_id=self.context["session_id"],
                started_at_utc=self.context["started_at_utc"],
                next_iteration=151,
                parent_run_id="45150000001",
            )
        request = send.call_args.args[0]
        payload = __import__("json").loads(request.data.decode("utf-8"))
        self.assertEqual(payload["ref"], "main")
        self.assertEqual(payload["inputs"]["continuous_parent_run_id"], "45150000001")
        self.assertEqual(payload["inputs"]["continuous_iteration"], "151")

    def test_parent_run_verification_requires_exact_workflow_and_dispatch_job(self):
        parent = {
            "id": 45150000001,
            "workflow_id": continuous.AUTONOMOUS_WORKFLOW_ID,
            "path": ".github/workflows/" + continuous.WORKFLOW_FILE,
            "head_branch": "main",
            "event": "schedule",
            "status": "completed",
            "conclusion": "success",
            "repository": {"full_name": continuous.REPOSITORY},
        }
        jobs = {
            "jobs": [{
                "name": "continue-research",
                "status": "completed",
                "conclusion": "success",
                "steps": [{
                    "name": "Dispatch exactly one successor research turn",
                    "status": "completed",
                    "conclusion": "success",
                }],
            }]
        }
        with patch.object(
            continuous, "_github_get_json", side_effect=[parent, jobs]
        ) as get_json:
            continuous.verify_parent_dispatch(
                repository=continuous.REPOSITORY,
                token="synthetic-read-token",
                parent_run_id="45150000001",
                current_run_id="45150000002",
            )
        self.assertEqual(get_json.call_count, 2)

    def test_parent_run_verification_rejects_other_workflow_or_missing_dispatch_job(self):
        parent = {
            "id": 45150000001,
            "workflow_id": continuous.AUTONOMOUS_WORKFLOW_ID + 1,
            "path": ".github/workflows/" + continuous.WORKFLOW_FILE,
            "head_branch": "main",
            "event": "schedule",
            "status": "completed",
            "conclusion": "success",
            "repository": {"full_name": continuous.REPOSITORY},
        }
        with patch.object(continuous, "_github_get_json", return_value=parent):
            with self.assertRaises(continuous.ContinuousResearchError):
                continuous.verify_parent_dispatch(
                    repository=continuous.REPOSITORY,
                    token="synthetic-read-token",
                    parent_run_id="45150000001",
                    current_run_id="45150000002",
                )

        parent["workflow_id"] = continuous.AUTONOMOUS_WORKFLOW_ID
        jobs = {"jobs": [{
            "name": "scheduled-cycle",
            "status": "completed",
            "conclusion": "success",
            "steps": [],
        }]}
        with patch.object(
            continuous, "_github_get_json", side_effect=[parent, jobs]
        ):
            with self.assertRaises(continuous.ContinuousResearchError):
                continuous.verify_parent_dispatch(
                    repository=continuous.REPOSITORY,
                    token="synthetic-read-token",
                    parent_run_id="45150000001",
                    current_run_id="45150000002",
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

        state = rate_state_with(minute=39)
        ready, reason = continuous.budget_has_next_turn_capacity(state, now=NOW)
        self.assertFalse(ready)
        self.assertEqual(reason, "HARD_RATE_LIMIT_INSUFFICIENT_FOR_DIRECTIONAL_PAIR")

        state = rate_state_with(minute=29)
        ready, reason = continuous.budget_has_next_turn_capacity(state, now=NOW)
        self.assertFalse(ready)
        self.assertEqual(reason, "NORMAL_RATE_LIMIT_INSUFFICIENT_FOR_DIRECTIONAL_PAIR")

        state = rate_state_with(minute=28)
        ready, reason = continuous.budget_has_next_turn_capacity(state, now=NOW)
        self.assertTrue(ready)
        self.assertEqual(reason, "BUDGET_AVAILABLE")

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
