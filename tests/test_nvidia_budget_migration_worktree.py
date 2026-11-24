"""Regression tests for the NVIDIA budget migration worktree boundary."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "general-nvidia-budget-migration.yml"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


class NvidiaBudgetMigrationWorktreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_uses_detached_worktree_without_switching_primary_checkout(self):
        self.assertIn(
            'git worktree add --detach "$BUDGET_WORKTREE" "$BASE_SHA"',
            self.workflow,
        )
        self.assertNotRegex(self.workflow, r"(?m)^\s*git switch\b")
        self.assertIn(
            'test "$(git -C "$GITHUB_WORKSPACE" branch --show-current)" = "main"',
            self.workflow,
        )
        self.assertIn(
            'test -f "$GITHUB_WORKSPACE/.github/actions/create-scoped-writer-token/action.yml"',
            self.workflow,
        )

    def test_validation_and_persistence_are_executed_inside_worktree(self):
        validation = self.workflow.split(
            "- name: Validate and convert the pinned ledger using trusted main-branch code",
            1,
        )[1].split("\n      - name:", 1)[0]
        persistence = self.workflow.split(
            "- name: Persist V3 ledger with the NVIDIA Budget Writer only",
            1,
        )[1].split("\n      - name:", 1)[0]
        self.assertIn('cd "$BUDGET_WORKTREE"', validation)
        self.assertIn('cd "$BUDGET_WORKTREE"', persistence)
        self.assertIn("git diff --check", validation)
        self.assertIn("git add -- general_nvidia_rate_limit.json", persistence)

    def test_detached_worktree_keeps_main_checkout_and_composite_action_available(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            worktree = Path(temporary) / "budget-worktree"
            root.mkdir()
            git(root, "init", "--initial-branch=main")
            git(root, "config", "user.name", "Regression Test")
            git(root, "config", "user.email", "regression@example.invalid")

            action = root / ".github" / "actions" / "create-scoped-writer-token"
            action.mkdir(parents=True)
            (action / "action.yml").write_text("name: test-action\n", encoding="utf-8")
            (root / "main.py").write_text("trusted main source\n", encoding="utf-8")
            git(root, "add", ".")
            git(root, "commit", "-m", "main source")
            git(root, "branch", "budget-state")
            git(root, "switch", "budget-state")
            (root / "general_nvidia_rate_limit.json").write_text(
                '{"schema_version": 2}\n', encoding="utf-8"
            )
            git(root, "add", "general_nvidia_rate_limit.json")
            git(root, "commit", "-m", "budget ledger")
            budget_sha = git(root, "rev-parse", "HEAD")
            git(root, "switch", "main")

            git(root, "worktree", "add", "--detach", str(worktree), budget_sha)

            self.assertEqual(git(root, "branch", "--show-current"), "main")
            self.assertTrue((root / ".github/actions/create-scoped-writer-token/action.yml").is_file())
            self.assertEqual(git(worktree, "rev-parse", "HEAD"), budget_sha)
            self.assertTrue((worktree / "general_nvidia_rate_limit.json").is_file())
            self.assertFalse((root / "general_nvidia_rate_limit.json").exists())

            git(root, "worktree", "remove", "--force", str(worktree))


if __name__ == "__main__":
    unittest.main()
