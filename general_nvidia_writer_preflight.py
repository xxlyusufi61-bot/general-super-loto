#!/usr/bin/env python3
"""Fail-closed preflight for the dedicated NVIDIA Budget Writer installation."""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

EXPECTED_REPOSITORY = "xxlyusufi61-bot/general-super-loto"
EXPECTED_APP_SLUG = "general-nvidia-budget-writer"
EXPECTED_APP_ID = 5861647
EXPECTED_RULESET_ID = 27814116
EXPECTED_RULESET_NAME = "nvidia-budget-state-protection"
EXPECTED_BRANCH = "refs/heads/general-nvidia-rate-limit-state"
EXPECTED_APP_PERMISSIONS = {"contents": "write", "metadata": "read"}
API_ROOT = "https://api.github.com"


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def create_app_jwt(app_id: int, private_key: str, now: int | None = None) -> str:
    if type(app_id) is not int or app_id < 1:
        raise ValueError("Ruleset bypass actor is not a valid GitHub App ID")
    now = int(time.time()) if now is None else int(now)
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(json.dumps({
        "iat": now - 60,
        "exp": now + 540,
        "iss": app_id,
    }, separators=(",", ":")).encode())
    signing_input = (header + "." + payload).encode("ascii")
    with tempfile.TemporaryDirectory(prefix="general-app-jwt-") as directory:
        key_path = Path(directory) / "app.pem"
        input_path = Path(directory) / "input"
        signature_path = Path(directory) / "signature"
        key_path.write_text(private_key, encoding="utf-8")
        key_path.chmod(0o600)
        input_path.write_bytes(signing_input)
        try:
            subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", str(key_path),
                 "-out", str(signature_path), str(input_path)],
                check=True, capture_output=True, timeout=15,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise RuntimeError("Unable to sign GitHub App identity proof") from None
        signature = _b64url(signature_path.read_bytes())
    return (signing_input.decode("ascii") + "." + signature)


def request_json(path: str, token: str = "", *, jwt: bool = False) -> dict:
    if not path.startswith("/"):
        raise ValueError("GitHub API path must be absolute")
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "GENERAL-NVIDIA-Budget-Preflight",
    }
    if token:
        headers["Authorization"] = ("Bearer " if jwt else "token ") + token
    request = Request(API_ROOT + path, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=20) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, UnicodeError, json.JSONDecodeError):
        raise RuntimeError("GitHub read-only preflight API request failed: " + path) from None
    if not isinstance(value, dict):
        raise RuntimeError("GitHub preflight returned an unexpected response: " + path)
    return value


def validate_preflight_payloads(app: dict, repositories: dict, ruleset: dict) -> dict:
    if ruleset.get("id") != EXPECTED_RULESET_ID or ruleset.get("name") != EXPECTED_RULESET_NAME:
        raise ValueError("NVIDIA budget Ruleset identity changed")
    if ruleset.get("enforcement") != "active" or ruleset.get("target") != "branch":
        raise ValueError("NVIDIA budget Ruleset is not active for branches")
    conditions = ruleset.get("conditions")
    ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
    if not isinstance(ref_name, dict):
        raise ValueError("NVIDIA budget Ruleset branch condition is missing")
    if ref_name.get("include") != [EXPECTED_BRANCH] or ref_name.get("exclude") != []:
        raise ValueError("NVIDIA budget Ruleset does not protect exactly the budget state branch")
    rules = ruleset.get("rules")
    if not isinstance(rules, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("type"), str)
        for item in rules
    ):
        raise ValueError("NVIDIA budget Ruleset protection rules are malformed")
    rule_types = sorted(item["type"] for item in rules)
    if rule_types != ["deletion", "non_fast_forward", "update"]:
        raise ValueError("NVIDIA budget Ruleset protection rules changed")
    if ruleset.get("source_type") != "Repository" or ruleset.get("source") != EXPECTED_REPOSITORY:
        raise ValueError("NVIDIA budget Ruleset source repository changed")

    if app.get("id") != EXPECTED_APP_ID or app.get("slug") != EXPECTED_APP_SLUG:
        raise ValueError("GitHub App identity does not match the dedicated NVIDIA Budget Writer App")
    permissions = app.get("permissions")
    if permissions != EXPECTED_APP_PERMISSIONS:
        raise ValueError("NVIDIA Budget Writer App permissions are not exactly Contents write plus Metadata read")

    repo_rows = repositories.get("repositories")
    if repositories.get("total_count") != 1 or not isinstance(repo_rows, list) or len(repo_rows) != 1:
        raise ValueError("NVIDIA Budget Writer token is not scoped to exactly one repository")
    if not isinstance(repo_rows[0], dict) or repo_rows[0].get("full_name") != EXPECTED_REPOSITORY:
        raise ValueError("NVIDIA Budget Writer token is scoped to the wrong repository")

    return {
        "app_id": app_id,
        "app_slug": app.get("slug"),
        "app_permissions": permissions,
        "ruleset_id": ruleset.get("id"),
        "ruleset_name": ruleset.get("name"),
        "protected_branch": EXPECTED_BRANCH,
        "token_repository_scope": EXPECTED_REPOSITORY,
    }


def run_preflight() -> dict:
    writer_token = os.environ.get("GENERAL_NVIDIA_BUDGET_WRITER_TOKEN", "")
    private_key = os.environ.get("GENERAL_NVIDIA_BUDGET_WRITER_APP_PRIVATE_KEY", "")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not writer_token or not private_key:
        raise ValueError("Required short-lived writer token or App private key is missing")
    if repository != EXPECTED_REPOSITORY:
        raise ValueError("Preflight was invoked from an unexpected repository")

    ruleset = request_json(
        f"/repos/{EXPECTED_REPOSITORY}/rulesets/{EXPECTED_RULESET_ID}", writer_token,
    )
    bypass = ruleset.get("bypass_actors")
    if not isinstance(bypass, list) or len(bypass) != 1 or not isinstance(bypass[0], dict):
        raise ValueError("NVIDIA budget Ruleset bypass actor is missing or ambiguous")
    app_jwt = create_app_jwt(EXPECTED_APP_ID, private_key)
    app = request_json("/app", app_jwt, jwt=True)
    repositories = request_json("/installation/repositories", writer_token)
    return validate_preflight_payloads(app, repositories, ruleset)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        result = run_preflight()
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(1, "NVIDIA BUDGET WRITER PREFLIGHT BLOCKED: " + str(exc) + "\\n")
    print("NVIDIA BUDGET WRITER PREFLIGHT: PASS")
    for key, value in result.items():
        print(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
