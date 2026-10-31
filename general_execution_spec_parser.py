"""Strict parser for model-emitted safe execution specifications.

This parser validates JSON only. It never executes candidate code, changes the
method registry, or grants execution/promotion permission.
"""
from __future__ import annotations

import json
import re
from typing import Any

from general_hypothesis_execution import validate_execution_spec

START_MARKER = "<HYPOTHESIS_EXECUTION_SPECS_JSON>"
END_MARKER = "</HYPOTHESIS_EXECUTION_SPECS_JSON>"
SCHEMA_VERSION = 1
MAX_EXECUTION_SPECS = 5
SPECIFICATION_KEYS = {"hypothesis_id", "execution_spec"}


def _base(status: str = "REJECTED") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "dsl_validated": False,
        "execution_ready": False,
        "method_registry_modified": False,
        "specifications": [],
        "rejection_reasons": [],
    }


def parse_execution_spec_block(
    report: str,
    *,
    allowed_hypothesis_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Extract and validate exactly one bounded execution-specification block."""
    base = _base()
    if not isinstance(report, str) or not report.strip():
        base["rejection_reasons"] = ["Report is empty or is not text."]
        return base

    start_count = report.count(START_MARKER)
    end_count = report.count(END_MARKER)
    if start_count == 0 and end_count == 0:
        base["status"] = "MISSING_BLOCK"
        base["rejection_reasons"] = ["Structured execution-specification block is missing."]
        return base
    if start_count != 1 or end_count != 1:
        base["rejection_reasons"] = ["Exactly one paired execution-specification block is required."]
        return base

    start = report.index(START_MARKER) + len(START_MARKER)
    end = report.index(END_MARKER)
    if end <= start:
        base["rejection_reasons"] = ["Execution-specification markers are out of order."]
        return base
    raw = report[start:end].strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        base["rejection_reasons"] = ["Execution-specification block is not valid JSON."]
        return base

    if not isinstance(payload, dict) or set(payload) != {"schema_version", "specifications"}:
        base["rejection_reasons"] = ["Top-level execution-specification keys are invalid."]
        return base
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != SCHEMA_VERSION:
        base["rejection_reasons"] = ["Unsupported execution-specification schema version."]
        return base
    specifications = payload.get("specifications")
    if not isinstance(specifications, list) or len(specifications) > MAX_EXECUTION_SPECS:
        base["rejection_reasons"] = ["specifications must contain at most five entries."]
        return base

    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        for item in specifications:
            if not isinstance(item, dict) or set(item) != SPECIFICATION_KEYS:
                raise ValueError("Each specification must contain exactly hypothesis_id and execution_spec.")
            hypothesis_id = item.get("hypothesis_id")
            if (
                not isinstance(hypothesis_id, str)
                or re.fullmatch(r"HYP-(?!000)[0-9]{3}", hypothesis_id) is None
            ):
                raise ValueError("hypothesis_id must match HYP-001 through HYP-999.")
            if hypothesis_id in seen:
                raise ValueError("Duplicate hypothesis_id in execution specifications.")
            if allowed_hypothesis_ids is not None and hypothesis_id not in allowed_hypothesis_ids:
                raise ValueError("Execution specification does not match a validated proposal ID.")
            seen.add(hypothesis_id)
            spec = validate_execution_spec(item.get("execution_spec"))
            validated.append({
                "hypothesis_id": hypothesis_id,
                "execution_spec": spec,
                "dsl_validated": True,
                "execution_ready": False,
                "registry_write_allowed": False,
            })
    except ValueError as exc:
        base["rejection_reasons"] = [str(exc)]
        return base

    base["specifications"] = validated
    base["dsl_validated"] = True
    base["status"] = "VALID_EMPTY" if not validated else "VALIDATED_SPECS"
    return base
