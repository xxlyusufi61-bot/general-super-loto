import re
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
