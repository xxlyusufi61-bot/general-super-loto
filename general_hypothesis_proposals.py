"""Validate structured blank-slate research proposals without executing them.

This module is a contract validator only. Candidate text is never eval'd,
compiled, imported, inserted into the method registry, or treated as evidence.
"""
from __future__ import annotations

import json
import re
from typing import Any

from general_hypothesis_execution import validate_execution_spec

START_MARKER = "<HYPOTHESIS_PROPOSALS_JSON>"
END_MARKER = "</HYPOTHESIS_PROPOSALS_JSON>"
SCHEMA_VERSION = 3
# One proposal per isolated direction prevents mixed-quality lists from
# invalidating unrelated candidates while retaining atomic block rejection.
MAX_PROPOSALS = 1
ALLOWED_EVALUATION_PROTOCOLS = {
    "forward_walk_forward",
    "reverse_retrospective_stress_test",
    "time_ordered_cross_validation",
    "uniform_random_6_of_N",
}
REQUIRED_PROTOCOLS = [
    "forward_walk_forward",
    "reverse_retrospective_stress_test",
    "time_ordered_cross_validation",
    "uniform_random_6_of_N",
]
TOP_LEVEL_KEYS = {"schema_version", "proposals"}
MODEL_PROPOSAL_KEYS = {
    "hypothesis_id",
    "discovery_direction",
    "proposed_family",
    "research_rationale",
    "known_risks",
    "formal_execution_spec",
}
GENERATED_PROPOSAL_KEYS = {
    "hypothesis",
    "exact_definition",
    "inputs",
    "output_rule",
    "parameter_policy",
    "selection_policy",
    "falsification_criterion",
}
PROPOSAL_KEYS = MODEL_PROPOSAL_KEYS | GENERATED_PROPOSAL_KEYS | {"evaluation_protocols"}
TEXT_FIELDS = {"proposed_family", "research_rationale"}


def _valid_text(value: Any, field: str, *, minimum: int = 3) -> str:
    if not isinstance(value, str):
        raise ValueError(field + " must be text.")
    normalized = value.strip()
    if len(normalized) < minimum:
        raise ValueError(field + " is empty or too short.")
    if len(normalized) > 2000:
        raise ValueError(field + " is too long.")
    return normalized


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def execution_spec_matches_proposal(
    proposal: dict[str, Any],
    execution_spec: dict[str, Any],
) -> bool:
    """Require the separately parsed DSL block to equal the proposal's formal meaning."""
    formal = proposal.get("formal_execution_spec")
    if not isinstance(formal, dict) or not isinstance(execution_spec, dict):
        return False
    try:
        return _canonical_json(formal) == _canonical_json(execution_spec)
    except (TypeError, ValueError):
        return False


def _render_expression(node: dict[str, Any]) -> str:
    """Render a validated DSL tree without accepting model-authored formula prose."""
    op = node["op"]
    if op == "const":
        return repr(node["value"])
    if op == "var":
        return {
            "candidate": "candidate_value",
            "value": "historical_draw_value",
            "draw_age": "directional_draw_age",
            "a_contains_candidate": "candidate_present_in_A",
            "b_contains_candidate": "candidate_present_in_B",
            "c_contains_candidate": "candidate_present_in_C",
        }[node["name"]]
    if op in {"aggregate_history_numbers", "aggregate_history_triples"}:
        aggregate_name = (
            "AGGREGATE_HISTORY_TRIPLES"
            if op == "aggregate_history_triples"
            else "AGGREGATE_HISTORY_NUMBERS"
        )
        return (
            aggregate_name + "[" + node["aggregate"].upper() + "]("
            + _render_expression(node["expression"]) + ")"
        )
    if op == "abs":
        return "ABS(" + _render_expression(node["arg"]) + ")"
    if op == "not":
        return "NOT(" + _render_expression(node["arg"]) + ")"
    symbols = {
        "add": "+", "sub": "-", "mul": "*", "div": "/",
        "mod": "MOD", "min": "MIN", "max": "MAX",
        "eq": "==", "ne": "!=", "lt": "<", "lte": "<=",
        "gt": ">", "gte": ">=", "and": "AND", "or": "OR",
    }
    return (
        "(" + _render_expression(node["left"]) + " "
        + symbols[op] + " " + _render_expression(node["right"]) + ")"
    )


def _validate_proposal(
    value: Any,
    *,
    canonical: bool = False,
) -> dict[str, Any]:
    """Validate raw model fields and locally derive all mathematical semantics."""
    if not isinstance(value, dict):
        raise ValueError("Each proposal must be a JSON object.")

    if canonical:
        if set(value) != PROPOSAL_KEYS:
            raise ValueError("Stored proposal keys do not match the canonical schema.")
        model_view = {key: value[key] for key in MODEL_PROPOSAL_KEYS}
        regenerated = _validate_proposal(model_view)
        if any(value[key] != regenerated[key] for key in PROPOSAL_KEYS):
            raise ValueError("Stored proposal differs from coordinator-generated semantics.")
        return regenerated

    if set(value) != MODEL_PROPOSAL_KEYS:
        raise ValueError("Proposal keys do not match the exact allowed schema.")
    proposal_id = value.get("hypothesis_id")
    if not isinstance(proposal_id, str) or re.fullmatch(r"HYP-(?!000)[0-9]{3}", proposal_id) is None:
        raise ValueError("hypothesis_id must match HYP-001 through HYP-999.")
    direction = value.get("discovery_direction")
    if direction not in {"forward", "reverse", "both"}:
        raise ValueError("discovery_direction must be forward, reverse, or both.")

    family = _valid_text(value.get("proposed_family"), "proposed_family")
    rationale = _valid_text(value.get("research_rationale"), "research_rationale", minimum=5)
    risks = value.get("known_risks")
    if not isinstance(risks, list) or not risks or len(risks) > 10:
        raise ValueError("known_risks must be a non-empty list of at most 10 items.")
    checked_risks = [
        _valid_text(risk, "known_risks item", minimum=5) for risk in risks
    ]

    formal = value.get("formal_execution_spec")
    if formal is not None:
        try:
            formal = validate_execution_spec(formal)
        except (TypeError, ValueError):
            raise ValueError(
                "formal_execution_spec is not a valid safe-DSL expression."
            ) from None
        rendered = _render_expression(formal["score_expression"])
        hypothesis = (
            "The score defined by the attached formal DSL expression is hypothesized "
            "to rank candidates with greater chronological test-draw overlap than the "
            "uniform random 6-of-60 baseline."
        )
        exact_definition = "Formal DSL definition: score(candidate) = " + rendered + "."
        output_rule = (
            "Score every member of the complete supported number pool with the formal DSL "
            "expression; rank by descending score and break equal-score ties by ascending value."
        )
        parameter_policy = (
            "All constants in the validated DSL expression are frozen before sealed "
            "evaluation; sealed outcomes cannot tune them."
        )
    else:
        hypothesis = (
            "This research idea has no faithful representation in the current safe DSL "
            "and therefore makes no executable scoring claim."
        )
        exact_definition = (
            "No faithful safe-DSL mathematical definition is available; this proposal "
            "is non-executable and cannot be scored."
        )
        output_rule = "No candidate ranking is emitted and no execution specification is permitted."
        parameter_policy = "Not applicable because no executable formal definition is available."

    if direction == "reverse":
        inputs = (
            "Only direction-isolated 6-of-60 records strictly later than each retrospective "
            "cutoff; reverse results are stress tests only and never forward predictive evidence."
        )
    elif direction == "forward":
        inputs = (
            "Only direction-isolated 6-of-60 records strictly earlier than each chronological "
            "scoring cutoff; the cutoff draw and every later record are excluded."
        )
    else:
        inputs = (
            "Only direction-isolated 6-of-60 historical records strictly on the permitted side "
            "of each cutoff; forward and reverse interpretations remain separately evaluated."
        )

    proposal: dict[str, Any] = {
        "hypothesis_id": proposal_id,
        "discovery_direction": direction,
        "proposed_family": family,
        "research_rationale": rationale,
        "hypothesis": hypothesis,
        "exact_definition": exact_definition,
        "inputs": inputs,
        "output_rule": output_rule,
        "parameter_policy": parameter_policy,
        "selection_policy": (
            "The formal DSL definition is fixed before sealed evaluation. Discovery and "
            "time-ordered development data may be used for selection; sealed outcomes "
            "and post-target records may not be used."
        ),
        "falsification_criterion": (
            "Reject the hypothesis if chronological forward evaluation and time-ordered "
            "cross-validation do not outperform the uniform-random 6-of-60 baseline within "
            "reported uncertainty. Reverse results remain retrospective stress tests only."
        ),
        "known_risks": checked_risks,
        "formal_execution_spec": formal,
        "evaluation_protocols": list(REQUIRED_PROTOCOLS),
    }

    # Status and permissions are local coordinator fields, never model-controlled.
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
        base["rejection_reasons"] = ["proposals must be a list containing at most one entry per direction."]
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
