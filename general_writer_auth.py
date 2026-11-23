#!/usr/bin/env python3
"""Resolve narrowly scoped GitHub writer credentials without a broad-token fallback."""
from __future__ import annotations

import argparse
import base64
import os
import re
import subprocess
from typing import Mapping

ROLE_ENVIRONMENT = {
    "main": "GENERAL_MAIN_WRITER_TOKEN",
    "automation_state": "GENERAL_AUTOMATION_STATE_WRITER_TOKEN",
    "nvidia_budget": "GENERAL_NVIDIA_BUDGET_WRITER_TOKEN",
}


class WriterAuthorizationError(RuntimeError):
    """A required, role-specific GitHub writer identity is unavailable."""


def resolve_writer_token(role: str, environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    variable = ROLE_ENVIRONMENT.get(role)
    if variable is None:
        raise WriterAuthorizationError("unknown GitHub writer role")
    token = env.get(variable, "")
    if not isinstance(token, str) or not token.strip() or any(ch.isspace() for ch in token):
        raise WriterAuthorizationError(
            "required dedicated GitHub writer credential is missing for role: " + role
        )
    return token.strip()


def git_auth_environment(
    role: str,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return an isolated child-process environment with a role-specific HTTPS header."""
    source = os.environ if environ is None else environ
    env = dict(source)
    token = resolve_writer_token(role, env)
    raw_count = env.get("GIT_CONFIG_COUNT", "0")
    try:
        count = int(raw_count)
    except (TypeError, ValueError):
        raise WriterAuthorizationError("existing Git config environment is malformed") from None
    if count < 0 or count > 98:
        raise WriterAuthorizationError("existing Git config environment is outside safe bounds")
    encoded = base64.b64encode(("x-access-token:" + token).encode("utf-8")).decode("ascii")
    env["GIT_CONFIG_COUNT"] = str(count + 1)
    env["GIT_CONFIG_KEY_" + str(count)] = "http.https://github.com/.extraheader"
    env["GIT_CONFIG_VALUE_" + str(count)] = "AUTHORIZATION: basic " + encoded
    return env


def run_git_push(role: str, remote: str, ref: str) -> None:
    """Push only the explicitly approved main-branch writer destinations."""
    allowed_refs = {
        "main": lambda value: value == "HEAD:main" or re.fullmatch(
            r"(?:general-agent-memory|general-memory-proposal)-[0-9]+-[0-9]+", value
        ) is not None,
        "nvidia_budget": lambda value: value == "HEAD:general-nvidia-rate-limit-state",
    }
    if remote != "origin" or role not in allowed_refs:
        raise WriterAuthorizationError("Git push role or remote is not allowlisted")
    if not isinstance(ref, str) or not allowed_refs[role](ref):
        raise WriterAuthorizationError("Git push ref is not allowlisted for the selected writer")
    try:
        subprocess.run(
            ["git", "push", remote, ref],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
            env=git_auth_environment(role),
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise WriterAuthorizationError(
            "GitHub writer push failed; credential and remote response details were suppressed"
        ) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("git-push",))
    parser.add_argument("--role", choices=tuple(ROLE_ENVIRONMENT), required=True)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--ref", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "git-push":
            run_git_push(args.role, args.remote, args.ref)
    except WriterAuthorizationError as exc:
        parser.exit(1, "GITHUB WRITER AUTHORIZATION FAILED: " + str(exc) + "\n")
    print("GITHUB WRITER PUSH: SUCCESS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
