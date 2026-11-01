#!/usr/bin/env python3
"""One-time, fail-closed cleanup of reviewed stale Git branch references."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "tools" / "legacy_branch_ref_cleanup_manifest.json"
EXPECTED_REPOSITORY = "xxlyusufi61-bot/general-super-loto"
PROTECTED_BRANCHES = {
    "main",
    "general-nvidia-rate-limit-state",
    "audit/all-branch-reference-review-20261102",
}
NO_PR_RETIREMENT_ALLOWLIST = {
    "fix/system-agent-role-routing-20261029",
    "nvidia-connection-test-20261029",
    "nvidia-single-inference-20261029-1849",
}
TRIGGER_PULL_REQUEST = 67


class CleanupError(RuntimeError):
    pass


def validate_manifest(manifest, repository):
    if repository != EXPECTED_REPOSITORY:
        raise CleanupError("Unexpected repository; refusing branch cleanup.")
    if manifest.get("repository") != EXPECTED_REPOSITORY:
        raise CleanupError("Manifest repository does not match.")
    if manifest.get("cleanup_trigger_pull_request") != TRIGGER_PULL_REQUEST:
        raise CleanupError("Unexpected cleanup trigger pull request.")
    protected = set(manifest.get("protected_branches", []))
    if not PROTECTED_BRANCHES.issubset(protected):
        raise CleanupError("Manifest omits a required protected branch.")

    targets = manifest.get("targets")
    if not isinstance(targets, list) or len(targets) != 60:
        raise CleanupError("Manifest must contain exactly 60 audited targets.")

    seen = set()
    for item in targets:
        if not isinstance(item, dict):
            raise CleanupError("Invalid manifest entry.")
        branch = item.get("branch")
        sha = item.get("expected_sha")
        if not isinstance(branch, str) or not branch:
            raise CleanupError("Target branch name is missing.")
        if branch in seen:
            raise CleanupError("Duplicate target branch in manifest.")
        seen.add(branch)
        if branch in PROTECTED_BRANCHES or branch == "main":
            raise CleanupError("Manifest attempts to retire a protected branch.")
        if not isinstance(sha, str) or len(sha) != 40 or any(
            ch not in "0123456789abcdef" for ch in sha.lower()
        ):
            raise CleanupError("Invalid expected commit hash.")
        pr_number = item.get("pull_request")
        if pr_number is None:
            if branch not in NO_PR_RETIREMENT_ALLOWLIST:
                raise CleanupError("Unreviewed no-PR branch is not allowlisted.")
            if not isinstance(item.get("no_pr_reason"), str) or not item["no_pr_reason"].strip():
                raise CleanupError("No-PR retirement reason is missing.")
        elif not isinstance(pr_number, int) or pr_number < 1:
            raise CleanupError("Invalid pull request number.")
    return targets


def event_authorizes_cleanup(event, repository):
    if not isinstance(event, dict):
        return False
    pr = event.get("pull_request")
    base = pr.get("base") if isinstance(pr, dict) else None
    return (
        event.get("action") == "closed"
        and event.get("repository", {}).get("full_name") == repository
        and isinstance(pr, dict)
        and pr.get("number") == TRIGGER_PULL_REQUEST
        and pr.get("merged") is True
        and isinstance(base, dict)
        and base.get("ref") == "main"
    )


class GitHubAPI:
    def __init__(self, token, repository):
        self.repository = repository
        self.base_url = "https://api.github.com/repos/" + repository
        self.token = token

    def request_json(self, method, path):
        request = urllib.request.Request(
            self.base_url + path,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "GENERAL-Guarded-Branch-Cleanup",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                raw = response.read(1_000_000)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise CleanupError(
                "GitHub API returned HTTP " + str(exc.code) +
                " for " + method + " " + path
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CleanupError(
                "GitHub API transport failed (" + type(exc).__name__ + "); details suppressed."
            ) from None
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CleanupError("GitHub API returned invalid JSON; details suppressed.") from None

    def get_branch(self, branch):
        encoded = urllib.parse.quote(branch, safe="")
        return self.request_json("GET", "/branches/" + encoded)

    def get_pull_request(self, number):
        return self.request_json("GET", "/pulls/" + str(number))

    def delete_branch(self, branch):
        encoded = urllib.parse.quote(branch, safe="/")
        result = self.request_json("DELETE", "/git/refs/heads/" + encoded)
        return result is not None


def _branch_sha(branch_response):
    if not isinstance(branch_response, dict):
        return None
    commit = branch_response.get("commit")
    return commit.get("sha") if isinstance(commit, dict) else None


def _pull_request_matches(pull, target, repository):
    if not isinstance(pull, dict) or pull.get("state") != "closed":
        return False
    head = pull.get("head")
    if not isinstance(head, dict):
        return False
    head_repo = head.get("repo")
    return (
        head.get("ref") == target["branch"]
        and head.get("sha") == target["expected_sha"]
        and isinstance(head_repo, dict)
        and head_repo.get("full_name") == repository
    )


def cleanup_stale_refs(manifest, api, event, repository):
    if not event_authorizes_cleanup(event, repository):
        raise CleanupError("Cleanup event is not authorized; no branch was changed.")
    targets = validate_manifest(manifest, repository)
    outcomes = []
    prepared = []
    errors = []

    # Preflight every target before performing any deletion.
    for target in targets:
        branch = target["branch"]
        try:
            remote = api.get_branch(branch)
        except CleanupError as exc:
            errors.append({"branch": branch, "status": "preflight_error", "error": str(exc)})
            continue

        if remote is None:
            outcomes.append({"branch": branch, "status": "already_absent"})
            continue
        if _branch_sha(remote) != target["expected_sha"]:
            errors.append({"branch": branch, "status": "sha_changed"})
            continue

        pr_number = target.get("pull_request")
        if pr_number is not None:
            try:
                pull = api.get_pull_request(pr_number)
            except CleanupError as exc:
                errors.append({"branch": branch, "status": "pr_check_error", "error": str(exc)})
                continue
            if not _pull_request_matches(pull, target, repository):
                errors.append({"branch": branch, "status": "pr_not_closed_or_head_mismatch"})
                continue
        prepared.append(target)

    if errors:
        return {
            "status": "aborted_preflight",
            "targets": len(targets),
            "already_absent": sum(row["status"] == "already_absent" for row in outcomes),
            "deleted": 0,
            "errors": errors,
            "outcomes": outcomes,
        }, 1

    # Recheck each reference immediately before deletion to avoid deleting a moved branch.
    deleted = 0
    for target in prepared:
        branch = target["branch"]
        try:
            current = api.get_branch(branch)
            if current is None:
                outcomes.append({"branch": branch, "status": "already_absent"})
                continue
            if _branch_sha(current) != target["expected_sha"]:
                errors.append({"branch": branch, "status": "sha_changed_before_delete"})
                continue
            removed = api.delete_branch(branch)
            if removed:
                deleted += 1
                outcomes.append({"branch": branch, "status": "deleted"})
            else:
                outcomes.append({"branch": branch, "status": "already_absent"})
        except CleanupError as exc:
            errors.append({"branch": branch, "status": "delete_error", "error": str(exc)})
            break

    return {
        "status": "completed" if not errors else "partial_failure",
        "targets": len(targets),
        "deleted": deleted,
        "already_absent": sum(row["status"] == "already_absent" for row in outcomes),
        "errors": errors,
        "outcomes": outcomes,
        "protected_branches": sorted(PROTECTED_BRANCHES),
    }, 1 if errors else 0


def main():
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    event_path = os.environ.get("GITHUB_EVENT_PATH", "").strip()
    if not token or not repository or not event_path:
        print("Required GitHub Actions context is missing; refusing cleanup.", file=sys.stderr)
        return 2
    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        api = GitHubAPI(token, repository)
        result, status = cleanup_stale_refs(manifest, api, event, repository)
    except (OSError, UnicodeError, json.JSONDecodeError, CleanupError) as exc:
        print(json.dumps({"status": "refused", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
