#!/usr/bin/env python3
"""GET-only reader for GENERAL's isolated automation-state branch.

Reads are pinned to one commit and the branch head is checked again before
returning. Manifest, tree entries, Git blob IDs, SHA-256 digests and byte
lengths are verified. This module never writes state or calls NVIDIA.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import json
import hashlib
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import general_automation_state as contract

API_ROOT = "https://api.github.com"
USER_AGENT = "GENERAL-Automation-State-ReadOnly/1.0"
MAX_API_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_OBJECT_BYTES = 8 * 1024 * 1024
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class StateStoreReadError(RuntimeError):
    """Raised when the remote state store cannot be verified safely."""


def _validate_repository(repository: str) -> str:
    if not isinstance(repository, str) or not REPOSITORY_PATTERN.fullmatch(repository):
        raise StateStoreReadError("repository must use the owner/repository form")
    return repository


class GitHubStateStoreReader:
    """Fetch and validate a consistent snapshot from the fixed state branch."""

    def __init__(self, repository: str, token: str | None = None, *, timeout: float = 10.0):
        self.repository = _validate_repository(repository)
        if token is not None and not isinstance(token, str):
            raise StateStoreReadError("GitHub token must be text")
        self.token = token.strip() if token else None
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise StateStoreReadError("timeout must be a positive number")
        self.timeout = float(timeout)

    def _get_json(self, route: str) -> Any:
        """Perform one bounded HTTPS GET; no write-capable transport is exposed."""
        if not isinstance(route, str) or route.startswith("/") or "://" in route:
            raise StateStoreReadError("invalid GitHub API route")
        request = urllib.request.Request(
            API_ROOT + "/" + route,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": USER_AGENT,
                **({"Authorization": "Bearer " + self.token} if self.token else {}),
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if getattr(response, "status", 200) != 200:
                    raise StateStoreReadError("GitHub API returned a non-success status")
                raw = response.read(MAX_API_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise StateStoreReadError(
                "GitHub API read failed with HTTP " + str(exc.code)
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise StateStoreReadError("GitHub API read failed; no state was accepted") from None
        if len(raw) > MAX_API_RESPONSE_BYTES:
            raise StateStoreReadError("GitHub API response exceeded the safety size limit")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise StateStoreReadError("GitHub API returned invalid JSON") from None
        if not isinstance(value, dict):
            raise StateStoreReadError("GitHub API response root must be an object")
        return value

    def _branch_head(self, branch_name: str = contract.STATE_BRANCH) -> tuple[str, str]:
        if branch_name not in {contract.STATE_BRANCH, "main"}:
            raise StateStoreReadError("branch is not approved for a read-only comparison")
        owner, name = self.repository.split("/", 1)
        route = (
            "repos/" + urllib.parse.quote(owner, safe="")
            + "/" + urllib.parse.quote(name, safe="")
            + "/branches/" + urllib.parse.quote(branch_name, safe="")
        )
        value = self._get_json(route)
        if value.get("name") != branch_name:
            raise StateStoreReadError("GitHub returned the wrong branch")
        commit = value.get("commit")
        commit_data = commit.get("commit") if isinstance(commit, dict) else None
        tree = commit_data.get("tree") if isinstance(commit_data, dict) else None
        commit_sha = commit.get("sha") if isinstance(commit, dict) else None
        tree_sha = tree.get("sha") if isinstance(tree, dict) else None
        if not isinstance(commit_sha, str) or not contract.SHA1_RE.fullmatch(commit_sha):
            raise StateStoreReadError("branch commit SHA is invalid")
        if not isinstance(tree_sha, str) or not contract.SHA1_RE.fullmatch(tree_sha):
            raise StateStoreReadError("branch tree SHA is invalid")
        return commit_sha, tree_sha

    def _blob_text(self, blob_sha: str) -> str:
        if not isinstance(blob_sha, str) or not contract.SHA1_RE.fullmatch(blob_sha):
            raise StateStoreReadError("requested Git blob SHA is invalid")
        owner, name = self.repository.split("/", 1)
        route = (
            "repos/" + urllib.parse.quote(owner, safe="")
            + "/" + urllib.parse.quote(name, safe="")
            + "/git/blobs/" + blob_sha
        )
        value = self._get_json(route)
        if value.get("sha") != blob_sha or value.get("encoding") != "base64":
            raise StateStoreReadError("Git blob identity or encoding mismatch")
        size = value.get("size")
        if type(size) is not int or size < 0 or size > MAX_OBJECT_BYTES:
            raise StateStoreReadError("Git blob size is invalid or exceeds the safety limit")
        encoded = value.get("content")
        if not isinstance(encoded, str):
            raise StateStoreReadError("Git blob content is missing")
        try:
            raw = base64.b64decode("".join(encoded.split()), validate=True)
            content = raw.decode("utf-8")
        except (binascii.Error, UnicodeError, ValueError):
            raise StateStoreReadError("Git blob is not valid base64-encoded UTF-8") from None
        if len(raw) != size:
            raise StateStoreReadError("Git blob byte length does not match GitHub metadata")
        if contract.git_blob_sha(content) != blob_sha:
            raise StateStoreReadError("Git blob content does not match its Git SHA")
        return content

    def read_snapshot(self) -> dict[str, Any]:
        """Return verified documents from one immutable, rechecked branch head."""
        commit_sha, tree_sha = self._branch_head()
        owner, name = self.repository.split("/", 1)
        route = (
            "repos/" + urllib.parse.quote(owner, safe="")
            + "/" + urllib.parse.quote(name, safe="")
            + "/git/trees/" + tree_sha + "?recursive=1"
        )
        tree = self._get_json(route)
        if tree.get("sha") != tree_sha or tree.get("truncated") is not False:
            raise StateStoreReadError("state branch tree identity is invalid or truncated")
        raw_entries = tree.get("tree")
        if not isinstance(raw_entries, list) or len(raw_entries) > 10000:
            raise StateStoreReadError("state branch tree is missing or exceeds its entry limit")

        allowed_paths = {
            "README.md",
            contract.MANIFEST_PATH,
            *contract.STATE_OBJECTS.values(),
        }
        files: dict[str, dict[str, str]] = {}
        for item in raw_entries:
            if not isinstance(item, dict):
                raise StateStoreReadError("state branch tree contains a malformed entry")
            path = item.get("path")
            if not isinstance(path, str) or path not in allowed_paths:
                raise StateStoreReadError("state branch contains a path outside its allowlist")
            if path in files:
                raise StateStoreReadError("state branch tree contains a duplicate path")
            if item.get("type") != "blob" or item.get("mode") != "100644":
                raise StateStoreReadError("state branch contains a non-regular file")
            sha = item.get("sha")
            if not isinstance(sha, str) or not contract.SHA1_RE.fullmatch(sha):
                raise StateStoreReadError("state branch contains an invalid file SHA")
            files[path] = {"sha": sha}

        if "README.md" not in files or contract.MANIFEST_PATH not in files:
            raise StateStoreReadError("state branch is missing its README or manifest")

        manifest_sha = files[contract.MANIFEST_PATH]["sha"]
        manifest_text = self._blob_text(manifest_sha)
        manifest = contract.validate_manifest(contract._strict_json_loads(manifest_text))

        indexed_paths = {entry["path"] for entry in manifest["entries"].values()}
        actual_state_paths = set(files) & set(contract.STATE_OBJECTS.values())
        if indexed_paths != actual_state_paths:
            raise StateStoreReadError(
                "manifest entries do not exactly match the state files in the branch tree"
            )

        documents: dict[str, dict[str, Any]] = {}
        for object_key, entry in manifest["entries"].items():
            path = contract.STATE_OBJECTS[object_key]
            blob_sha = files[path]["sha"]
            if blob_sha != entry["blob_sha"]:
                raise StateStoreReadError("manifest blob SHA does not match the branch tree")
            content = self._blob_text(blob_sha)
            contract.verify_object_content(object_key, entry, content)
            documents[object_key] = contract.validate_state_document(object_key, content)

        final_commit_sha, _ = self._branch_head()
        if final_commit_sha != commit_sha:
            raise StateStoreReadError("state branch moved during the read; snapshot discarded")

        return {
            "branch": contract.STATE_BRANCH,
            "commit_sha": commit_sha,
            "manifest": manifest,
            "documents": documents,
            "verified_objects": sorted(documents),
            "read_only": True,
        }



    def read_shadow_comparison(self) -> dict[str, Any]:
        """Return a read-only legacy parity diagnostic against the state branch.

        The state branch remains authoritative. Copies on main are legacy
        comparison snapshots only; this diagnostic never promotes them to an
        operational source or repairs either branch. Missing registrations and
        divergences are reported without changing either source.
        """
        state_snapshot = self.read_snapshot()
        main_commit_sha, main_tree_sha = self._branch_head("main")
        owner, name = self.repository.split("/", 1)
        route = (
            "repos/" + urllib.parse.quote(owner, safe="")
            + "/" + urllib.parse.quote(name, safe="")
            + "/git/trees/" + main_tree_sha + "?recursive=1"
        )
        tree = self._get_json(route)
        if tree.get("sha") != main_tree_sha or tree.get("truncated") is not False:
            raise StateStoreReadError("main branch tree identity is invalid or truncated")
        raw_entries = tree.get("tree")
        if not isinstance(raw_entries, list) or len(raw_entries) > 10000:
            raise StateStoreReadError("main branch tree is missing or exceeds its entry limit")

        required_paths = set(contract.STATE_OBJECTS.values())
        main_files: dict[str, str] = {}
        for item in raw_entries:
            if not isinstance(item, dict):
                raise StateStoreReadError("main branch tree contains a malformed entry")
            path = item.get("path")
            if not isinstance(path, str):
                raise StateStoreReadError("main branch tree contains a non-text path")
            if path not in required_paths:
                continue
            if path in main_files:
                raise StateStoreReadError("main branch contains a duplicate state-object path")
            if item.get("type") != "blob" or item.get("mode") != "100644":
                raise StateStoreReadError("main state object is not a regular file")
            blob_sha = item.get("sha")
            if not isinstance(blob_sha, str) or not contract.SHA1_RE.fullmatch(blob_sha):
                raise StateStoreReadError("main state object has an invalid Git blob SHA")
            main_files[path] = blob_sha

        missing_paths = required_paths - set(main_files)
        if missing_paths:
            raise StateStoreReadError("main branch is missing a required persistent state object")

        comparisons: dict[str, str] = {}
        for object_key, path in contract.STATE_OBJECTS.items():
            main_blob_sha = main_files[path]
            main_content = self._blob_text(main_blob_sha)
            contract.validate_state_document(object_key, main_content)
            encoded = main_content.encode("utf-8")
            entry = state_snapshot["manifest"]["entries"].get(object_key)
            if entry is None:
                comparisons[object_key] = "NOT_REGISTERED"
                continue
            same_content = (
                entry["blob_sha"] == main_blob_sha
                and entry["content_sha256"] == hashlib.sha256(encoded).hexdigest()
                and entry["byte_length"] == len(encoded)
            )
            comparisons[object_key] = "MATCH" if same_content else "DIVERGED"

        final_main_sha, _ = self._branch_head("main")
        if final_main_sha != main_commit_sha:
            raise StateStoreReadError("main branch moved during the comparison; result discarded")
        final_state_sha, _ = self._branch_head(contract.STATE_BRANCH)
        if final_state_sha != state_snapshot["commit_sha"]:
            raise StateStoreReadError("state branch moved during the comparison; result discarded")

        diverged_count = sum(value == "DIVERGED" for value in comparisons.values())
        matched_count = sum(value == "MATCH" for value in comparisons.values())
        not_registered_count = sum(value == "NOT_REGISTERED" for value in comparisons.values())
        return {
            "mode": "SHADOW_COMPARE",
            "main_commit_sha": main_commit_sha,
            "state_store_commit_sha": state_snapshot["commit_sha"],
            "state_store_status": state_snapshot["manifest"]["status"],
            "state_store_generation": state_snapshot["manifest"]["generation"],
            "comparisons": comparisons,
            "matched_count": matched_count,
            "not_registered_count": not_registered_count,
            "diverged_count": diverged_count,
            "diverged": diverged_count > 0,
            "authoritative_source": contract.STATE_BRANCH,
            "comparison_source": "main_legacy_snapshot",
            "read_only": True,
        }

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="GitHub owner/repository (defaults to GITHUB_REPOSITORY)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN"),
        help="Optional read-only GitHub token (defaults to GITHUB_TOKEN)",
    )
    parser.add_argument(
        "--shadow-compare",
        action="store_true",
        help="Compare registered state-store objects against main without changing either branch",
    )
    args = parser.parse_args(argv)
    if not args.repository:
        parser.error("--repository or GITHUB_REPOSITORY is required")
    reader = GitHubStateStoreReader(args.repository, args.token)
    try:
        if args.shadow_compare:
            comparison = reader.read_shadow_comparison()
            print(json.dumps(comparison, sort_keys=True))
            return 2 if comparison["diverged"] else 0
        snapshot = reader.read_snapshot()
    except (StateStoreReadError, contract.AutomationStateError) as exc:
        print("STATE STORE READ FAILED:", type(exc).__name__, str(exc), file=sys.stderr)
        return 1
    print(json.dumps({
        "branch": snapshot["branch"],
        "commit_sha": snapshot["commit_sha"],
        "status": snapshot["manifest"]["status"],
        "generation": snapshot["manifest"]["generation"],
        "verified_objects": snapshot["verified_objects"],
        "read_only": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
