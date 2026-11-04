#!/usr/bin/env python3
"""Offline contract for the isolated GENERAL automation-state branch.

This module intentionally contains no GitHub client and performs no network I/O.
It validates the state-store manifest and immutable object metadata. Workflow
integration and migration are separate, explicitly reviewed steps.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from typing import Any

STATE_BRANCH = "general-automation-state"
MANIFEST_PATH = "manifest.json"
SCHEMA_VERSION = 1
STORE_ID = "GENERAL_AUTOMATION_STATE"
SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Only persistent runtime state belongs here. The method registry, legacy agent
# memory, NVIDIA rate-limit branch, code, reports, and temporary sealed artifacts
# are deliberately excluded from this first contract.
STATE_OBJECTS = {
    "archive": "super_loto_archive.json",
    "evaluation_ledger": "general_evaluation_ledger.json",
    "autonomous_research_state": "general_autonomous_research_state.json",
    "research_memory": "general_research_memory.json",
    "nvidia_lock": "general_nvidia_lock.json",
}


class AutomationStateError(ValueError):
    """Base class for fail-closed state-contract errors."""


class StaleGenerationError(AutomationStateError):
    """Raised when a writer attempts to publish against an old manifest."""


def _utc_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AutomationStateError(f"{field} must be a non-empty UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AutomationStateError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if (parsed.tzinfo is None or parsed.utcoffset() is None
            or parsed.utcoffset().total_seconds() != 0):
        raise AutomationStateError(f"{field} must use UTC (zero offset)")
    return value


def _strict_json_loads(content: str) -> Any:
    if not isinstance(content, str):
        raise AutomationStateError("state content must be UTF-8 text")

    def reject_constant(value: str) -> None:
        raise AutomationStateError(f"non-standard JSON constant is forbidden: {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AutomationStateError(f"duplicate JSON object key is forbidden: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            content,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise AutomationStateError("state object is not valid strict JSON") from exc


def git_blob_sha(content: str) -> str:
    """Return Git's canonical SHA-1 for a UTF-8 blob payload."""
    encoded = content.encode("utf-8")
    header = f"blob {len(encoded)}\0".encode("ascii")
    return hashlib.sha1(header + encoded).hexdigest()


def initial_manifest() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "store_id": STORE_ID,
        "branch": STATE_BRANCH,
        "status": "BOOTSTRAP_ONLY",
        "generation": 0,
        "entries": {},
        "updated_at_utc": None,
    }


def validate_state_document(object_key: str, content: str) -> dict[str, Any]:
    """Validate the object key and strict JSON root without exposing its data."""
    if object_key not in STATE_OBJECTS:
        raise AutomationStateError("state object key is not allowlisted")
    value = _strict_json_loads(content)
    if not isinstance(value, dict):
        raise AutomationStateError("state object root must be a JSON object")
    return value


def build_manifest_entry(
    object_key: str,
    content: str,
    blob_sha: str,
    generation: int,
    updated_at_utc: str,
) -> dict[str, Any]:
    """Build metadata for an already-uploaded immutable Git blob."""
    validate_state_document(object_key, content)
    if not isinstance(blob_sha, str) or not SHA1_RE.fullmatch(blob_sha):
        raise AutomationStateError("blob_sha must be a lowercase 40-character Git SHA")
    if blob_sha != git_blob_sha(content):
        raise AutomationStateError("blob_sha does not match the supplied Git blob content")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        raise AutomationStateError("generation must be a positive integer")
    stamp = _utc_timestamp(updated_at_utc, "updated_at_utc")
    encoded = content.encode("utf-8")
    return {
        "path": STATE_OBJECTS[object_key],
        "blob_sha": blob_sha,
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
        "byte_length": len(encoded),
        "generation": generation,
        "updated_at_utc": stamp,
    }


def validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AutomationStateError("manifest root must be an object")
    expected_keys = {
        "schema_version", "store_id", "branch", "status",
        "generation", "entries", "updated_at_utc",
    }
    if set(value) != expected_keys:
        raise AutomationStateError("manifest contains missing or unknown fields")
    schema_version = value.get("schema_version")
    if type(schema_version) is not int or schema_version != SCHEMA_VERSION:
        raise AutomationStateError("unsupported manifest schema")
    if value.get("store_id") != STORE_ID:
        raise AutomationStateError("unexpected state-store identity")
    if value.get("branch") != STATE_BRANCH:
        raise AutomationStateError("manifest branch identity mismatch")
    generation = value.get("generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise AutomationStateError("manifest generation must be a non-negative integer")
    entries = value.get("entries")
    if not isinstance(entries, dict):
        raise AutomationStateError("manifest entries must be an object")
    if set(entries) - set(STATE_OBJECTS):
        raise AutomationStateError("manifest contains a non-allowlisted object")
    if value.get("status") not in {"BOOTSTRAP_ONLY", "ACTIVE"}:
        raise AutomationStateError("invalid manifest status")
    if bool(entries) != (value["status"] == "ACTIVE"):
        raise AutomationStateError("manifest status does not match its registered entries")
    stamp = value.get("updated_at_utc")
    if generation == 0:
        if stamp is not None:
            raise AutomationStateError("generation zero must not have an update timestamp")
    else:
        _utc_timestamp(stamp, "updated_at_utc")
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            raise AutomationStateError(f"entry {key} must be an object")
        expected_entry_keys = {
            "path", "blob_sha", "content_sha256", "byte_length",
            "generation", "updated_at_utc",
        }
        if set(entry) != expected_entry_keys:
            raise AutomationStateError(f"entry {key} has missing or unknown fields")
        if entry.get("path") != STATE_OBJECTS[key]:
            raise AutomationStateError(f"entry {key} path does not match the allowlist")
        if not isinstance(entry.get("blob_sha"), str) or not SHA1_RE.fullmatch(entry["blob_sha"]):
            raise AutomationStateError(f"entry {key} has an invalid Git blob SHA")
        if not isinstance(entry.get("content_sha256"), str) or not SHA256_RE.fullmatch(entry["content_sha256"]):
            raise AutomationStateError(f"entry {key} has an invalid content digest")
        size = entry.get("byte_length")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise AutomationStateError(f"entry {key} has an invalid byte length")
        entry_generation = entry.get("generation")
        if (isinstance(entry_generation, bool) or not isinstance(entry_generation, int)
                or entry_generation < 1 or entry_generation > generation):
            raise AutomationStateError(f"entry {key} has an invalid generation")
        _utc_timestamp(entry.get("updated_at_utc"), f"entry {key} updated_at_utc")
    return value


def apply_manifest_update(
    manifest: Any,
    object_key: str,
    entry: dict[str, Any],
    expected_generation: int,
) -> dict[str, Any]:
    """Return a new manifest, rejecting stale writers instead of overwriting."""
    current = validate_manifest(manifest)
    if isinstance(expected_generation, bool) or not isinstance(expected_generation, int):
        raise AutomationStateError("expected_generation must be an integer")
    if expected_generation != current["generation"]:
        raise StaleGenerationError("stale manifest generation; reload before writing")
    if object_key not in STATE_OBJECTS:
        raise AutomationStateError("state object key is not allowlisted")
    if not isinstance(entry, dict) or entry.get("path") != STATE_OBJECTS[object_key]:
        raise AutomationStateError("entry path does not match the allowlisted object")
    next_generation = current["generation"] + 1
    candidate_entry = copy.deepcopy(entry)
    if candidate_entry.get("generation") != next_generation:
        raise AutomationStateError("entry generation must equal the next manifest generation")
    candidate = copy.deepcopy(current)
    candidate["generation"] = next_generation
    candidate["status"] = "ACTIVE"
    candidate["updated_at_utc"] = candidate_entry.get("updated_at_utc")
    candidate["entries"][object_key] = candidate_entry
    return validate_manifest(candidate)


def verify_object_content(object_key: str, entry: dict[str, Any], content: str) -> bool:
    """Verify fetched object bytes against manifest metadata; fail closed."""
    validate_state_document(object_key, content)
    if not isinstance(entry, dict) or entry.get("path") != STATE_OBJECTS.get(object_key):
        raise AutomationStateError("object metadata path mismatch")
    encoded = content.encode("utf-8")
    if len(encoded) != entry.get("byte_length"):
        raise AutomationStateError("state object byte length mismatch")
    digest = hashlib.sha256(encoded).hexdigest()
    if digest != entry.get("content_sha256"):
        raise AutomationStateError("state object content digest mismatch")
    if not isinstance(entry.get("blob_sha"), str) or not SHA1_RE.fullmatch(entry["blob_sha"]):
        raise AutomationStateError("state object Git blob SHA is invalid")
    if entry["blob_sha"] != git_blob_sha(content):
        raise AutomationStateError("state object Git blob SHA mismatch")
    return True
