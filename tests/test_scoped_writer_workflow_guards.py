import os
import re
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def job_level_if(workflow_path: str, job_name: str) -> str:
    lines = (ROOT / workflow_path).read_text(encoding="utf-8").splitlines()
    jobs_index = next(
        (index for index, line in enumerate(lines) if line.strip() == "jobs:"),
        None,
    )
    if jobs_index is None:
        raise AssertionError(f"{workflow_path}: missing jobs section")

    job_header = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")
    for index in range(jobs_index + 1, len(lines)):
        match = job_header.match(lines[index])
        if not match or match.group(1) != job_name:
            continue
        for line in lines[index + 1:]:
            if job_header.match(line) or (line and not line.startswith(" ")):
                break
            condition = re.match(r"^    if:\s*(.+)$", line)
            if condition:
                return condition.group(1)
        return ""
    raise AssertionError(f"{workflow_path}: job {job_name!r} not found")


class ScopedWriterWorkflowGuardTests(unittest.TestCase):
    def assert_main_dispatch_gate(self, path: str, job: str) -> str:
        condition = job_level_if(path, job)
        self.assertIn("github.event_name == 'workflow_dispatch'", condition)
        self.assertIn("github.ref == 'refs/heads/main'", condition)
        return condition

    def test_privileged_workflows_require_main_dispatch_at_job_level(self):
        targets = (
            (".github/workflows/general-nvidia-unlock.yml", "unlock"),
            (".github/workflows/general-coordinator-execute.yml", "coordinator"),
            (".github/workflows/nvidia-chat-test.yml", "chat-test"),
            (".github/workflows/nvidia-test.yml", "nvidia-test"),
        )
        for path, job in targets:
            with self.subTest(path=path, job=job):
                self.assert_main_dispatch_gate(path, job)

    def test_manual_unlock_also_requires_explicit_yes(self):
        condition = job_level_if(
            ".github/workflows/general-nvidia-unlock.yml", "unlock"
        )
        self.assertIn("inputs.confirm == 'yes'", condition)

    def test_scoped_writer_token_action_defaults_to_fail_closed(self):
        action = (
            ROOT / ".github/actions/create-scoped-writer-token/action.yml"
        ).read_text(encoding="utf-8")
        gate_index = action.index("Verify scoped writer deployment gate")
        token_index = action.index("uses: actions/create-github-app-token@")
        self.assertLess(gate_index, token_index)
        self.assertIn(
            "WRITERS_VALIDATED: ${{ inputs.writers_validated }}",
            action,
        )
        self.assertIn("default: 'false'", action)
        self.assertIn("inputs.allow_prevalidation_safety_lock", action)
        self.assertIn("inputs.main_writer_client_id", action)
        self.assertIn("github.workflow_ref", action)
        self.assertIn(
            "GENERAL_SCOPED_WRITERS_VALIDATED=true",
            action,
        )

    def run_deployment_gate(self, *, validated=None, allow_lock="false",
                            requested_client="main-app", main_client="main-app",
                            workflow_ref="xxlyusufi61-bot/general-super-loto/.github/workflows/general-nvidia-manual-lock.yml@refs/heads/main"):
        action = (
            ROOT / ".github/actions/create-scoped-writer-token/action.yml"
        ).read_text(encoding="utf-8")
        marker = "    - name: Verify scoped writer deployment gate\n"
        start = action.index(marker)
        run_marker = "      run: |\n"
        run_start = action.index(run_marker, start) + len(run_marker)
        script_lines = []
        for line in action[run_start:].splitlines():
            if line and not line.startswith("        "):
                break
            script_lines.append(line[8:] if line.startswith("        ") else "")
        env = os.environ.copy()
        env["WRITERS_VALIDATED"] = "" if validated is None else validated
        env.update({
            "ALLOW_PREVALIDATION_SAFETY_LOCK": allow_lock,
            "REQUESTED_CLIENT_ID": requested_client,
            "MAIN_WRITER_CLIENT_ID": main_client,
            "WORKFLOW_REF": workflow_ref,
        })
        return subprocess.run(
            ["bash", "-e", "-u", "-o", "pipefail", "-c", "\n".join(script_lines)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_deployment_gate_runtime_defaults_closed_and_accepts_only_validated(self):
        self.assertNotEqual(self.run_deployment_gate().returncode, 0)
        self.assertNotEqual(self.run_deployment_gate(validated="false").returncode, 0)
        self.assertNotEqual(self.run_deployment_gate(validated="TRUE").returncode, 0)
        self.assertEqual(self.run_deployment_gate(validated="true").returncode, 0)

    def test_deployment_gate_runtime_limits_emergency_exception(self):
        allowed = self.run_deployment_gate(allow_lock="true")
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        wrong_app = self.run_deployment_gate(
            allow_lock="true", requested_client="budget-app"
        )
        self.assertNotEqual(wrong_app.returncode, 0)
        wrong_workflow = self.run_deployment_gate(
            allow_lock="true",
            workflow_ref="xxlyusufi61-bot/general-super-loto/.github/workflows/general-autonomous-research.yml@refs/heads/feature",
        )
        self.assertNotEqual(wrong_workflow.returncode, 0)
        exception_disabled = self.run_deployment_gate(allow_lock="false")
        self.assertNotEqual(exception_disabled.returncode, 0)

    def test_prevalidation_exception_is_limited_to_emergency_main_writer_locks(self):
        expected = {
            ".github/workflows/general-agent.yml": {
                "Create short-lived main writer token for safety-lock persistence"
            },
            ".github/workflows/general-autonomous-research.yml": {
                "Create short-lived main writer token for safety-lock persistence"
            },
            ".github/workflows/general-coordinator-execute.yml": {
                "Create short-lived main writer token for coordinator safety-lock persistence"
            },
            ".github/workflows/general-nvidia-manual-lock.yml": {
                "Create short-lived main writer token for manual safety-lock activation"
            },
            ".github/workflows/nvidia-chat-test.yml": {
                "Create short-lived main writer token for safety-lock persistence"
            },
            ".github/workflows/nvidia-diagnostic.yml": {
                "Create short-lived main writer token for safety-lock persistence"
            },
            ".github/workflows/nvidia-test.yml": {
                "Create short-lived main writer token for safety-lock persistence"
            },
            ".github/workflows/open-research.yml": {
                "Create short-lived main writer token for failed research safety lock"
            },
        }
        found = {}
        for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
            lines = workflow.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                if "allow_prevalidation_safety_lock: 'true'" not in line:
                    continue
                start = index
                while start >= 0 and not lines[start].strip().startswith("- name:"):
                    start -= 1
                self.assertGreaterEqual(start, 0, str(workflow))
                end = index + 1
                while end < len(lines) and not lines[end].strip().startswith("- name:"):
                    end += 1
                block = lines[start:end]
                step_name = lines[start].split("- name:", 1)[1].strip()
                self.assertTrue(
                    any(
                        "client_id: ${{ vars.GENERAL_MAIN_WRITER_APP_CLIENT_ID }}"
                        in item
                        for item in block
                    ),
                    f"{workflow}: pre-validation exception must use Main Writer",
                )
                relative = workflow.relative_to(ROOT).as_posix()
                found.setdefault(relative, set()).add(step_name)
        self.assertEqual(found, expected)

    def test_publish_automation_state_forwards_scoped_writer_gate_to_all_live_callers(self):
        action = (
            ROOT / ".github/actions/publish-automation-state/action.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("  writers_validated:", action)
        self.assertIn("    required: false", action)
        self.assertIn("    default: 'false'", action)
        self.assertIn(
            "writers_validated: ${{ inputs.writers_validated }}",
            action,
        )

        expected_callers = {
            ".github/workflows/general-agent.yml",
            ".github/workflows/general-autonomous-research.yml",
            ".github/workflows/general-coordinator-execute.yml",
            ".github/workflows/general-target-admission-maintenance.yml",
            ".github/workflows/open-research.yml",
        }
        disabled_legacy_callers = {
            ".github/workflows/general-recovery-20261105.yml",
            ".github/workflows/retire-exhausted-fifo-target.yml",
        }
        found_callers = set()
        for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
            relative = workflow.relative_to(ROOT).as_posix()
            lines = workflow.read_text(encoding="utf-8").splitlines()
            for index, line in enumerate(lines):
                if line.strip() != "uses: ./.github/actions/publish-automation-state":
                    continue
                start = index
                while start >= 0 and not lines[start].startswith("      - "):
                    start -= 1
                self.assertGreaterEqual(start, 0, f"{relative}: publish step has no step header")
                end = index + 1
                while end < len(lines) and not lines[end].startswith("      - "):
                    end += 1
                step = " ".join(lines[start:end])
                if relative in disabled_legacy_callers:
                    continue
                self.assertIn(
                    "writers_validated: ${{ vars.GENERAL_SCOPED_WRITERS_VALIDATED }}",
                    step,
                    f"{relative}: live publish step must pass the repository writer gate",
                )
                found_callers.add(relative)

        self.assertEqual(found_callers, expected_callers)

    def test_direct_app_token_minting_is_reserved_for_read_only_validation(self):
        validation_workflow = (
            ROOT / ".github/workflows/general-github-app-read-only-validation.yml"
        )
        direct_mints = []
        for workflow in sorted((ROOT / ".github/workflows").glob("*.yml")):
            if "uses: actions/create-github-app-token@" in workflow.read_text(
                encoding="utf-8"
            ):
                direct_mints.append(workflow)
        self.assertEqual(direct_mints, [validation_workflow])

    def load_yaml_jobs_without_duplicate_keys(self, relative: str) -> dict:
        # Use the same Psych YAML parser family available on GitHub's ubuntu runners.
        # The AST pass rejects duplicate mapping keys before loading the effective job.
        ruby = r"""
require "yaml"
require "json"

def inspect_node(node, path, duplicates)
  if node.is_a?(Psych::Nodes::Mapping)
    seen = {}
    node.children.each_slice(2) do |key, value|
      if key.is_a?(Psych::Nodes::Scalar)
        name = key.value
        duplicates << "#{path}.#{name}" if seen.key?(name)
        seen[name] = true
        inspect_node(value, "#{path}.#{name}", duplicates)
      else
        inspect_node(key, path, duplicates)
        inspect_node(value, path, duplicates)
      end
    end
  elsif node.respond_to?(:children) && node.children
    node.children.each_with_index do |child, index|
      inspect_node(child, "#{path}[#{index}]", duplicates)
    end
  end
end

path = ARGV.fetch(0)
ast = YAML.parse_file(path)
duplicates = []
inspect_node(ast, "$", duplicates)
abort "duplicate YAML mapping keys: #{duplicates.join(', ')}" unless duplicates.empty?
data = YAML.load_file(path)
jobs = data.fetch("jobs")
puts JSON.generate({"jobs" => jobs.transform_values { |job| job.is_a?(Hash) ? job["if"] : nil }, "permissions" => data.fetch("permissions")})
"""
        result = subprocess.run(
            ["ruby", "-e", ruby, str(ROOT / relative)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"{relative}: YAML must parse without duplicate keys: {result.stderr}",
        )
        import json
        return json.loads(result.stdout)

    def test_legacy_unscoped_writer_workflows_are_structurally_disabled(self):
        targets = (
            (".github/workflows/general-recovery-20261105.yml", "recovery"),
            (".github/workflows/retire-exhausted-fifo-target.yml", "retire-exhausted-target"),
        )
        for relative, job_name in targets:
            with self.subTest(workflow=relative):
                parsed = self.load_yaml_jobs_without_duplicate_keys(relative)
                jobs = parsed["jobs"]
                self.assertIn(job_name, jobs)
                self.assertEqual(
                    jobs[job_name],
                    "${{ false }}",
                    "legacy writer job must remain unconditionally disabled",
                )
                self.assertEqual(parsed["permissions"]["contents"], "read")
                workflow = (ROOT / relative).read_text(encoding="utf-8")
                self.assertNotIn("contents: write", workflow)

    def test_scheduled_autonomous_writer_jobs_require_validation_flag(self):
        for job in ("archive-refresh", "scheduled-cycle"):
            with self.subTest(job=job):
                condition = job_level_if(
                    ".github/workflows/general-autonomous-research.yml", job
                )
                self.assertIn(
                    "vars.GENERAL_SCOPED_WRITERS_VALIDATED == 'true'",
                    condition,
                )

    def test_post_merge_token_validation_remains_available_before_gate_enablement(self):
        workflow = (
            ROOT / ".github/workflows/general-github-app-read-only-validation.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("GET /installation/repositories", workflow)
        self.assertNotIn("GENERAL_SCOPED_WRITERS_VALIDATED", workflow)

    def test_budget_migration_requires_explicit_ruleset_bypass_confirmation(self):
        workflow = (
            ROOT / ".github/workflows/general-nvidia-budget-migration.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("ruleset_bypass_confirmed:", workflow)
        self.assertIn("default: \"NO\"", workflow)
        self.assertIn("RULESET_BYPASS_CONFIRMED: ${{ inputs.ruleset_bypass_confirmed }}", workflow)
        self.assertIn('if [ "$RULESET_BYPASS_CONFIRMED" != "YES" ]', workflow)
        self.assertIn("Integration ID 5861647, bypass mode always", workflow)
        preflight = (ROOT / "general_nvidia_writer_preflight.py").read_text(encoding="utf-8")
        self.assertIn("EXPECTED_APP_ID = 5861647", preflight)
        self.assertNotIn('ruleset.get("bypass_actors")', preflight)

    def test_single_request_checkout_uses_one_with_mapping(self):
        path = ROOT / ".github/workflows/general-single-request-normal-token-test.yml"
        lines = path.read_text(encoding="utf-8").splitlines()
        start = lines.index("      - name: Checkout exact main workflow commit")
        step = []
        for line in lines[start + 1:]:
            if line.startswith("      - "):
                break
            step.append(line)

        with_lines = [line for line in step if re.match(r"^        with:\s*$", line)]
        self.assertEqual(len(with_lines), 1, "checkout step must contain exactly one with: mapping")
        self.assertTrue(any("persist-credentials: false" in line for line in step))
        self.assertTrue(any("ref: ${{ github.sha }}" in line for line in step))
        self.assertTrue(any("fetch-depth: 0" in line for line in step))


if __name__ == "__main__":
    unittest.main()
