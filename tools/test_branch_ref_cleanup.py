import copy
import unittest

from branch_ref_cleanup import (
    CleanupError,
    cleanup_stale_refs,
    validate_manifest,
)

REPOSITORY = "xxlyusufi61-bot/general-super-loto"
PROTECTED = [
    "main",
    "general-nvidia-rate-limit-state",
    "audit/all-branch-reference-review-20261102",
]
SHA = "a" * 40
BRANCH = "old/closed-branch"


def valid_manifest(targets=None):
    if targets is None:
        targets = [{
            "branch": BRANCH,
            "expected_sha": SHA,
            "pull_request": 19,
        }]
    return {
        "schema_version": 1,
        "repository": REPOSITORY,
        "cleanup_trigger_pull_request": 67,
        "protected_branches": PROTECTED,
        "targets": targets if len(targets) == 60 else targets + [
            {
                "branch": f"filler/branch-{i:02d}",
                "expected_sha": f"{i:040x}",
                "pull_request": 100 + i,
            }
            for i in range(60 - len(targets))
        ],
    }


def authorized_event():
    return {
        "action": "closed",
        "repository": {"full_name": REPOSITORY},
        "pull_request": {
            "number": 67,
            "merged": True,
            "base": {"ref": "main"},
        },
    }


def pull_request(branch=BRANCH, sha=SHA, state="closed"):
    return {
        "state": state,
        "head": {
            "ref": branch,
            "sha": sha,
            "repo": {"full_name": REPOSITORY},
        },
    }


class FakeAPI:
    def __init__(self, branches=None, pulls=None, branch_sequences=None):
        self.branches = branches or {}
        self.pulls = pulls or {}
        self.branch_sequences = branch_sequences or {}
        self.deleted = []
        self.branch_calls = {}

    def get_branch(self, branch):
        sequence = self.branch_sequences.get(branch)
        if sequence:
            index = self.branch_calls.get(branch, 0)
            self.branch_calls[branch] = index + 1
            value = sequence[min(index, len(sequence) - 1)]
            return copy.deepcopy(value)
        return copy.deepcopy(self.branches.get(branch))

    def get_pull_request(self, number):
        return copy.deepcopy(self.pulls.get(number))

    def delete_branch(self, branch):
        self.deleted.append(branch)
        self.branches.pop(branch, None)
        return True


class BranchRefCleanupTests(unittest.TestCase):
    def test_rejects_unapproved_event_before_api_calls(self):
        api = FakeAPI()
        event = authorized_event()
        event["pull_request"]["merged"] = False
        with self.assertRaises(CleanupError):
            cleanup_stale_refs(valid_manifest(), api, event, REPOSITORY)
        self.assertEqual(api.deleted, [])
        self.assertEqual(api.branch_calls, {})

    def test_manifest_cannot_target_protected_branch(self):
        manifest = valid_manifest([{
            "branch": "general-nvidia-rate-limit-state",
            "expected_sha": SHA,
            "pull_request": 19,
        }])
        with self.assertRaises(CleanupError):
            validate_manifest(manifest, REPOSITORY)

    def test_manifest_rejects_unreviewed_no_pr_branch(self):
        manifest = valid_manifest([{
            "branch": "unknown/no-pr-branch",
            "expected_sha": SHA,
            "pull_request": None,
            "no_pr_reason": "not approved",
        }])
        with self.assertRaises(CleanupError):
            validate_manifest(manifest, REPOSITORY)

    def test_deletes_only_exact_sha_with_closed_pr(self):
        manifest = valid_manifest()
        api = FakeAPI(
            branches={BRANCH: {"commit": {"sha": SHA}}},
            pulls={19: pull_request()},
        )
        result, status = cleanup_stale_refs(manifest, api, authorized_event(), REPOSITORY)
        self.assertEqual(status, 0)
        self.assertEqual(api.deleted, [BRANCH])
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["status"], "completed")

    def test_sha_mismatch_aborts_before_any_deletion(self):
        changed = "b" * 40
        manifest = valid_manifest([
            {"branch": BRANCH, "expected_sha": SHA, "pull_request": 19},
            {"branch": "old/changed-branch", "expected_sha": "c" * 40, "pull_request": 20},
        ])
        api = FakeAPI(
            branches={
                BRANCH: {"commit": {"sha": SHA}},
                "old/changed-branch": {"commit": {"sha": changed}},
            },
            pulls={
                19: pull_request(),
                20: pull_request("old/changed-branch", "c" * 40),
            },
        )
        result, status = cleanup_stale_refs(manifest, api, authorized_event(), REPOSITORY)
        self.assertEqual(status, 1)
        self.assertEqual(api.deleted, [])
        self.assertEqual(result["status"], "aborted_preflight")

    def test_open_pr_aborts_before_any_deletion(self):
        manifest = valid_manifest([
            {"branch": BRANCH, "expected_sha": SHA, "pull_request": 19},
            {"branch": "old/open-branch", "expected_sha": "c" * 40, "pull_request": 20},
        ])
        api = FakeAPI(
            branches={
                BRANCH: {"commit": {"sha": SHA}},
                "old/open-branch": {"commit": {"sha": "c" * 40}},
            },
            pulls={
                19: pull_request(),
                20: pull_request("old/open-branch", "c" * 40, state="open"),
            },
        )
        result, status = cleanup_stale_refs(manifest, api, authorized_event(), REPOSITORY)
        self.assertEqual(status, 1)
        self.assertEqual(api.deleted, [])
        self.assertEqual(result["status"], "aborted_preflight")

    def test_no_pr_branch_requires_explicit_allowlist(self):
        allowed = "nvidia-connection-test-20261029"
        sha = "d" * 40
        manifest = valid_manifest([{
            "branch": allowed,
            "expected_sha": sha,
            "pull_request": None,
            "no_pr_reason": "retired one-shot diagnostic",
        }])
        api = FakeAPI(branches={allowed: {"commit": {"sha": sha}}})
        result, status = cleanup_stale_refs(manifest, api, authorized_event(), REPOSITORY)
        self.assertEqual(status, 0)
        self.assertEqual(api.deleted, [allowed])
        self.assertEqual(result["deleted"], 1)

    def test_already_absent_branch_is_idempotent(self):
        manifest = valid_manifest()
        api = FakeAPI(pulls={19: pull_request()})
        result, status = cleanup_stale_refs(manifest, api, authorized_event(), REPOSITORY)
        self.assertEqual(status, 0)
        self.assertEqual(api.deleted, [])
        self.assertEqual(result["already_absent"], 60)

    def test_rechecks_sha_immediately_before_deletion(self):
        changed = {"commit": {"sha": "e" * 40}}
        stable = {"commit": {"sha": SHA}}
        manifest = valid_manifest()
        api = FakeAPI(
            branches={BRANCH: stable},
            pulls={19: pull_request()},
            branch_sequences={BRANCH: [stable, changed]},
        )
        result, status = cleanup_stale_refs(manifest, api, authorized_event(), REPOSITORY)
        self.assertEqual(status, 1)
        self.assertEqual(api.deleted, [])
        self.assertEqual(result["status"], "partial_failure")


if __name__ == "__main__":
    unittest.main()
