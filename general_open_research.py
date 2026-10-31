"""Blank-slate Super Loto research runner with cautious NVIDIA usage observation.

This module intentionally does not import the legacy method registry, candidate
implementations, evidence catalog, or project memory. It validates the archive,
isolates the latest draw locally, withholds a separate chronological validation
interval, and sends only discovery rows plus governance rules to the research model. Its output is a hypothesis report, not
a verified formula or prediction.

NVIDIA's account-level rate limit/quota is not queried here. The runner records
only safe usage metadata returned by each directional inference request, when provided.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import general_data_guard as guard
from general_live_consent import has_explicit_live_consent
from general_hypothesis_proposals import parse_hypothesis_proposals
from general_execution_spec_parser import parse_execution_spec_block
import general_sealed_evaluation as sealed

VERSION = "0.7.0"
MODEL = "z-ai/glm-5.3-flash"
BASE = Path(__file__).resolve().parent
ARCHIVE = BASE / os.environ.get("GENERAL_ARCHIVE_FILE", "super_loto_archive.json")
REPORT = BASE / "open_research_report.md"
FORWARD_REPORT = BASE / "open_research_forward_report.md"
REVERSE_REPORT = BASE / "open_research_reverse_report.md"
PROPOSALS = BASE / "open_research_proposals.json"
EXECUTION_SPECS = BASE / "open_research_execution_specs.json"
USAGE_REPORT = BASE / "open_research_usage.json"
API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
DOCUMENTATION_REVIEW_DATE = "2026-10-25"

QUOTA_LIMITATION_NOTE = (
    "No supported account-level remaining-quota API was identified in the NVIDIA "
    "API Catalog documentation reviewed on 2026-10-25. Check account-level rate "
    "limits in the NVIDIA API Catalog UI. This is a documentation review, not proof "
    "that NVIDIA has no private or newly introduced endpoint."
)

NOTATION_RULES = (
    "A, B, and C are neutral labels for consecutive chronological draw states: A is earlier, B follows A, and C follows B within one regime.",
    "T is an abstract transition or derived representation that each new hypothesis must define explicitly; no formula or encoding is preselected.",
    "MOD5 names a modulo-five research view; every new hypothesis must state its exact representation convention.",
    "A->B->C, A->T, and MOD5->T are user-authorized research-family labels only, not inherited algorithms or approved equations.",
)

GOVERNANCE_RULES = (
    "Keep the target draw hidden: do not use target or later draw values for discovery, "
    "training, feature construction, tuning, or selection.",
    "Use only draws strictly earlier than the isolated target date.",
    "Stop if archive structure, dates, chronology, number sets, or target partition fail validation.",
    "Explore the data without presupposing any previously known formula, feature family, transform, "
    "legacy implementation, prior weight, or historical result.",
    "Do not read or rely on legacy formula files, historical semantics, candidate implementations, "
    "evidence catalogs, earlier user experiments, or previous project-memory reports.",
    "Use the neutral research notation only; define every symbol, transformation, input, output, parameter, "
    "and tie-break rule from scratch. The three research-family labels remain OPEN_DEFINITION.",
    "Only records dated 2020-08-02 or later are eligible for discovery, training, evaluation, and agent context; reject any earlier row. "
    "any cross-regime comparison must be separately identified and justified.",
    "Use only free resources. Do not use Pydroid, card-required services, or services that may automatically "
    "convert to paid use. Block live model requests unless free-use and no-billing status are independently verified.",
    "Treat all ideas as hypotheses until tested with reproducible code and chronological evaluation.",
    "Do not invent data, tests, results, or mathematical definitions; say when a test has not been run.",
    "Separate discovery/selection data from a frozen chronological evaluation interval.",
    "Use forward walk-forward evaluation and time-ordered cross-validation; reverse chronology, if used, "
    "must be labelled retrospective stress testing and not forward predictive evidence.",
    "Compare candidates with an appropriate random baseline and report uncertainty and search limits.",
    "A valid outcome may be that no robust signal is found. Never promise lottery predictions.",
    "If a binding-rule change is proposed, do not alter the rule during research. Include a structured request "
    "only when justified. The separate workflow may submit that request for explicit Telegram user approval; "
    "never claim it was sent or approved without a confirmed workflow/decision record. Approval alone never "
    "edits project rules: a separate reviewed implementation pull request is required.",
)

def parse_archive(path: Path = ARCHIVE) -> list[dict[str, Any]]:
    if not path.is_file():
        raise RuntimeError("Archive missing; open research stopped.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("Archive unreadable; open research stopped.") from None
    if isinstance(payload, dict):
        if "draws" in payload and "records" in payload:
            raise RuntimeError("Ambiguous archive format; open research stopped.")
        rows = payload.get("draws", payload.get("records"))
    else:
        rows = payload
    if not isinstance(rows, list):
        raise RuntimeError("Archive must contain a list of draws.")
    try:
        checked = guard.validate_archive(rows)
    except Exception:
        raise RuntimeError("Data Guard rejected archive; open research stopped.") from None
    if not isinstance(checked, dict) or checked.get("valid") is not True:
        raise RuntimeError("Data Guard did not validate archive.")
    return rows

def isolate_pre_target(rows: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    checked = guard.validate_archive(rows)
    target = checked.get("last_date")
    if not isinstance(target, str):
        raise RuntimeError("Could not determine latest archive target.")
    try:
        target_day = date.fromisoformat(target)
    except ValueError:
        raise RuntimeError("Invalid target date from Data Guard.") from None
    if target_day.isoformat() != target:
        raise RuntimeError("Non-canonical target date.")
    split = guard.isolate_target(rows, target)
    if not isinstance(split, dict):
        raise RuntimeError("Data Guard returned an invalid isolation result.")
    if split.get("isolation_pass") is not True:
        raise RuntimeError("Target isolation failed.")
    if split.get("target_present_in_archive") is not True:
        raise RuntimeError("Latest target is not present in archive.")
    train = split.get("train")
    excluded = split.get("excluded_dates")
    if not isinstance(train, list) or not isinstance(excluded, list):
        raise RuntimeError("Data Guard returned invalid partitions.")
    train_dates = []
    for row in train:
        if not isinstance(row, dict):
            raise RuntimeError("Invalid training row.")
        day = row.get("date")
        if not isinstance(day, str):
            raise RuntimeError("Invalid training date.")
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            raise RuntimeError("Invalid training date.") from None
        if parsed.isoformat() != day or parsed >= target_day:
            raise RuntimeError("Target/future draw leaked into training.")
        nums = guard.validate_numbers(row.get("numbers"))
        if nums != sorted(nums):
            raise RuntimeError("Training draw numbers must be sorted.")
        train_dates.append(day)
    source_dates = [row.get("date") for row in rows]
    expected_train = [day for day in source_dates if day < target]
    expected_excluded = [day for day in source_dates if day >= target]
    if train_dates != expected_train or excluded != expected_excluded:
        raise RuntimeError("Archive partition mismatch; open research stopped.")
    if not train:
        raise RuntimeError("No pre-target draws available.")
    clean_train = [{"date": row["date"], "numbers": list(row["numbers"])} for row in train]
    return target, clean_train

def isolate_discovery_only(
    rows: list[dict[str, Any]],
    *,
    ledger_path: str | Path | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Return only fresh discovery rows; previously consumed holdouts stay excluded."""
    target, _pre_target = isolate_pre_target(rows)
    resolved_ledger = Path(ledger_path) if ledger_path is not None else BASE / "general_evaluation_ledger.json"
    ledger = sealed.load_ledger(resolved_ledger)
    used_dates = sealed.get_used_validation_dates(ledger)
    split = sealed.split_archive(rows, used_validation_dates=used_dates)
    if split.get("target_date") != target:
        raise RuntimeError("Sealed split target disagrees with Data Guard.")
    discovery = split.get("discovery_rows")
    if not isinstance(discovery, list):
        raise RuntimeError("Sealed split returned invalid discovery rows.")
    validation_dates = {
        row["date"]
        for regime in split["regimes"].values()
        for row in regime["validation_rows"] + regime["reverse_validation_rows"]
    }
    forward_validation_dates = {
        row["date"]
        for regime in split["regimes"].values()
        for row in regime["validation_rows"]
    }
    reverse_validation_dates = {
        row["date"]
        for regime in split["regimes"].values()
        for row in regime["reverse_validation_rows"]
    }
    if forward_validation_dates.intersection(reverse_validation_dates):
        raise RuntimeError("Forward and reverse sealed validation dates overlap.")
    discovery_dates = {row["date"] for row in discovery}
    if discovery_dates.intersection(validation_dates):
        raise RuntimeError("Discovery and sealed validation dates overlap.")
    if discovery_dates.intersection(used_dates):
        raise RuntimeError("A previously consumed holdout date entered model discovery.")
    if target in discovery_dates or target in validation_dates:
        raise RuntimeError("Isolated target entered the discovery or validation partition.")
    if any(row["date"] >= target for row in discovery):
        raise RuntimeError("Target/future row leaked into model discovery input.")
    return target, discovery, sealed.public_plan(split)


def build_messages(
    target: str,
    train: list[dict[str, Any]],
    focus: str = "",
    *,
    direction: str = "forward",
) -> list[dict[str, str]]:
    if direction not in {"forward", "reverse"}:
        raise ValueError("Discovery direction must be forward or reverse.")
    direction_order = "oldest_to_newest" if direction == "forward" else "newest_to_oldest"
    direction_phrase = "oldest-to-newest" if direction == "forward" else "newest-to-oldest"
    system = (
        "You are the GENERAL blank-slate research agent for Super Loto.\n"
        "You have no prior formulas or legacy research context. Discover candidate "
        "relationships from the supplied historical data rather than assuming a known method.\n"
        "Research notation:\n- " + "\n- ".join(NOTATION_RULES) + "\n"
        "Governance rules:\n- " + "\n- ".join(GOVERNANCE_RULES) + "\n"
        "This request is one isolated " + direction + " discovery pass. The only data supplied to this call is the " + direction_phrase + " history. The other direction's data and candidate list are not present in this request. Every proposal MUST use discovery_direction=" + direction + "; do not propose the other direction or label any proposal both. The coordinator will independently parse and keep the two call results separate.\n"
        "For full-history real-archive testing, selection must be repeated separately at each historical cutoff using only rows available in that direction. Never apply a formula selected after seeing later rows to earlier forward targets. Any reverse result using later draws is retrospective stress testing only, not predictive evidence.\n"
        "The target date is metadata only; its numbers and every record on or after it are absent. "
        "Do not infer or fabricate those values. The data supplied is discovery history, not a frozen "
        "final test set. Do not claim a candidate is validated or predictive based on inspection alone.\n"
        "Return a concise report with observations, clearly labelled candidate hypotheses, exact reproducible "
        "tests needed, leakage/overfitting risks, and what was not tested. "
        "Do not output winning-number predictions. A no-signal conclusion is acceptable. "
        "If and only if you recommend changing a binding rule, include exactly one structured block using "
        "the markers shown below, with every field filled by a factual, concise string. Do not state that "
        "the request was sent or approved. No rule changes during this research call.\n"
        "<RULE_CHANGE_REQUEST_JSON>\n"
        "{\"change_type\":\"ADD|REVISE|REMOVE\",\"rule_id\":\"stable rule identifier\",\"current_rule\":\"exact current wording or NONE for ADD\","
        "\"proposed_rule\":\"proposed wording or exactly REMOVE for REMOVE\",\"reason\":\"why change is needed\","
        "\"evidence\":\"available evidence and its limitations\","
        "\"risk_assessment\":\"risks and safeguards\","
        "\"tests_required\":\"tests required before implementation\","
        "\"rollback_plan\":\"how to revert\"}\n"
        "</RULE_CHANGE_REQUEST_JSON>\n"
        "If no rule change is needed, omit the entire rule-change block. Rule-change requests are submitted "
        "separately for user approval; malformed or incomplete blocks are rejected.\n"
        "Also include exactly one machine-readable hypothesis proposal block. It is a research queue only, "
        "not an approved or executable formula. If no testable hypothesis is supported, use an empty proposals "
        "list. At most five proposals; provide no source code and no winning-number predictions.\n"
        "<HYPOTHESIS_PROPOSALS_JSON>\n"
        "{\"schema_version\":2,\"proposals\":[{"
        "\"hypothesis_id\":\"HYP-001\","
        f"\"discovery_direction\":\"{direction}\","
        "\"proposed_family\":\"free descriptive label; not a registry status\","
        "\"hypothesis\":\"testable statement\","
        "\"exact_definition\":\"precise mathematical steps, no executable code\","
        "\"inputs\":\"strictly pre-target inputs\","
        "\"output_rule\":\"deterministic ranking and scoring rule\","
        "\"parameter_policy\":\"how parameters are fixed\","
        "\"selection_policy\":\"development-only selection policy\","
        "\"falsification_criterion\":\"what result rejects this hypothesis\","
        "\"known_risks\":[\"overfitting risk\"],"
        "\"evaluation_protocols\":[\"forward_walk_forward\","
        "\"reverse_retrospective_stress_test\","
        "\"time_ordered_cross_validation\","
        "\"uniform_random_6_of_N\"]}]}\n"
        "</HYPOTHESIS_PROPOSALS_JSON>\n"
        "Use exactly the schema keys shown; do not add prediction numbers, source code, weights chosen from hidden "
        "targets, or any claim of validation. Proposed family names are descriptive only: they do not create or "
        "change a method registry entry. An empty proposals list is an acceptable result.\n"
        "Order proposals by priority using discovery data only; put the most defensible, testable "
        "hypothesis first. Do not use the sealed validation interval, validation outcomes, or later "
        "draws to order or select proposals.\n"
        "Also include exactly one HYPOTHESIS_EXECUTION_SPECS_JSON block for this direction only. "
        "Provide zero or one matching execution specification for the first proposal in this isolated pass, "
        "when it can be represented faithfully by this DSL. Never provide a spec for another direction. "
        "Do not provide alternatives for later proposals and do not use validation outcomes to choose. "
        "The coordinator combines separately parsed directional outputs; a complete claim requires one "
        "valid spec from each independent direction.\n"
        "The block shape is schema_version 1 with specifications; each item has only "
        "hypothesis_id and execution_spec. execution_spec has only schema_version 1 and "
        "score_expression. It is a typed expression tree, not source code.\n"
        "Allowed nodes: const with a finite bounded numeric value; var with name candidate, "
        "value, or draw_age; unary abs or not; binary add, sub, mul, div, mod, min, max, "
        "eq, ne, lt, lte, gt, gte, and, or; and exactly one aggregate_history_numbers node "
        "with aggregate count or sum and one expression. count requires a boolean expression; "
        "sum requires a numeric expression. value and draw_age are available only inside "
        "the aggregate.\n"
        "Exact node shapes: const has only op,value; var has only op,name; abs and not "
        "have only op,arg; binary operators have only op,left,right; "
        "aggregate_history_numbers has only op,aggregate,expression. "
        "Use those exact key sets on every node.\n"
        "The score expression must return a number. Use at most 32 nodes and depth 8. "
        "Unknown operations, extra keys, nested aggregates, multiple aggregates, code, "
        "imports, loops, file/network access, and arbitrary functions are forbidden. "
        "If a hypothesis cannot be represented without changing its meaning, omit its execution spec.\n"
        "A DSL-valid specification is not approved or canonical. It is run only against fixed "
        "synthetic 6-of-60 active-scope fixtures; do not claim that it was real-archive backtested, "
        "promoted, or written to the method registry.\n"
        "Place one raw JSON object between <HYPOTHESIS_EXECUTION_SPECS_JSON> and "
        "</HYPOTHESIS_EXECUTION_SPECS_JSON>. Its top-level keys are schema_version and "
        "specifications. Provide zero or one item only. The item has exactly hypothesis_id and "
        "execution_spec and must match the first proposal in this isolated direction. Do not copy a template "
        "with placeholders; use a fully valid AST or leave the list empty."
    )
    target_day = date.fromisoformat(target)
    scope_start = date(2020, 8, 2)
    if target_day < scope_start:
        raise RuntimeError("Target is before the active research scope.")
    active_history = list(train)
    for row in active_history:
        day = date.fromisoformat(row["date"])
        if day < scope_start:
            raise RuntimeError("Pre-scope record reached agent discovery; request stopped.")
        if day >= target_day:
            raise RuntimeError("Target/future record found in discovery history.")
    if direction == "reverse":
        active_history.reverse()
    regime_history = {"R60": active_history}
    regime_counts = {"R60": len(active_history)}
    user = {
        "task": "Perform one isolated " + direction + " blank-slate hypothesis discovery pass. Do not output number predictions.",
        "discovery_direction": direction,
        "history_order": direction_order,
        "user_research_focus_untrusted": str(focus)[:1000],
        "target_date_metadata_only": target,
        "pre_target_draw_count": len(train),
        "discovery_history_by_regime": regime_history,
        "discovery_counts_by_regime": regime_counts,
        "regime_boundary": "2020-08-02",
        "regimes_must_be_evaluated_separately": True,
        "target_numbers_included": False,
        "post_target_records_included": False,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, separators=(",", ":"))},
    ]


def filter_usage_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    """Keep only non-secret rate-limit/retry/request-id headers from the response."""
    safe: dict[str, str] = {}
    if headers is None:
        return safe
    for key, value in headers.items():
        normalized = str(key).strip().lower().replace("_", "-")
        if (
            "ratelimit" in normalized
            or "rate-limit" in normalized
            or normalized == "retry-after"
            or normalized in {"request-id", "x-request-id"}
        ):
            safe[str(key)] = str(value)
    return safe

def build_usage_observation(
    status_code: int | None,
    headers: Mapping[str, Any] | None,
    payload: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build a cautious report without treating token usage as remaining quota or cost."""
    usage = payload.get("usage") if isinstance(payload, Mapping) else None
    if not isinstance(usage, dict):
        usage = None
    return {
        "schema_version": 1,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "endpoint": API_URL,
        "http_status": status_code,
        "inference_requests_made_by_this_run": 1,
        "automatic_retries": 0,
        "rate_limit_headers_returned": filter_usage_headers(headers),
        "token_usage_returned_by_provider": usage,
        "remaining_quota_before_request": None,
        "account_level_quota_api_query": "NOT_PERFORMED",
        "account_level_quota_status": "UNKNOWN",
        "account_level_rate_limits_location": "NVIDIA API Catalog account UI; check manually",
        "billing_or_paid_entitlement_status": "NOT_VERIFIED_BY_THIS_REQUEST",
        "documentation_review_date": DOCUMENTATION_REVIEW_DATE,
        "quota_limitation_note": QUOTA_LIMITATION_NOTE,
        "interpretation": (
            "Response token usage is not remaining quota and does not establish cost or unlimited access. "
            "Only provider-returned rate-limit headers are recorded; missing headers mean unavailable, "
            "not unlimited."
        ),
    }

def save_usage_observation(
    observation: dict[str, Any],
    *,
    request_label: str | None = None,
) -> None:
    if request_label not in {None, "forward", "reverse"}:
        raise ValueError("Unknown NVIDIA request label.")
    report_path = (
        USAGE_REPORT
        if request_label is None
        else USAGE_REPORT.with_name(
            USAGE_REPORT.stem + "_" + request_label + USAGE_REPORT.suffix
        )
    )
    try:
        report_path.write_text(
            json.dumps(observation, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise RuntimeError("Could not write NVIDIA usage observation report.") from exc


def write_combined_usage_summary() -> dict[str, Any]:
    directional = []
    for direction in ("forward", "reverse"):
        path = USAGE_REPORT.with_name(
            USAGE_REPORT.stem + "_" + direction + USAGE_REPORT.suffix
        )
        if not path.is_file():
            raise RuntimeError("Missing " + direction + " usage observation; run is incomplete.")
        try:
            observation = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise RuntimeError("Unreadable " + direction + " usage observation.") from None
        if not isinstance(observation, dict):
            raise RuntimeError("Invalid " + direction + " usage observation.")
        directional.append({"direction": direction, "observation": observation})
    summary = {
        "schema_version": 1,
        "inference_requests_made_by_this_run": 2,
        "automatic_retries": 0,
        "request_direction_order": ["forward", "reverse"],
        "account_level_quota_status": "UNKNOWN",
        "billing_or_paid_entitlement_status": "NOT_VERIFIED_BY_THIS_RUN",
        "requests": directional,
        "interpretation": (
            "Two separate hosted requests were made only after the explicit free-only/no-billing "
            "confirmation, global safety-lock preflight, and independent free-use verification. "
            "Each direction's data was isolated to its own request. Token usage and rate-limit headers "
            "do not prove remaining quota, free entitlement, or absence of billing risk."
        ),
    }
    try:
        USAGE_REPORT.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise RuntimeError("Could not write combined NVIDIA usage summary.") from exc
    return summary

def parse_directional_result(
    report: str,
    direction: str,
) -> dict[str, Any]:
    """Validate one direction's model output and namespace its IDs before merging."""
    if direction not in {"forward", "reverse"}:
        raise ValueError("Directional output must be forward or reverse.")
    proposals = parse_hypothesis_proposals(report)
    if proposals["status"] in {"VALID_PROPOSALS", "VALID_EMPTY"}:
        wrong_direction = [
            item.get("hypothesis_id")
            for item in proposals["proposals"]
            if item.get("discovery_direction") != direction
        ]
        if wrong_direction:
            proposals["status"] = "REJECTED_DIRECTION_MISMATCH"
            proposals["rejection_reasons"] = [
                "Every proposal in this isolated " + direction +
                " call must use the same discovery_direction; mismatched IDs: "
                + ", ".join(str(item) for item in wrong_direction)
            ]
            proposals["proposals"] = []

    original_ids = [item["hypothesis_id"] for item in proposals["proposals"]]
    offset = 0 if direction == "forward" else 500
    remap = {
        old_id: "HYP-" + str(offset + index + 1).zfill(3)
        for index, old_id in enumerate(original_ids)
    }
    mapped_proposals = []
    for item in proposals["proposals"]:
        mapped = dict(item)
        mapped["source_hypothesis_id"] = item["hypothesis_id"]
        mapped["hypothesis_id"] = remap[item["hypothesis_id"]]
        mapped_proposals.append(mapped)
    proposals["proposals"] = mapped_proposals
    proposals["direction"] = direction
    proposals["source_hypothesis_ids"] = original_ids

    execution = parse_execution_spec_block(
        report,
        allowed_hypothesis_ids=set(original_ids),
    )
    if len(execution["specifications"]) > 1:
        execution["status"] = "REJECTED_DIRECTIONAL_SPEC_COUNT"
        execution["dsl_validated"] = False
        execution["synthetic_tests_passed"] = False
        execution["execution_ready"] = False
        execution["rejection_reasons"] = [
            "An isolated directional response may provide at most one execution specification."
        ]
        execution["specifications"] = []
    mapped_specs = []
    for item in execution["specifications"]:
        mapped = dict(item)
        source_id = item["hypothesis_id"]
        if source_id not in remap:
            execution["status"] = "REJECTED_DIRECTIONAL_SPEC_ID"
            execution["dsl_validated"] = False
            execution["synthetic_tests_passed"] = False
            execution["execution_ready"] = False
            execution["rejection_reasons"] = [
                "Execution specification is not attached to a validated proposal from this direction."
            ]
            mapped_specs = []
            break
        mapped["source_hypothesis_id"] = source_id
        mapped["hypothesis_id"] = remap[source_id]
        mapped_specs.append(mapped)
    execution["specifications"] = mapped_specs
    execution["direction"] = direction
    execution["source_hypothesis_ids"] = original_ids
    return {
        "direction": direction,
        "proposal_document": proposals,
        "execution_spec_document": execution,
    }


def combine_directional_results(
    forward_result: dict[str, Any],
    reverse_result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Merge already-isolated results, preserving direction labels and fail-closed readiness."""
    if forward_result.get("direction") != "forward" or reverse_result.get("direction") != "reverse":
        raise ValueError("Directional results must be supplied in forward, reverse order.")
    directional_results = {"forward": forward_result, "reverse": reverse_result}
    merged_proposals = []
    merged_specs = []
    proposal_statuses = {}
    spec_statuses = {}
    ready_each_direction = {}
    rejection_reasons = []

    for direction in ("forward", "reverse"):
        result = directional_results[direction]
        proposal_doc = result["proposal_document"]
        spec_doc = result["execution_spec_document"]
        proposal_statuses[direction] = {
            "status": proposal_doc["status"],
            "proposal_count": len(proposal_doc["proposals"]),
            "source_hypothesis_ids": proposal_doc.get("source_hypothesis_ids", []),
            "rejection_reasons": proposal_doc.get("rejection_reasons", []),
        }
        spec_statuses[direction] = {
            "status": spec_doc["status"],
            "specification_count": len(spec_doc["specifications"]),
            "dsl_validated": spec_doc.get("dsl_validated") is True,
            "synthetic_tests_passed": spec_doc.get("synthetic_tests_passed") is True,
            "rejection_reasons": spec_doc.get("rejection_reasons", []),
        }
        eligible = [
            item for item in proposal_doc["proposals"]
            if item.get("discovery_direction") == direction
        ]
        first = eligible[0] if eligible else None
        matching_specs = spec_doc["specifications"]
        has_one_matching_spec = (
            proposal_doc["status"] == "VALID_PROPOSALS"
            and spec_doc["status"] == "VALIDATED_SPECS"
            and len(matching_specs) == 1
            and first is not None
            and matching_specs[0].get("hypothesis_id") == first.get("hypothesis_id")
            and matching_specs[0].get("source_hypothesis_id") == first.get("source_hypothesis_id")
            and spec_doc.get("dsl_validated") is True
            and spec_doc.get("synthetic_tests_passed") is True
            and matching_specs[0].get("synthetic_tests_passed") is True
            and matching_specs[0].get("synthetic_test_summary", {}).get("real_archive_used") is False
        )
        ready_each_direction[direction] = has_one_matching_spec
        if not has_one_matching_spec:
            rejection_reasons.append(
                direction + " direction does not have exactly one DSL/synthetic-tested specification "
                "matching its first validated proposal."
            )
        merged_proposals.extend(proposal_doc["proposals"])
        merged_specs.extend(matching_specs)

    proposal_document = {
        "schema_version": 2,
        "status": "VALID_PROPOSALS" if merged_proposals else "VALID_EMPTY",
        "execution_ready": False,
        "registry_modified": False,
        "proposals": merged_proposals,
        "directional_results": proposal_statuses,
        "directional_independence": {
            "separate_model_requests": True,
            "forward_data_sent_to_reverse_request": False,
            "reverse_data_sent_to_forward_request": False,
        },
        "rejection_reasons": rejection_reasons,
    }
    all_specs_ready = all(ready_each_direction.values()) and len(merged_specs) == 2
    execution_document = {
        "schema_version": 1,
        "status": "VALIDATED_SPECS" if all_specs_ready else "INCOMPLETE_DIRECTIONAL_SPECS",
        "dsl_validated": all_specs_ready,
        "synthetic_tests_passed": all_specs_ready and all(
            item.get("synthetic_tests_passed") is True for item in merged_specs
        ),
        "execution_ready": False,
        "method_registry_modified": False,
        "specifications": merged_specs,
        "directional_results": spec_statuses,
        "separate_directional_specs_required": True,
        "real_archive_used": False,
        "rejection_reasons": [] if all_specs_ready else rejection_reasons,
    }
    return proposal_document, execution_document


def call_model(
    messages: list[dict[str, str]],
    *,
    request_label: str | None = None,
) -> str:
    if request_label not in {None, "forward", "reverse"}:
        raise ValueError("Unknown NVIDIA request label.")
    request_usage_name = (
        USAGE_REPORT.name
        if request_label is None
        else USAGE_REPORT.stem + "_" + request_label + USAGE_REPORT.suffix
    )
    if not has_explicit_live_consent(
        os.environ.get("GENERAL_CONFIRM_LIVE_CALLS", "")
    ):
        raise RuntimeError(
            "NVIDIA inference was not explicitly approved by exact confirmation phrase; "
            "no provider request was sent."
        )
    if os.environ.get("GENERAL_NVIDIA_GUARD_PASSED") != "1":
        raise RuntimeError(
            "Global NVIDIA safety-lock preflight was not confirmed; no provider request was sent."
        )
    if os.environ.get("GENERAL_ALLOW_NVIDIA_CALL", "").strip().lower() != "true":
        raise RuntimeError(
            "NVIDIA inference was not explicitly approved; no provider request was sent."
        )
    # Every individual hosted request is blocked unless the operator has
    # independently verified its free-only/no-billing status. The orchestrator
    # uses separate directional calls and never retries automatically. Rate limits
    # alone are not proof of free allowance.
    if os.environ.get("GENERAL_NVIDIA_FREE_USAGE_VERIFIED", "").strip().lower() != "true":
        raise RuntimeError(
            "Free-use status was not independently verified; NVIDIA inference blocked. "
            "No provider request was sent."
        )
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY missing; research stopped.")
    body = json.dumps({
        "model": MODEL,
        "messages": messages,
        "max_tokens": 1800,
        "temperature": 0.2,
        "stream": False,
    }).encode("utf-8")
    request = urllib.request.Request(
        API_URL, data=body,
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    attempt_marker = Path("/tmp/general_nvidia_api_attempted")
    success_marker = Path("/tmp/general_nvidia_api_succeeded")
    for marker in (attempt_marker, success_marker):
        try:
            marker.unlink()
        except FileNotFoundError:
            pass
    attempt_marker.write_text(
        datetime.now(timezone.utc).isoformat(), encoding="utf-8"
    )

    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            status = getattr(response, "status", 200)
            headers = response.headers
            raw = response.read().decode("utf-8")
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            save_usage_observation(build_usage_observation(status, headers, None), request_label=request_label)
            raise RuntimeError(
                "Research model returned invalid JSON; usage observation was saved; no retry attempted."
            ) from None
        save_usage_observation(build_usage_observation(status, headers, result), request_label=request_label)
    except urllib.error.HTTPError as exc:
        save_usage_observation(build_usage_observation(exc.code, exc.headers, None), request_label=request_label)
        if exc.code == 429:
            raise RuntimeError(
                "NVIDIA returned HTTP 429 (rate limit). No automatic retry was attempted; "
                "inspect " + request_usage_name + "."
            ) from None
        if exc.code == 402:
            raise RuntimeError(
                "NVIDIA returned HTTP 402 (provider reported insufficient credits/access). "
                "No automatic retry was attempted; inspect account access and usage metadata."
            ) from None
        raise RuntimeError(
            "Research model HTTP error " + str(exc.code) +
            "; no automatic retry was attempted; inspect " + request_usage_name + "."
        ) from None
    except (urllib.error.URLError, TimeoutError):
        save_usage_observation(build_usage_observation(None, None, None), request_label=request_label)
        raise RuntimeError(
            "Research model connection/timeout error. Whether the provider received the request "
            "is unknown; no automatic retry was attempted."
        ) from None
    choices = result.get("choices", [])
    message = choices[0].get("message", {}) if choices else {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(
            "Research model returned no final content. Inspect " + request_usage_name + "; "
            "no automatic retry was attempted."
        )
    success_marker.write_text(
        datetime.now(timezone.utc).isoformat(), encoding="utf-8"
    )
    return content.strip()

def main() -> int:
    try:
        ledger_path = BASE / "general_evaluation_ledger.json"
        # Repeat preflight inside the runner so a direct invocation cannot bypass
        # the workflow's no-request gate after validation dates have been consumed.
        sealed.preflight(ARCHIVE, ledger_path)
        rows = parse_archive()
        target, train, _initial_plan = isolate_discovery_only(rows, ledger_path=ledger_path)
        # Bind the plan to exact archive bytes, then recheck it has not changed.
        archive_proof = sealed.load_archive(ARCHIVE)
        if archive_proof["rows"] != rows:
            raise RuntimeError("Archive changed between validation and sealed split.")
        current_ledger = sealed.load_ledger(ledger_path)
        used_dates = sealed.get_used_validation_dates(current_ledger)
        partition = sealed.split_archive(
            rows,
            archive_sha256=archive_proof["archive_sha256"],
            used_validation_dates=used_dates,
        )
        sealed_plan = sealed.public_plan(partition)
        if partition["target_date"] != target or partition["discovery_rows"] != train:
            raise RuntimeError("Sealed discovery split changed before prompt construction.")
        (BASE / "open_research_sealed_plan.json").write_text(
            json.dumps(sealed_plan, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # Each direction gets its own request and data payload. The reverse request
        # never receives the forward-ordered payload, and vice versa.
        forward_messages = build_messages(
            target, train, os.environ.get("TASK_TEXT", ""), direction="forward"
        )
        report_forward = call_model(forward_messages, request_label="forward")
        FORWARD_REPORT.write_text(report_forward.strip() + "\n", encoding="utf-8")

        reverse_messages = build_messages(
            target, train, os.environ.get("TASK_TEXT", ""), direction="reverse"
        )
        report_reverse = call_model(reverse_messages, request_label="reverse")
        REVERSE_REPORT.write_text(report_reverse.strip() + "\n", encoding="utf-8")

        forward_result = parse_directional_result(report_forward, "forward")
        reverse_result = parse_directional_result(report_reverse, "reverse")
        proposal_document, execution_spec_document = combine_directional_results(
            forward_result, reverse_result
        )
        usage_summary = write_combined_usage_summary()

        PROPOSALS.write_text(
            json.dumps(proposal_document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        EXECUTION_SPECS.write_text(
            json.dumps(execution_spec_document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        forward_proposals = forward_result["proposal_document"]["proposals"]
        reverse_proposals = reverse_result["proposal_document"]["proposals"]
        forward_specs = forward_result["execution_spec_document"]["specifications"]
        reverse_specs = reverse_result["execution_spec_document"]["specifications"]
        proposal_note = (
            "\n\n## Directional discovery and sealed preflight metadata\n\n"
            "- Forward discovery request data: forward chronological rows only.\n"
            "- Reverse discovery request data: reverse-ordered rows only; no forward payload was included.\n"
            "- The two requests were parsed independently before their direction-namespaced IDs were combined.\n"
            f"- Forward proposal parser status: {forward_result['proposal_document']['status']}\n"
            f"- Forward validated proposal count: {len(forward_proposals)}\n"
            f"- Forward validated spec count: {len(forward_specs)}\n"
            f"- Reverse proposal parser status: {reverse_result['proposal_document']['status']}\n"
            f"- Reverse validated proposal count: {len(reverse_proposals)}\n"
            f"- Reverse validated spec count: {len(reverse_specs)}\n"
            f"- Combined proposal status: {proposal_document['status']}\n"
            f"- Combined directional spec status: {execution_spec_document['status']}\n"
            f"- Both directional DSL specs passed synthetic tests: {execution_spec_document['synthetic_tests_passed']}\n"
            "- Real-archive ranking was not executed by discovery.\n"
            "- Method registry modified: NO.\n"
            "- Candidate promotion/approval: NO; hypotheses remain unverified.\n"
            "- Forward sealed R60 validation window: "
            f"{sealed_plan['regimes']['R60']['validation_start_date']} to "
            f"{sealed_plan['regimes']['R60']['validation_end_date']} "
            f"({sealed_plan['regimes']['R60']['validation_count']} draws).\n"
            "- Reverse sealed R60 validation window: "
            f"{sealed_plan['regimes']['R60']['reverse_validation_start_date']} to "
            f"{sealed_plan['regimes']['R60']['reverse_validation_end_date']} "
            f"({sealed_plan['regimes']['R60']['reverse_validation_count']} draws).\n"
            "- Reverse results remain retrospective stress tests, never predictive evidence.\n"
            "- Rule-change requests from the reverse report are saved separately and are not automatically submitted.\n"
        )
        usage_note = (
            "\n\n## NVIDIA usage observation\n\n"
            "- Provider account-level quota was not queried; remaining quota is UNKNOWN.\n"
            "- Directional requests made by this run: "
            f"{usage_summary['inference_requests_made_by_this_run']}; automatic retries: 0.\n"
            "- Forward/reverse response usage and safe headers are recorded separately in "
            "open_research_usage_forward.json and open_research_usage_reverse.json.\n"
            "- Combined metadata is in open_research_usage.json.\n"
            "- Missing usage/limit data is UNKNOWN, not unlimited or proof of free use.\n"
        )
        header = (
            "# GENERAL Bidirectional Blank-Slate Research Report\n\n"
            f"- Runner version: {VERSION}\n"
            f"- Generated (UTC): {datetime.now(timezone.utc).isoformat()}\n"
            f"- Isolated target date: {target}\n"
            f"- Pre-target discovery rows available before directional partitioning: {len(train)}\n"
            "- Hosted discovery requests: two separate direction-isolated calls.\n"
            "- Sealed validation rows sent to either model request: NO.\n"
            "- Target numbers sent to either model request: NO.\n"
            "- Target/future records sent to either model request: NO.\n"
            "- Legacy method registry, candidate files, evidence catalog, and project memory loaded: NO.\n"
            "- Status: EXPLORATORY HYPOTHESES ONLY; NOT VALIDATED; NOT CANONICAL.\n\n"
        )
        REPORT.write_text(
            header
            + "## Forward-direction report\n\n"
            + report_forward
            + proposal_note
            + usage_note,
            encoding="utf-8",
        )
        reverse_header = (
            "# GENERAL Reverse-Direction Research Report\n\n"
            f"- Runner version: {VERSION}\n"
            f"- Generated (UTC): {datetime.now(timezone.utc).isoformat()}\n"
            f"- Isolated target date: {target}\n"
            "- Input: only reverse-ordered discovery rows; forward-ordered data was not included.\n"
            "- This direction is retrospective stress testing, not predictive evidence.\n"
            "- Rule-change requests in this report are not automatically submitted.\n\n"
        )
        REVERSE_REPORT.write_text(
            reverse_header
            + report_reverse
            + "\n\n## Parsed directional status\n\n"
            + f"- Proposal status: {reverse_result['proposal_document']['status']}\n"
            + f"- Validated proposal count: {len(reverse_proposals)}\n"
            + f"- Validated execution-spec count: {len(reverse_specs)}\n",
            encoding="utf-8",
        )
        print("OPEN RESEARCH: completed two isolated directional calls")
        print("Target date isolated:", target)
        print("Pre-target discovery rows supplied per directional view:", len(train))
        print("Sealed validation rows sent to either model request: NO")
        print("Target numbers exposed: NO")
        print("Reverse report is retrospective only: YES")
        print("Provider quota checked before request: NO; account-level quota remains UNKNOWN")
        print("Requests:", usage_summary["inference_requests_made_by_this_run"], "| automatic retries: 0")
        print("Usage observation:", USAGE_REPORT.name)
        print("Proposal status:", proposal_document["status"])
        print("Validated directional proposals:", len(proposal_document["proposals"]))
        print("Execution-spec status:", execution_spec_document["status"])
        print("DSL-validated directional specifications:", len(execution_spec_document["specifications"]))
        print("Both directional synthetic tests passed:", execution_spec_document["synthetic_tests_passed"])
        print("Real-archive execution: NO | Execution approval: NO")
        print("Method registry modified: NO")
        print("Proposal file:", PROPOSALS.name)
        print("Execution-spec file:", EXECUTION_SPECS.name)
        print("Forward report path:", FORWARD_REPORT.name)
        print("Reverse report path:", REVERSE_REPORT.name)
        print("Report path:", REPORT.name)
        return 0
    except Exception as exc:
        print("OPEN RESEARCH FAILED:", str(exc))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
