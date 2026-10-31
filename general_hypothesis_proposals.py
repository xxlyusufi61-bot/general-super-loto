"""Validate structured blank-slate research proposals without executing them.

This module is a contract validator only. Candidate text is never eval'd,
compiled, imported, inserted into the method registry, or treated as evidence.
"""
from __future__ import annotations

import json
import re
from typing import Any

START_MARKER = "<HYPOTHESIS_PROPOSALS_JSON>"
END_MARKER = "</HYPOTHESIS_PROPOSALS_JSON>"
SCHEMA_VERSION = 1
MAX_PROPOSALS = 5
ALLOWED_EVALUATION_PROTOCOLS = {
    "forward_walk_forward",
    "reverse_retrospective_stress_test",
    "time_ordered_cross_validation",
    "uniform_random_6_of_N",
}
TOP_LEVEL_KEYS = {"schema_version", "proposals"}
PROPOSAL_KEYS = {
    "hypothesis_id",
    "proposed_family",
    "hypothesis",
    "exact_definition",
    "inputs",
    "output_rule",
    "parameter_policy",
    "selection_policy",
    "falsification_criterion",
    "known_risks",
    "evaluation_protocols",
}
TEXT_FIELDS = {
    "proposed_family",
    "hypothesis",
    "exact_definition",
    "inputs",
    "output_rule",
    "parameter_policy",
    "selection_policy",
    "falsification_criterion",
}
REQUIRED_PROTOCOLS = set(ALLOWED_EVALUATION_PROTOCOLS)


def _valid_text(value: Any, field: str, *, minimum: int = 3) -> str:
    if not isinstance(value, str):
        raise ValueError(field + " must be text.")
    normalized = value.strip()
    if len(normalized) < minimum:
        raise ValueError(field + " is empty or too short.")
    if len(normalized) > 2000:
        raise ValueError(field + " is too long.")
    return normalized


def _validate_proposal(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Each proposal must be a JSON object.")
    if set(value) != PROPOSAL_KEYS:
        raise ValueError("Proposal keys do not match the exact allowed schema.")
    proposal_id = value.get("hypothesis_id")
    if not isinstance(proposal_id, str) or re.fullmatch(r"HYP-(?!000)[0-9]{3}", proposal_id) is None:
        raise ValueError("hypothesis_id must match HYP-001 through HYP-999.")
    proposal: dict[str, Any] = {"hypothesis_id": proposal_id}
    for field in sorted(TEXT_FIELDS):
        proposal[field] = _valid_text(value.get(field), field)
    risks = value.get("known_risks")
    if not isinstance(risks, list) or not risks or len(risks) > 10:
        raise ValueError("known_risks must be a non-empty list of at most 10 items.")
    proposal["known_risks"] = [
        _valid_text(risk, "known_risks item", minimum=5) for risk in risks
    ]
    protocols = value.get("evaluation_protocols")
    if (
        not isinstance(protocols, list)
        or any(not isinstance(item, str) for item in protocols)
        or len(protocols) != len(set(protocols))
        or set(protocols) != REQUIRED_PROTOCOLS
    ):
        raise ValueError(
            "evaluation_protocols must contain all four required protocol identifiers exactly once."
        )
    proposal["evaluation_protocols"] = list(protocols)
    # These fields are assigned locally, never accepted from the model.
    proposal["status"] = "PROPOSAL_ONLY"
    proposal["execution_ready"] = False
    proposal["registry_write_allowed"] = False
    return proposal


def parse_hypothesis_proposals(report: str) -> dict[str, Any]:
    """Extract one bounded proposal block; malformed proposals fail closed."""
    base: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "REJECTED",
        "execution_ready": False,
        "registry_modified": False,
        "proposals": [],
        "rejection_reasons": [],
    }
    if not isinstance(report, str) or not report.strip():
        base["rejection_reasons"] = ["Report is empty or is not text."]
        return base

    start_count = report.count(START_MARKER)
    end_count = report.count(END_MARKER)
    if start_count == 0 and end_count == 0:
        base["status"] = "MISSING_BLOCK"
        base["rejection_reasons"] = ["Structured hypothesis proposal block is missing."]
        return base
    if start_count != 1 or end_count != 1:
        base["rejection_reasons"] = ["Exactly one paired proposal block is required."]
        return base

    start = report.index(START_MARKER) + len(START_MARKER)
    end = report.index(END_MARKER)
    if end <= start:
        base["rejection_reasons"] = ["Proposal block markers are out of order."]
        return base
    raw = report[start:end].strip()
    # The contract asks for raw JSON. This wrapper allowance only strips a
    # conventional Markdown fence; it does not relax JSON/schema validation.
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            raw = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        base["rejection_reasons"] = ["Proposal block is not valid JSON."]
        return base
    if not isinstance(payload, dict) or set(payload) != TOP_LEVEL_KEYS:
        base["rejection_reasons"] = ["Top-level proposal JSON keys do not match the allowed schema."]
        return base
    if type(payload.get("schema_version")) is not int or payload["schema_version"] != SCHEMA_VERSION:
        base["rejection_reasons"] = ["Unsupported proposal schema version."]
        return base
    proposals = payload.get("proposals")
    if not isinstance(proposals, list) or len(proposals) > MAX_PROPOSALS:
        base["rejection_reasons"] = ["proposals must be a list containing at most five entries."]
        return base

    validated: list[dict[str, Any]] = []
    try:
        seen: set[str] = set()
        for raw_proposal in proposals:
            proposal = _validate_proposal(raw_proposal)
            if proposal["hypothesis_id"] in seen:
                raise ValueError("Duplicate hypothesis_id.")
            seen.add(proposal["hypothesis_id"])
            validated.append(proposal)
    except ValueError as exc:
        # Reject the entire block; do not silently accept a partial subset.
        base["rejection_reasons"] = [str(exc)]
        return base

    base["proposals"] = validated
    base["status"] = "VALID_EMPTY" if not validated else "VALID_PROPOSALS"
    base["rejection_reasons"] = []
    return base


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="Validate a structured hypothesis proposal block without executing candidates."
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report_text = args.report.read_text(encoding="utf-8")
    except OSError:
        raise SystemExit("Proposal validation failed: report could not be read.") from None
    result = parse_hypothesis_proposals(report_text)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("PROPOSAL STATUS:", result["status"])
    print("VALIDATED PROPOSALS:", len(result["proposals"]))
    print("EXECUTION READY: NO")
    print("METHOD REGISTRY MODIFIED: NO")
    if result["status"] in {"REJECTED", "MISSING_BLOCK"}:
        print("REASON:", "; ".join(result["rejection_reasons"]))
