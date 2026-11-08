from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
WORKFLOWS = ROOT / ".github" / "workflows"
ACTIONS = ROOT / ".github" / "actions"


def read(path):
    return path.read_text(encoding="utf-8")


class AutomationStateCutoverWorkflowTests(unittest.TestCase):
    def test_writer_is_scoped_to_the_four_migrated_objects(self):
        writer = read(ROOT / "general_automation_state_writer.py")
        for key in (
            '"archive"',
            '"evaluation_ledger"',
            '"autonomous_research_state"',
            '"research_memory"',
        ):
            self.assertIn(key, writer)
        self.assertIn("NVIDIA lock is not writable here", writer)
        self.assertNotIn('"nvidia_lock",\n', writer.split("WRITABLE_OBJECTS = (", 1)[1].split(")", 1)[0])

    def test_composite_actions_route_through_the_transactional_writer(self):
        hydrate = read(ACTIONS / "hydrate-automation-state" / "action.yml")
        publish = read(ACTIONS / "publish-automation-state" / "action.yml")
        self.assertIn("general_automation_state_writer.py hydrate", hydrate)
        self.assertIn("general_automation_state_writer.py publish", publish)
        self.assertIn("--expected-generation", publish)
        self.assertIn("--objects", publish)
        self.assertIn("GITHUB_OUTPUT", hydrate)
        self.assertIn("GITHUB_OUTPUT", publish)

    def test_all_canonical_runtime_readers_hydrate_the_state_store(self):
        names = (
            "general-autonomous-research.yml",
            "open-research.yml",
            "general-agent.yml",
            "general-coordinator-execute.yml",
            "general-coordinator-plan.yml",
            "walkforward-evaluation.yml",
            "walkforward-test.yml",
            "archive-target-integrity.yml",
            "archive-live-cross-source-audit.yml",
            "nvidia-diagnostic.yml",
        )
        for name in names:
            with self.subTest(workflow=name):
                workflow = read(WORKFLOWS / name)
                self.assertIn("./.github/actions/hydrate-automation-state", workflow)
                self.assertIn("general_automation_state_integrity_audit.py", workflow)

    def test_live_nvidia_workflows_keep_dedicated_lock_checks(self):
        lock_guards = {
            "general-autonomous-research.yml": "python3 general_nvidia_guard.py check",
            "open-research.yml": "python3 general_nvidia_guard.py check",
            "general-agent.yml": "python3 general_nvidia_guard.py check",
            "general-coordinator-execute.yml": "python3 general_nvidia_guard.py check",
            "nvidia-diagnostic.yml": "python3 general_nvidia_guard.py diagnostic-check",
        }
        for name, guard in lock_guards.items():
            with self.subTest(workflow=name):
                workflow = read(WORKFLOWS / name)
                self.assertIn("general_automation_state_integrity_audit.py", workflow)
                self.assertIn(guard, workflow)

    def test_integrity_ci_runs_the_new_policy_neutral_audit_and_tests(self):
        for name in (
            "nvidia-safety-tests.yml",
            "general-automation-state-writer-test.yml",
        ):
            with self.subTest(workflow=name):
                workflow = read(WORKFLOWS / name)
                self.assertIn("general_automation_state_integrity_audit.py", workflow)
                self.assertIn("general_automation_state_integrity_audit_test.py", workflow)

    def test_migration_preflight_keeps_its_locked_and_paused_requirements(self):
        migration_audit = read(ROOT / "general_automation_state_runtime_audit.py")
        self.assertIn('if lock.get("locked") is not True:', migration_audit)
        self.assertIn('authorization.get("enabled") is not False', migration_audit)

    def test_legacy_runtime_audit_is_state_authoritative_by_default(self):
        migration_audit = read(ROOT / "general_automation_state_runtime_audit.py")
        self.assertIn('parser.set_defaults(audit_mode="state-authoritative")', migration_audit)
        self.assertIn('"--pre-cutover-diagnostic"', migration_audit)
        self.assertIn('"authoritative_source": contract.STATE_BRANCH', migration_audit)

    def test_research_workflows_publish_runtime_objects_to_the_state_store(self):
        for name in ("general-autonomous-research.yml", "open-research.yml"):
            with self.subTest(workflow=name):
                workflow = read(WORKFLOWS / name)
                self.assertIn("./.github/actions/publish-automation-state", workflow)
                self.assertNotIn("git add super_loto_archive.json", workflow)
                self.assertNotIn("git add general_evaluation_ledger.json", workflow)
                self.assertNotIn("git add general_research_memory.json", workflow)
                self.assertNotIn("git add general_autonomous_research_state.json", workflow)

    def test_only_immutable_evaluation_snapshots_remain_a_main_branch_data_push(self):
        auto = read(WORKFLOWS / "general-autonomous-research.yml")
        open_research = read(WORKFLOWS / "open-research.yml")
        self.assertEqual(auto.count("git push origin HEAD:main"), 1)
        self.assertEqual(open_research.count("git push origin HEAD:main"), 1)
        for workflow in (auto, open_research):
            self.assertIn("general_evaluation_snapshots/", workflow)
            self.assertIn("git push origin HEAD:main", workflow)
        self.assertIn("git add general_evaluation_snapshots/", auto)
        self.assertIn("git add general_evaluation_snapshots/", open_research)

    def test_nvidia_lock_stays_separate_and_rate_limit_branch_is_not_rewired(self):
        writer = read(ROOT / "general_automation_state_writer.py")
        guard = read(ROOT / "general_nvidia_guard.py")
        self.assertNotIn('"nvidia_lock",', writer.split("WRITABLE_OBJECTS = (", 1)[1].split(")", 1)[0])
        self.assertIn("GENERAL_DEFAULT_BRANCH", guard)
        self.assertIn("general-nvidia-rate-limit-state", guard)
        for name in ("general-autonomous-research.yml", "open-research.yml", "nvidia-diagnostic.yml"):
            workflow = read(WORKFLOWS / name)
            self.assertIn("GENERAL_DEFAULT_BRANCH: main", workflow)


if __name__ == "__main__":
    unittest.main()
