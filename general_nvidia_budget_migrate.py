#!/usr/bin/env python3
"""Validate and persist the one-time known schema-v2 NVIDIA budget migration."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from general_nvidia_guard import NVIDIA_BUDGET_CATEGORY_LIMITS, NVIDIA_DAILY_REQUEST_BUDGET, validate_rate_limit_state

def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key in NVIDIA budget ledger")
        result[key] = value
    return result

def load_ledger(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys)
    if not isinstance(value, dict):
        raise ValueError("NVIDIA budget ledger root must be an object")
    return value

def reservation_digest(entries: list[dict]) -> str:
    raw = json.dumps(entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def migrate_ledger(value: dict) -> tuple[dict, dict]:
    if not isinstance(value, dict):
        raise ValueError("ledger must be an object")
    original = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    version = value.get("schema_version")
    migrated = validate_rate_limit_state(value)
    if version == 3:
        status = "ALREADY_V3_NOOP"
    elif version == 2 and migrated.get("schema_version") == 3:
        status = "MIGRATED_V2_TO_V3"
        for key in ("daily_requests", "requests", "diagnostic_run_ids"):
            if migrated[key] != value[key]:
                raise ValueError(key + " history changed during migration")
    else:
        raise ValueError("only exact known schema-v2 or valid schema-v3 ledger is accepted")
    report = {
        "status": status,
        "schema_version": 3,
        "daily_max_requests": NVIDIA_DAILY_REQUEST_BUDGET,
        "category_limits": dict(NVIDIA_BUDGET_CATEGORY_LIMITS),
        "reservation_count": len(migrated["daily_requests"]),
        "reservation_ids_sha256": reservation_digest(migrated["daily_requests"]),
        "source_sha256": hashlib.sha256(original).hexdigest(),
    }
    return migrated, report

def migrate_file(path: Path) -> dict:
    original = load_ledger(path)
    migrated, report = migrate_ledger(original)
    if report["status"] == "MIGRATED_V2_TO_V3":
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(json.dumps(migrated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
    return report

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = migrate_file(args.ledger)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        parser.exit(1, "NVIDIA BUDGET MIGRATION BLOCKED: " + str(exc) + "\n")
    for key, value in report.items():
        print(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
