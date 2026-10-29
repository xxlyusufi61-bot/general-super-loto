import unittest

from general_coordinator import build_plan


class GeneralCoordinatorPlanTests(unittest.TestCase):
    def test_generic_system_task_uses_validation_then_debug(self):
        plan = build_plan("mode=system Review the project architecture.")
        self.assertEqual(plan["task_type"], "system")
        self.assertEqual(plan["agent_order"], ["validation", "debug"])
        self.assertTrue(plan["execution_ready"])

    def test_code_task_routes_to_debug_and_validation(self):
        plan = build_plan("mode=code Fix a Python workflow bug and add a test.")
        self.assertEqual(plan["task_type"], "code")
        self.assertEqual(plan["agent_order"], ["debug", "validation"])

    def test_archive_integrity_task_routes_only_to_validation(self):
        plan = build_plan("mode=data target_date=2026-10-20 validate archive integrity")
        self.assertEqual(plan["task_type"], "data")
        self.assertEqual(plan["agent_order"], ["validation"])
        self.assertEqual(plan["target_date"], "2026-10-20")
        self.assertTrue(plan["requires_guarded_archive_preflight"])
        self.assertFalse(plan["execution_ready"])
        self.assertEqual(plan["target_resolution"], "explicit_target_requires_archive_guard_validation")

    def test_formula_research_task_adds_research(self):
        plan = build_plan("mode=data target_date=2026-10-20 research formula patterns")
        self.assertEqual(plan["agent_order"], ["validation", "research"])

    def test_comparison_task_adds_research_and_crossover(self):
        plan = build_plan("mode=data target_date=2026-10-20 compare methods with crossover")
        self.assertEqual(plan["agent_order"], ["validation", "research", "crossover"])

    def test_formula_and_comparison_task_routes_three_agents(self):
        plan = build_plan("mode=data target_date=2026-10-20 research formula and compare methods")
        self.assertEqual(plan["agent_order"], ["validation", "research", "crossover"])
        self.assertLessEqual(plan["agent_count"], 3)

    def test_implicit_data_task_requires_guarded_target_resolution(self):
        plan = build_plan("Analyze Super Loto frequency and formula patterns")
        self.assertEqual(plan["task_type"], "data")
        self.assertIsNone(plan["target_date"])
        self.assertTrue(plan["requires_guarded_archive_preflight"])
        self.assertEqual(
            plan["target_resolution"],
            "resolve_latest_valid_archive_target_before_execution"
        )
        self.assertFalse(plan["execution_ready"])

    def test_explicit_system_mode_overrides_data_words(self):
        plan = build_plan("mode=system Review Super Loto project's workflow architecture.")
        self.assertEqual(plan["task_type"], "system")
        self.assertFalse(plan["requires_guarded_archive_preflight"])

    def test_mode_and_target_conflict_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "conflicts"):
            build_plan("target_date=2026-10-20 mode=system Review the workflow.")

    def test_conflicting_target_dates_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Conflicting target dates"):
            build_plan("target_date=2026-10-20 target_date=2026-10-21 analyze data")

    def test_invalid_target_date_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Target date must"):
            build_plan("target_date=2026-02-30 analyze data")

    def test_empty_task_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Task is empty"):
            build_plan("mode=system")

    def test_planner_never_calls_a_model_or_loads_archive(self):
        plan = build_plan("mode=system inspect architecture")
        self.assertEqual(plan["mode"], "plan_only")
        self.assertEqual(plan["model_requests_made"], 0)
        self.assertFalse(plan["agents_executed"])
        self.assertFalse(plan["archive_loaded_by_planner"])
        self.assertFalse(plan["archive_guard_run_by_planner"])
        self.assertFalse(plan["binding_rules_changed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
