"""Validate a GENERAL shared-memory update before proposing it for review.

The helper never writes files or opens a pull request. It rejects any change to
binding rules and accepts only up to three new, hash-checked, unverified agent
reports appended by GENERAL Core.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any


MAX_ENTRIES = 200
MAX_NEW_REPORTS = 3
MAX_REPORT_CHARS = 12_000
ALLOWED_AGENTS = {"research", "debug", "validation", "crossover"}


class MemoryProposalError(ValueError):
    """Raised when a memory delta is not safe to propose."""


def _read_json(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        raise MemoryProposalError("Memory JSON could not be read.") from None
    if not isinstance(value, dict):
        raise MemoryProposalError("Memory root must be a JSON object.")
    return value


def _valid_entry_list(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_ENTRIES:
        raise MemoryProposalError(label + " entries must be a list within the 200-entry cap.")
    if any(not isinstance(item, dict) for item in value):
        raise MemoryProposalError(label + " contains a non-object entry.")

    ids = [item.get("id") for item in value]
    if any(type(item_id) is not int or item_id < 1 for item_id in ids):
        raise MemoryProposalError(label + " contains an invalid entry ID.")
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise MemoryProposalError(label + " entry IDs must be unique and increasing.")
    return value


def _validate_new_report(entry: dict[str, Any], baseline_max_id: int) -> None:
    entry_id = entry.get("id")
    if type(entry_id) is not int or entry_id <= baseline_max_id:
        raise MemoryProposalError("New report ID is not greater than the baseline IDs.")

    source = entry.get("source")
    if not isinstance(source, str) or not source.startswith("GENERAL/"):
        raise MemoryProposalError("New report source is not a GENERAL agent.")
    agent = source.split("/", 1)[1]
    if agent not in ALLOWED_AGENTS:
        raise MemoryProposalError("New report source is not an allowed coordinator agent.")

    if entry.get("category") != "agent_report":
        raise MemoryProposalError("New memory entries must be agent reports.")
    if entry.get("title") != agent + " report":
        raise MemoryProposalError("New report title does not match its source agent.")
    if entry.get("status") != "unverified":
        raise MemoryProposalError("New agent reports must remain unverified.")

    text = entry.get("content")
    digest = entry.get("content_hash")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_REPORT_CHARS:
        raise MemoryProposalError("New report content is empty or exceeds the size limit.")
    expected_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if not isinstance(digest, str) or digest != expected_digest:
        raise MemoryProposalError("New report content hash does not match.")

    timestamp = entry.get("timestamp")
    if not isinstance(timestamp, str):
        raise MemoryProposalError("New report timestamp is missing.")
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        raise MemoryProposalError("New report timestamp is invalid.") from None
    if parsed.tzinfo is None:
        raise MemoryProposalError("New report timestamp must include a timezone.")


def validate_memory_delta(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Validate a report-only, append-style memory change without modifying either value."""
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        raise MemoryProposalError("Baseline and candidate must be JSON objects.")

    for field in ("schema_version", "project", "binding_rules", "created_at"):
        if baseline.get(field) != candidate.get(field):
            raise MemoryProposalError("Persistent memory field changed: " + field)

    if baseline.get("schema_version") != 1:
        raise MemoryProposalError("Unsupported memory schema.")
    if baseline.get("project") != "GENERAL Super Loto":
        raise MemoryProposalError("Unexpected memory project.")

    baseline_entries = _valid_entry_list(baseline.get("entries"), "Baseline")
    candidate_entries = _valid_entry_list(candidate.get("entries"), "Candidate")
    baseline_ids = {item["id"] for item in baseline_entries}
    baseline_max_id = max(baseline_ids or {0})

    new_entries = [
        item for item in candidate_entries
        if item["id"] not in baseline_ids
    ]
    if len(new_entries) > MAX_NEW_REPORTS:
        raise MemoryProposalError("A coordinator run may add no more than three reports.")

    # Existing entries must be retained unchanged, except for the oldest prefix
    # removed by GENERAL Core's fixed 200-entry rolling window.
    trim_count = max(0, len(baseline_entries) + len(new_entries) - MAX_ENTRIES)
    expected_existing = baseline_entries[trim_count:]
    if candidate_entries != expected_existing + new_entries:
        raise MemoryProposalError(
            "Memory change is not an append-only update of unmodified existing entries."
        )

    for entry in new_entries:
        _validate_new_report(entry, baseline_max_id)

    allowed_top_level = set(baseline) | {"updated_at"}
    if set(candidate) - allowed_top_level:
        raise MemoryProposalError("Candidate memory added unexpected top-level fields.")
    for key in allowed_top_level - {"entries", "updated_at"}:
        if baseline.get(key) != candidate.get(key):
            raise MemoryProposalError("Unexpected top-level memory field changed: " + key)

    updated_at = candidate.get("updated_at")
    if updated_at is not None:
        if not isinstance(updated_at, str):
            raise MemoryProposalError("updated_at must be a string.")
        try:
            parsed_updated = datetime.fromisoformat(updated_at)
        except ValueError:
            raise MemoryProposalError("updated_at is invalid.") from None
        if parsed_updated.tzinfo is None:
            raise MemoryProposalError("updated_at must include a timezone.")

    return {
        "valid": True,
        "new_entries_count": len(new_entries),
        "has_new_entries": bool(new_entries),
        "binding_rules_changed": False,
        "all_new_entries_unverified": all(
            item.get("status") == "unverified" for item in new_entries
        ),
        "auto_merge": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a shared-memory update before proposing it for human review."
    )
    parser.add_argument("--baseline", required=True, help="Path to pre-run memory JSON.")
    parser.add_argument("--candidate", required=True, help="Path to updated memory JSON.")
    args = parser.parse_args()

    try:
        baseline = _read_json(args.baseline)
        candidate = _read_json(args.candidate)
        summary = validate_memory_delta(baseline, candidate)
    except MemoryProposalError as error:
        print(json.dumps({
            "valid": False,
            "reason": str(error),
            "auto_merge": False,
        }, ensure_ascii=False))
        return 1

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
