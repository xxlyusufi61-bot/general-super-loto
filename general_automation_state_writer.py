#!/usr/bin/env python3
"""Transactional writer for GENERAL's isolated automation-state branch.

Only the four migrated runtime objects are writable here. The NVIDIA lock
remains on main until its readers and writer are migrated in one separate,
explicitly reviewed change. This module never calls NVIDIA.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import general_automation_state as contract
from general_automation_state_reader import (
    GitHubStateStoreReader,
    StateStoreReadError,
)

WRITABLE_OBJECTS = (
    "archive",
    "evaluation_ledger",
    "autonomous_research_state",
    "research_memory",
)
USER_AGENT = "GENERAL-Automation-State-Writer/1.0"
MAX_API_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_OBJECT_BYTES = 8 * 1024 * 1024
API_ROOT = "https://api.github.com"


class StateStoreWriteError(RuntimeError):
    """A state-store write was rejected or could not be verified."""


class StateStoreWriteConflict(StateStoreWriteError):
    """The state branch or manifest changed since the caller's snapshot."""


class StateStoreWriteOutcomeUnknown(StateStoreWriteError):
    """The ref update may have succeeded but its result could not be verified."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_repository(repository: str) -> str:
    if not isinstance(repository, str) or repository.count("/") != 1:
        raise StateStoreWriteError("repository must use owner/repository form")
    owner, name = repository.split("/", 1)
    if not owner or not name or any(ch.isspace() for ch in repository):
        raise StateStoreWriteError("repository must use owner/repository form")
    return repository


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, indent=2, allow_nan=False
    ) + "\n"


def validate_domain_documents(
    documents: dict[str, dict[str, Any]],
) -> None:
    """Run the existing domain validators before any remote write."""
    if not isinstance(documents, dict) or set(documents) != set(WRITABLE_OBJECTS):
        raise StateStoreWriteError(
            "state snapshot must contain exactly the four migrated runtime objects"
        )

    with tempfile.TemporaryDirectory(prefix="general-state-validation-") as temp:
        root = Path(temp)
        for key in WRITABLE_OBJECTS:
            value = documents[key]
            if not isinstance(value, dict):
                raise StateStoreWriteError("state object root must be a JSON object")
            content = _canonical_json(value)
            if len(content.encode("utf-8")) > MAX_OBJECT_BYTES:
                raise StateStoreWriteError("state object exceeds the configured size limit")
            (root / contract.STATE_OBJECTS[key]).write_text(
                content, encoding="utf-8"
            )

        try:
            import general_automation_state_sync as sync
            import general_autonomous_research as autonomous
            import general_research_memory as research_memory
            import general_sealed_evaluation as sealed

            sync._validate_archive(root, documents["archive"])
            sealed.load_ledger(root / contract.STATE_OBJECTS["evaluation_ledger"])
            autonomous.validate_state(documents["autonomous_research_state"])
            research_memory.load_memory(root / contract.STATE_OBJECTS["research_memory"])
        except Exception as exc:
            raise StateStoreWriteError(
                "one or more state objects failed their existing domain validators: "
                + type(exc).__name__
            ) from None


class GitHubStateStoreWriter:
    """Publish a batch as one Git commit using generation and fast-forward guards."""

    def __init__(
        self,
        repository: str,
        token: str,
        *,
        timeout: float = 15.0,
        reader: GitHubStateStoreReader | None = None,
        request_func: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
        domain_validator: Callable[[dict[str, dict[str, Any]]], None] | None = None,
    ):
        self.repository = _validate_repository(repository)
        if not isinstance(token, str) or not token.strip():
            raise StateStoreWriteError("a GitHub token is required for state-store writes")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise StateStoreWriteError("timeout must be a positive number")
        self.token = token.strip()
        self.timeout = float(timeout)
        self.reader = reader or GitHubStateStoreReader(
            self.repository, self.token, timeout=self.timeout
        )
        self.request_func = request_func
        self.domain_validator = domain_validator or validate_domain_documents
        self.api_root = os.environ.get("GITHUB_API_URL", API_ROOT).rstrip("/")

    def _write_api(self, method: str, route: str, payload: dict[str, Any]) -> dict[str, Any]:
        if method not in {"POST", "PATCH"}:
            raise StateStoreWriteError("writer transport permits only POST and PATCH")
        if not isinstance(route, str) or route.startswith("/") or "://" in route:
            raise StateStoreWriteError("invalid GitHub API route")
        if self.request_func is not None:
            return self.request_func(method, route, payload)

        url = self.api_root + "/" + route
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": USER_AGENT,
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if getattr(response, "status", 200) not in {200, 201}:
                    raise StateStoreWriteError("GitHub returned an unexpected write status")
                raw = response.read(MAX_API_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if method == "PATCH" and exc.code in {409, 422}:
                raise StateStoreWriteConflict(
                    "state branch ref update was rejected; reload the branch before retrying"
                ) from None
            raise StateStoreWriteError(
                "GitHub state-store write failed with HTTP " + str(exc.code)
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if method == "PATCH":
                raise StateStoreWriteOutcomeUnknown(
                    "state branch ref update outcome is unknown; inspect the branch before retrying"
                ) from None
            raise StateStoreWriteError("GitHub state-store write connection failed") from None
        if len(raw) > MAX_API_RESPONSE_BYTES:
            raise StateStoreWriteError("GitHub write response exceeded the safety size limit")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            if method == "PATCH":
                raise StateStoreWriteOutcomeUnknown(
                    "state branch ref update response was invalid; inspect the branch before retrying"
                ) from None
            raise StateStoreWriteError("GitHub write response was not valid JSON") from None
        if not isinstance(value, dict):
            raise StateStoreWriteError("GitHub write response root must be an object")
        return value

    def _snapshot(self) -> dict[str, Any]:
        try:
            snapshot = self.reader.read_snapshot()
        except (StateStoreReadError, contract.AutomationStateError):
            raise
        if snapshot.get("branch") != contract.STATE_BRANCH:
            raise StateStoreWriteError("reader returned an unexpected state branch")
        manifest = contract.validate_manifest(snapshot.get("manifest"))
        documents = snapshot.get("documents")
        if not isinstance(documents, dict) or set(documents) != set(WRITABLE_OBJECTS):
            raise StateStoreWriteError(
                "state store is incomplete or contains an object outside the approved writer scope"
            )
        if set(manifest["entries"]) != set(WRITABLE_OBJECTS):
            raise StateStoreWriteError(
                "manifest registration does not match the approved four-object migration"
            )
        self.domain_validator(documents)
        return snapshot

    def hydrate(self, workspace: str | Path = ".") -> dict[str, Any]:
        """Atomically replace each local runtime file from one verified snapshot."""
        snapshot = self._snapshot()
        root = Path(workspace).resolve()
        root.mkdir(parents=True, exist_ok=True)
        for key in WRITABLE_OBJECTS:
            path = root / contract.STATE_OBJECTS[key]
            content = _canonical_json(snapshot["documents"][key])
            if len(content.encode("utf-8")) > MAX_OBJECT_BYTES:
                raise StateStoreWriteError("state object exceeds the configured size limit")
            temp_name = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=root,
                    prefix=path.name + ".", suffix=".tmp", delete=False,
                ) as stream:
                    temp_name = stream.name
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, path)
            except OSError:
                if temp_name:
                    try:
                        Path(temp_name).unlink(missing_ok=True)
                    except OSError:
                        pass
                raise StateStoreWriteError(
                    "could not atomically hydrate a local state object"
                ) from None
        return {
            "branch": contract.STATE_BRANCH,
            "commit_sha": snapshot["commit_sha"],
            "generation": snapshot["manifest"]["generation"],
            "verified_objects": list(WRITABLE_OBJECTS),
            "read_only": True,
        }

    def publish(
        self,
        updates: dict[str, str],
        *,
        expected_generation: int,
        message: str,
    ) -> dict[str, Any]:
        """Publish one or more changed objects in exactly one state-branch commit."""
        if isinstance(expected_generation, bool) or not isinstance(expected_generation, int) or expected_generation < 0:
            raise StateStoreWriteError("expected_generation must be a non-negative integer")
        if not isinstance(message, str) or not message.strip() or len(message) > 180:
            raise StateStoreWriteError("commit message must contain 1 to 180 characters")
        if not isinstance(updates, dict) or not updates:
            raise StateStoreWriteError("at least one state object update is required")
        if set(updates) - set(WRITABLE_OBJECTS):
            raise StateStoreWriteError(
                "write includes an object outside the approved scope; NVIDIA lock is not writable here"
            )

        snapshot = self._snapshot()
        manifest = copy.deepcopy(snapshot["manifest"])
        base_commit_sha = snapshot.get("commit_sha")
        if not isinstance(base_commit_sha, str) or not contract.SHA1_RE.fullmatch(base_commit_sha):
            raise StateStoreWriteError("snapshot commit SHA is invalid")
        if manifest["generation"] != expected_generation:
            raise StateStoreWriteConflict(
                "state generation changed; reload current state instead of overwriting it"
            )

        parsed_updates: dict[str, tuple[dict[str, Any], str]] = {}
        for key, content in updates.items():
            if not isinstance(content, str):
                raise StateStoreWriteError("state update content must be UTF-8 text")
            if len(content.encode("utf-8")) > MAX_OBJECT_BYTES:
                raise StateStoreWriteError("state object exceeds the configured size limit")
            value = contract.validate_state_document(key, content)
            parsed_updates[key] = (value, content)

        effective = {
            key: (value, content)
            for key, (value, content) in parsed_updates.items()
            if value != snapshot["documents"][key]
        }
        if not effective:
            return {
                "branch": contract.STATE_BRANCH,
                "commit_sha": base_commit_sha,
                "generation": expected_generation,
                "updated_objects": [],
                "updated": False,
                "atomic_commit": True,
            }

        merged_documents = copy.deepcopy(snapshot["documents"])
        for key, (value, _) in effective.items():
            merged_documents[key] = value
        self.domain_validator(merged_documents)

        current_commit, base_tree = self.reader._branch_head(contract.STATE_BRANCH)
        if current_commit != base_commit_sha:
            raise StateStoreWriteConflict(
                "state branch moved after snapshot validation; no write was attempted"
            )
        if not isinstance(base_tree, str) or not contract.SHA1_RE.fullmatch(base_tree):
            raise StateStoreWriteError("state branch tree SHA is invalid")

        next_generation = expected_generation + 1
        stamp = _utc_now()
        next_manifest = copy.deepcopy(manifest)
        next_manifest["generation"] = next_generation
        next_manifest["status"] = "ACTIVE"
        next_manifest["updated_at_utc"] = stamp
        tree_entries = []
        for key, (_, content) in effective.items():
            entry = contract.build_manifest_entry(
                key, content, contract.git_blob_sha(content), next_generation, stamp
            )
            next_manifest["entries"][key] = entry
            tree_entries.append({
                "path": contract.STATE_OBJECTS[key],
                "mode": "100644",
                "type": "blob",
                "content": content,
            })
        next_manifest = contract.validate_manifest(next_manifest)
        tree_entries.append({
            "path": contract.MANIFEST_PATH,
            "mode": "100644",
            "type": "blob",
            "content": _canonical_json(next_manifest),
        })

        owner, name = self.repository.split("/", 1)
        base_route = (
            "repos/" + urllib.parse.quote(owner, safe="")
            + "/" + urllib.parse.quote(name, safe="")
        )
        tree_response = self._write_api(
            "POST", base_route + "/git/trees",
            {"base_tree": base_tree, "tree": tree_entries},
        )
        tree_data = tree_response.get("sha")
        if not isinstance(tree_data, str) or not contract.SHA1_RE.fullmatch(tree_data):
            raise StateStoreWriteError("GitHub did not return a valid state tree SHA")
        commit_response = self._write_api(
            "POST", base_route + "/git/commits",
            {
                "message": message.strip(),
                "tree": tree_data,
                "parents": [base_commit_sha],
            },
        )
        commit_sha = commit_response.get("sha")
        if not isinstance(commit_sha, str) or not contract.SHA1_RE.fullmatch(commit_sha):
            raise StateStoreWriteError("GitHub did not return a valid state commit SHA")

        latest_commit, _ = self.reader._branch_head(contract.STATE_BRANCH)
        if latest_commit != base_commit_sha:
            raise StateStoreWriteConflict(
                "state branch moved before ref update; candidate commit was not published"
            )
        ref_route = (
            base_route + "/git/refs/heads/"
            + urllib.parse.quote(contract.STATE_BRANCH, safe="")
        )
        try:
            ref_response = self._write_api(
                "PATCH", ref_route,
                {"sha": commit_sha, "force": False},
            )
        except StateStoreWriteConflict:
            raise
        except StateStoreWriteError:
            # A transport failure can happen after GitHub accepted the ref update.
            # Confirm by reading the branch; never retry a possibly committed write.
            try:
                observed_commit, _ = self.reader._branch_head(contract.STATE_BRANCH)
            except Exception:
                raise StateStoreWriteOutcomeUnknown(
                    "ref update could not be verified; inspect the state branch before retrying"
                ) from None
            if observed_commit == commit_sha:
                ref_response = {"ref": "refs/heads/" + contract.STATE_BRANCH,
                                "object": {"sha": commit_sha}}
            else:
                raise

        returned_ref = ref_response.get("ref")
        returned_object = ref_response.get("object")
        returned_sha = returned_object.get("sha") if isinstance(returned_object, dict) else None
        if returned_ref != "refs/heads/" + contract.STATE_BRANCH or returned_sha != commit_sha:
            raise StateStoreWriteOutcomeUnknown(
                "GitHub ref response did not confirm the intended state commit"
            )
        return {
            "branch": contract.STATE_BRANCH,
            "previous_commit_sha": base_commit_sha,
            "commit_sha": commit_sha,
            "generation": next_generation,
            "updated_objects": sorted(effective),
            "updated": True,
            "atomic_commit": True,
            "force_push": False,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("hydrate", "publish"))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--expected-generation", type=int)
    parser.add_argument("--objects", nargs="+", choices=WRITABLE_OBJECTS)
    parser.add_argument("--message", default="Update GENERAL automation state")
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args(argv)

    if not args.repository:
        parser.error("--repository or GITHUB_REPOSITORY is required")
    if not args.token:
        parser.error("--token or GITHUB_TOKEN is required")
    writer = GitHubStateStoreWriter(args.repository, args.token)
    try:
        if args.action == "hydrate":
            result = writer.hydrate(args.workspace)
        else:
            if args.expected_generation is None or not args.objects:
                parser.error("publish requires --expected-generation and --objects")
            updates = {}
            for key in args.objects:
                path = args.workspace / contract.STATE_OBJECTS[key]
                try:
                    content = path.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    raise StateStoreWriteError(
                        "a requested local state object is missing or unreadable"
                    ) from None
                updates[key] = content
            result = writer.publish(
                updates,
                expected_generation=args.expected_generation,
                message=args.message,
            )
    except (StateStoreWriteError, StateStoreReadError, contract.AutomationStateError) as exc:
        print("STATE STORE WRITE FAILED:", type(exc).__name__, str(exc), file=__import__("sys").stderr)
        return 1

    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write("state_generation=" + str(result["generation"]) + "\n")
            output.write("state_commit_sha=" + result["commit_sha"] + "\n")
            output.write("state_updated=" + ("true" if result.get("updated") else "false") + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
