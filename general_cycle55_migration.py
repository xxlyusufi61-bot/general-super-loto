#!/usr/bin/env python3
"""Fail-closed maintenance gate and pinned-source verifier for Cycle #55 recovery.

The controller can read or manually disable only the autonomous research
workflow. It never cancels runs, reads secrets, writes the automation state
store, invokes NVIDIA/Telegram, or starts research.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

import general_automation_state as state_contract
import general_cycle55_recovery as recovery

API_ROOT = "https://api.github.com"
USER_AGENT = "GENERAL-Cycle55-Migration-Gate/1.0"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_RUN_PAGES = 100
WORKFLOW_FILE = ".github/workflows/general-autonomous-research.yml"
WORKFLOW_ID = "general-autonomous-research.yml"
STATE_OBJECT_PATH = "general_autonomous_research_state.json"

PINNED_STATE_PROVENANCE = {
    51: {
        "commit_sha": recovery.SNAPSHOT_SOURCE_PROVENANCE["commit_sha"],
        "generation": recovery.SNAPSHOT_SOURCE_PROVENANCE["generation"],
        "blob_sha": recovery.SNAPSHOT_SOURCE_PROVENANCE["blob_sha"],
        "content_sha256": recovery.SNAPSHOT_SOURCE_PROVENANCE["content_sha256"],
    },
    52: {
        "commit_sha": recovery.CURRENT_STATE_PROVENANCE["commit_sha"],
        "generation": recovery.CURRENT_STATE_PROVENANCE["generation"],
        "blob_sha": recovery.CURRENT_STATE_PROVENANCE["blob_sha"],
        "content_sha256": recovery.CURRENT_STATE_PROVENANCE["content_sha256"],
    },
}


class MigrationGateError(RuntimeError):
    """Raised when scheduled work or pinned recovery evidence is not safe."""


def _strict_json_bytes(raw: bytes, label: str) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise MigrationGateError(label + " contains a non-standard JSON constant.")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise MigrationGateError(label + " contains a duplicate JSON key.")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeError, json.JSONDecodeError):
        raise MigrationGateError(label + " is not strict UTF-8 JSON.") from None
    if not isinstance(value, dict):
        raise MigrationGateError(label + " root must be a JSON object.")
    return value


def _git_blob_sha(raw: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
    ).hexdigest()


def verify_pinned_state_source(
    manifest_raw: bytes,
    state_raw: bytes,
    *,
    generation: int,
    commit_sha: str,
) -> dict[str, Any]:
    """Verify one exact scheduler state blob against its immutable manifest pin."""
    if type(generation) is not int or generation not in PINNED_STATE_PROVENANCE:
        raise MigrationGateError("Only the pinned Generation 51 or 52 source is accepted.")
    expected = PINNED_STATE_PROVENANCE[generation]
    if commit_sha != expected["commit_sha"]:
        raise MigrationGateError("Source commit does not match the pinned Git history.")

    manifest = _strict_json_bytes(manifest_raw, "State-store manifest")
    try:
        manifest = state_contract.validate_manifest(manifest)
    except state_contract.AutomationStateError as exc:
        raise MigrationGateError("State-store manifest failed validation: " + str(exc)) from None
    if manifest["branch"] != state_contract.STATE_BRANCH:
        raise MigrationGateError("Source manifest belongs to an unexpected state branch.")
    if manifest["generation"] != generation:
        raise MigrationGateError("Manifest generation does not match the pinned source.")

    entry = manifest["entries"].get("autonomous_research_state")
    if not isinstance(entry, dict) or entry.get("path") != STATE_OBJECT_PATH:
        raise MigrationGateError("Pinned scheduler state entry is missing or misrouted.")
    if entry.get("generation") != generation:
        raise MigrationGateError("Scheduler state entry generation does not match the pin.")

    actual_blob_sha = _git_blob_sha(state_raw)
    actual_content_sha = hashlib.sha256(state_raw).hexdigest()
    if (
        actual_blob_sha != expected["blob_sha"]
        or actual_content_sha != expected["content_sha256"]
        or entry.get("blob_sha") != expected["blob_sha"]
        or entry.get("content_sha256") != expected["content_sha256"]
        or entry.get("byte_length") != len(state_raw)
    ):
        raise MigrationGateError("Scheduler state bytes do not match the pinned manifest evidence.")

    state = _strict_json_bytes(state_raw, "Scheduler state")
    if state.get("schema_version") != 2:
        raise MigrationGateError("Pinned source must be the original schema v2 document.")
    return {
        "verified": True,
        "generation": generation,
        "commit_sha": commit_sha,
        "state_blob_sha": actual_blob_sha,
        "state_content_sha256": actual_content_sha,
        "state_byte_length": len(state_raw),
        "raw_schema_version": 2,
        "draw_rows_rendered": False,
        "target_values_rendered": False,
    }


class GitHubActionsWorkflowClient:
    """Small, bounded GitHub Actions API client with injectable offline transport."""

    def __init__(
        self,
        repository: str,
        token: str,
        *,
        request_func: Callable[[str, str, str], tuple[int, bytes]] | None = None,
    ) -> None:
        if not isinstance(repository, str) or repository.count("/") != 1:
            raise MigrationGateError("Repository must use owner/repository form.")
        owner, name = repository.split("/", 1)
        if not owner or not name or any(ch.isspace() for ch in repository):
            raise MigrationGateError("Repository must use owner/repository form.")
        if not isinstance(token, str) or not token.strip():
            raise MigrationGateError("A GitHub Actions API token is required.")
        self.repository = repository
        self.token = token.strip()
        self.request_func = request_func
        self.base = (
            "/repos/" + urllib.parse.quote(owner, safe="")
            + "/" + urllib.parse.quote(name, safe="")
            + "/actions/workflows/" + urllib.parse.quote(WORKFLOW_ID, safe="")
        )

    def _request(self, method: str, path: str) -> dict[str, Any]:
        if method not in {"GET", "PUT"}:
            raise MigrationGateError("Unsupported GitHub Actions API method.")
        if not isinstance(path, str) or not path.startswith(self.base):
            raise MigrationGateError("GitHub Actions API path is outside the fixed workflow scope.")

        if self.request_func is not None:
            try:
                status, raw = self.request_func(method, path, self.token)
            except MigrationGateError:
                raise
            except Exception:
                if method == "PUT":
                    raise MigrationGateError(
                        "GitHub Actions pause result is uncertain; inspect status before retrying."
                    ) from None
                raise MigrationGateError("GitHub Actions API read failed.") from None
        else:
            request = urllib.request.Request(
                API_ROOT + path,
                data=(b"" if method == "PATCH" else None),
                headers={
                    "Authorization": "Bearer " + self.token,
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": USER_AGENT,
                },
                method=method,
            )
            try:
                with urllib.request.urlopen(request, timeout=15) as response:
                    status = int(getattr(response, "status", 200))
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
            except urllib.error.HTTPError as exc:
                raise MigrationGateError(
                    "GitHub Actions API returned HTTP " + str(exc.code) + "; inspect status before retrying."
                ) from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise MigrationGateError(
                    "GitHub Actions API result is uncertain; do not retry the pause mutation automatically."
                ) from None

        if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
            raise MigrationGateError("GitHub Actions API response is malformed or oversized.")
        if method == "PUT" and status == 204 and not raw:
            return {}
        if status != 200:
            raise MigrationGateError("GitHub Actions API returned an unexpected HTTP status.")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise MigrationGateError("GitHub Actions API returned invalid JSON.") from None
        if not isinstance(value, dict):
            raise MigrationGateError("GitHub Actions API response root must be an object.")
        return value

    def _workflow(self) -> dict[str, Any]:
        value = self._request("GET", self.base)
        if value.get("path") != WORKFLOW_FILE:
            raise MigrationGateError("GitHub resolved a different workflow path.")
        if value.get("id") is not None and type(value["id"]) is not int:
            raise MigrationGateError("GitHub workflow identity is malformed.")
        if value.get("state") not in {
            "active", "disabled_manually", "disabled_inactivity", "disabled_fork"
        }:
            raise MigrationGateError("Workflow has an unknown or unsupported state.")
        return value

    def _scheduled_runs(self) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for page in range(1, MAX_RUN_PAGES + 1):
            query = urllib.parse.urlencode(
                {"event": "schedule", "per_page": 100, "page": page}
            )
            value = self._request("GET", self.base + "/runs?" + query)
            page_runs = value.get("workflow_runs")
            if not isinstance(page_runs, list) or len(page_runs) > 100:
                raise MigrationGateError("Scheduled workflow run listing is malformed.")
            for item in page_runs:
                if not isinstance(item, dict):
                    raise MigrationGateError("Scheduled workflow run record is malformed.")
                run_id = item.get("id")
                if type(run_id) is not int or run_id < 1 or run_id in seen_ids:
                    raise MigrationGateError("Scheduled workflow run identity is invalid or duplicated.")
                if item.get("event") != "schedule":
                    raise MigrationGateError("Run listing returned a non-schedule event.")
                if not isinstance(item.get("status"), str):
                    raise MigrationGateError("Scheduled workflow run status is missing.")
                seen_ids.add(run_id)
                runs.append(item)
            if len(page_runs) < 100:
                return runs
        raise MigrationGateError("Scheduled run history exceeded the pagination safety limit.")

    def status(self) -> dict[str, Any]:
        workflow = self._workflow()
        runs = self._scheduled_runs()
        active = [
            {"run_id": item["id"], "status": item["status"]}
            for item in runs
            if item["status"] != "completed"
        ]
        state = workflow["state"]
        if state == "disabled_manually":
            result = "SCHEDULE_PAUSED_READY" if not active else "SCHEDULE_PAUSED_DRAINING"
        elif state == "active":
            result = "SCHEDULE_ACTIVE"
        else:
            result = "SCHEDULE_DISABLED_BY_OTHER_CAUSE"
        return {
            "status": result,
            "workflow_state": state,
            "scheduled_run_count": len(runs),
            "active_scheduled_run_count": len(active),
            "active_scheduled_runs": active,
            "workflow_path": WORKFLOW_FILE,
            "runs_cancelled": 0,
        }

    def pause(self) -> dict[str, Any]:
        workflow = self._workflow()
        state = workflow["state"]
        if state == "active":
            self._request("PUT", self.base + "/disable")
        elif state != "disabled_manually":
            raise MigrationGateError(
                "Workflow is disabled for a cause other than manual pause; refusing to change it."
            )
        result = self.status()
        if result["workflow_state"] != "disabled_manually":
            raise MigrationGateError(
                "GitHub did not confirm the manual schedule pause; inspect status before retrying."
            )
        return result

    def assert_ready(self) -> dict[str, Any]:
        result = self.status()
        if result["workflow_state"] != "disabled_manually":
            raise MigrationGateError("Research workflow is not manually disabled.")
        if result["active_scheduled_run_count"] != 0:
            raise MigrationGateError(
                "Scheduled runs are still active; wait for them to complete. No run was cancelled."
            )
        return {**result, "status": "MIGRATION_GATE_READY"}


def _client_from_environment() -> GitHubActionsWorkflowClient:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    return GitHubActionsWorkflowClient(repository, token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status")
    sub.add_parser("pause")
    sub.add_parser("assert-ready")
    verify = sub.add_parser("verify-state-source")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--state", type=Path, required=True)
    verify.add_argument("--generation", type=int, choices=(51, 52), required=True)
    verify.add_argument("--commit-sha", required=True)
    args = parser.parse_args(argv)

    try:
        if args.action == "verify-state-source":
            try:
                report = verify_pinned_state_source(
                    args.manifest.read_bytes(),
                    args.state.read_bytes(),
                    generation=args.generation,
                    commit_sha=args.commit_sha,
                )
            except OSError:
                raise MigrationGateError("A pinned source file could not be read.") from None
        else:
            client = _client_from_environment()
            if args.action == "status":
                report = client.status()
            elif args.action == "pause":
                report = client.pause()
            else:
                report = client.assert_ready()
    except (MigrationGateError, state_contract.AutomationStateError) as exc:
        print("CYCLE55 MIGRATION GATE FAILED: " + str(exc), file=sys.stderr)
        return 2

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
