"""Structural contract for GENERAL's specialized agent reports.

This guard checks report shape only. It cannot establish that a claim is true.
"""
from __future__ import annotations

import re
from typing import Any


COMMON_SECTIONS = (
    "SCOPE_AND_EVIDENCE",
    "CHECKS_PERFORMED",
    "FINDINGS",
    "LIMITATIONS",
    "NEXT_OFFLINE_CHECK",
)

AGENT_SECTIONS = {
    "research": (
        "HYPOTHESES_AND_METHODS",
        "EVIDENCE_NEEDED_TO_VALIDATE_OR_FALSIFY",
    ),
    "debug": (
        "REPRODUCTION_OR_DIAGNOSTIC",
        "PROPOSED_FIX_AND_TEST",
    ),
    "validation": (
        "VALIDATION_MATRIX",
        "PASS_FAIL_UNKNOWN",
    ),
    "crossover": (
        "COMPARISON_BASIS",
        "OVERFITTING_CONTROLS",
    ),
}

FINDING_LABELS = (
    "VERIFIED_FROM_SUPPLIED_EVIDENCE",
    "HYPOTHESIS",
    "UNVERIFIED",
)

ALL_SECTIONS = frozenset(
    COMMON_SECTIONS + tuple(
        section
        for sections in AGENT_SECTIONS.values()
        for section in sections
    )
)

ROLE_GUIDANCE = {
    "research": (
        "Research: define each hypothesis/method precisely; state required inputs, "
        "chronological split and baseline; say what evidence would validate or falsify it. "
        "Do not describe a hypothesis as a successful formula without a reproducible test."
    ),
    "debug": (
        "Debug: distinguish an observed defect from a suspected cause; state whether it "
        "was reproduced, give a minimal proposed fix, and name the offline regression test. "
        "Never say a test passed unless its actual output was supplied."
    ),
    "validation": (
        "Validation: provide a check-by-check matrix with PASS, FAIL, or UNKNOWN and its "
        "specific supplied evidence. Separate checks actually performed by the workflow "
        "from checks merely recommended. Do not infer validation from a model report."
    ),
    "crossover": (
        "Crossover: name candidates, common data/splits, metric, random or simple baseline, "
        "selection interval, and untouched evaluation interval. Report uncertainty and "
        "overfitting controls; never select weights using the final evaluation interval."
    ),
}


def required_sections(agent: str) -> tuple[str, ...]:
    if agent not in AGENT_SECTIONS:
        raise ValueError("Unknown GENERAL agent.")
    return COMMON_SECTIONS + AGENT_SECTIONS[agent]


def report_contract_prompt(agent: str) -> str:
    """Return explicit format and role guidance for one known agent."""
    sections = required_sections(agent)
    headings = "\n".join(section + ":" for section in sections)
    return (
        "AGENT-SPECIFIC REPORT CONTRACT (the runner enforces these headings):\n"
        + headings
        + "\nUse every heading exactly once on its own line; put substantive text below it.\n"
        "Under FINDINGS, each non-empty finding line must start with exactly one of "
        "VERIFIED_FROM_SUPPLIED_EVIDENCE:, HYPOTHESIS:, or UNVERIFIED:. "
        "Only use VERIFIED_FROM_SUPPLIED_EVIDENCE when the cited material in this "
        "response actually supports that claim. A label is not proof.\n"
        "SCOPE_AND_EVIDENCE must name the source/context supplied. CHECKS_PERFORMED "
        "must say what was actually checked; explicitly say 'not performed' when relevant. "
        "LIMITATIONS must disclose missing inputs and runtime checks. NEXT_OFFLINE_CHECK "
        "must propose a reproducible, no-live-model check. The model has no command-execution "
        "tool in this call: never claim repository commands or tests ran or passed unless their "
        "actual execution output is supplied. Read-only source inspection is not runtime validation. "
        "If a check was not performed, say so explicitly. Do not invent results, commands, tests, "
        "metrics, archive checks, or tool access.\n"
        + ROLE_GUIDANCE[agent]
    )


def _heading(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    stripped = re.sub(r"^\s*#{1,6}\s*", "", stripped)
    match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.*)", stripped)
    if not match:
        return None
    key = match.group(1).upper()
    if key not in ALL_SECTIONS:
        return None
    return key, match.group(2).strip()


def validate_agent_report(agent: str, report: Any) -> dict[str, Any]:
    """Validate required headings and finding labels without exposing report text."""
    if agent not in AGENT_SECTIONS:
        return {"valid": False, "reason": "unknown_agent"}
    if not isinstance(report, str) or not report.strip():
        return {"valid": False, "reason": "empty_or_non_text_report"}

    sections: dict[str, list[str]] = {}
    duplicates: list[str] = []
    current: str | None = None
    for line in report.splitlines():
        parsed = _heading(line)
        if parsed is not None:
            key, inline_text = parsed
            if key in sections:
                duplicates.append(key)
            sections.setdefault(key, [])
            current = key
            if inline_text:
                sections[key].append(inline_text)
            continue
        if current is not None:
            sections[current].append(line)

    expected = required_sections(agent)
    missing = [key for key in expected if key not in sections]
    empty = [
        key for key in expected
        if key in sections and not "\n".join(sections[key]).strip()
    ]
    findings = "\n".join(sections.get("FINDINGS", []))
    finding_lines = [line.strip() for line in findings.splitlines() if line.strip()]
    unlabelled = [
        line_number
        for line_number, line in enumerate(finding_lines, start=1)
        if not re.match(
            r"^(?:[-*•]\s*)?(?:VERIFIED_FROM_SUPPLIED_EVIDENCE|HYPOTHESIS|UNVERIFIED)\s*:",
            line,
            flags=re.IGNORECASE,
        )
    ]

    valid = not missing and not empty and not duplicates and bool(finding_lines) and not unlabelled
    return {
        "valid": valid,
        "reason": "report_contract_pass" if valid else "report_contract_failed",
        "missing_sections": missing,
        "empty_sections": empty,
        "duplicate_sections": sorted(set(duplicates)),
        "unlabelled_finding_lines": unlabelled,
        "finding_line_count": len(finding_lines),
        "semantic_truth_verified": False,
    }
