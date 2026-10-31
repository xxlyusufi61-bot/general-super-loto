"""Blank-slate Super Loto research runner with cautious NVIDIA usage observation.

This module intentionally does not import the legacy method registry, candidate
implementations, evidence catalog, or project memory. It validates the archive,
isolates the latest draw locally, and sends only strictly pre-target draws plus
governance rules to the research model. Its output is a hypothesis report, not
a verified formula or prediction.

NVIDIA's account-level rate limit/quota is not queried here. The runner records
only safe usage metadata returned by the same inference request, when provided.
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

VERSION = "0.3.0"
MODEL = "z-ai/glm-5.3-flash"
BASE = Path(__file__).resolve().parent
ARCHIVE = BASE / os.environ.get("GENERAL_ARCHIVE_FILE", "super_loto_archive.json")
REPORT = BASE / "open_research_report.md"
PROPOSALS = BASE / "open_research_proposals.json"
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
    "Keep R54 (before 2020-08-02) and R60 (from 2020-08-02) separate. Do not pool their draw rows; "
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

def build_messages(target: str, train: list[dict[str, Any]], focus: str = "") -> list[dict[str, str]]:
    system = (
        "You are the GENERAL blank-slate research agent for Super Loto.\n"
        "You have no prior formulas or legacy research context. Discover candidate "
        "relationships from the supplied historical data rather than assuming a known method.\n"
        "Research notation:\n- " + "\n- ".join(NOTATION_RULES) + "\n"
        "Governance rules:\n- " + "\n- ".join(GOVERNANCE_RULES) + "\n"
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
        "{\"schema_version\":1,\"proposals\":[{"
        "\"hypothesis_id\":\"HYP-001\","
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
        "\"uniform_random_6_of_60\"]}]}\n"
        "</HYPOTHESIS_PROPOSALS_JSON>\n"
        "Use exactly the schema keys shown; do not add prediction numbers, source code, weights chosen from hidden "
        "targets, or any claim of validation. Proposed family names are descriptive only: they do not create or "
        "change a method registry entry. An empty proposals list is an acceptable result."
    )
    target_day = date.fromisoformat(target)
    r54 = [row for row in train if date.fromisoformat(row["date"]) < date(2020, 8, 2)]
    r60 = [row for row in train if date.fromisoformat(row["date"]) >= date(2020, 8, 2)]
    if len(r54) + len(r60) != len(train):
        raise RuntimeError("R54/R60 partition count mismatch; open research stopped.")
    if any(date.fromisoformat(row["date"]) >= target_day for row in r54 + r60):
        raise RuntimeError("Target/future record found in a regime partition.")

    user = {
        "task": "Perform exploratory, blank-slate hypothesis discovery only. Do not output number predictions.",
        "user_research_focus_untrusted": str(focus)[:1000],
        "target_date_metadata_only": target,
        "pre_target_draw_count": len(train),
        "pre_target_history_by_regime": {"R54": r54, "R60": r60},
        "regime_counts": {"R54": len(r54), "R60": len(r60)},
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

def save_usage_observation(observation: dict[str, Any]) -> None:
    try:
        USAGE_REPORT.write_text(
            json.dumps(observation, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise RuntimeError("Could not write NVIDIA usage observation report.") from exc

def call_model(messages: list[dict[str, str]]) -> str:
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
    # Fail closed unless the operator has independently verified that this
    # single hosted request is within the currently documented free terms.
    # If NVIDIA exposes only rate limits and no remaining-free-usage status,
    # this gate must remain closed: rate limits are not proof of free allowance.
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
            save_usage_observation(build_usage_observation(status, headers, None))
            raise RuntimeError(
                "Research model returned invalid JSON; usage observation was saved; no retry attempted."
            ) from None
        save_usage_observation(build_usage_observation(status, headers, result))
    except urllib.error.HTTPError as exc:
        save_usage_observation(build_usage_observation(exc.code, exc.headers, None))
        if exc.code == 429:
            raise RuntimeError(
                "NVIDIA returned HTTP 429 (rate limit). No automatic retry was attempted; "
                "inspect open_research_usage.json."
            ) from None
        if exc.code == 402:
            raise RuntimeError(
                "NVIDIA returned HTTP 402 (provider reported insufficient credits/access). "
                "No automatic retry was attempted; inspect account access and usage metadata."
            ) from None
        raise RuntimeError(
            "Research model HTTP error " + str(exc.code) +
            "; no automatic retry was attempted; inspect open_research_usage.json."
        ) from None
    except (urllib.error.URLError, TimeoutError):
        save_usage_observation(build_usage_observation(None, None, None))
        raise RuntimeError(
            "Research model connection/timeout error. Whether the provider received the request "
            "is unknown; no automatic retry was attempted."
        ) from None
    choices = result.get("choices", [])
    message = choices[0].get("message", {}) if choices else {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(
            "Research model returned no final content. Inspect open_research_usage.json; "
            "no automatic retry was attempted."
        )
    success_marker.write_text(
        datetime.now(timezone.utc).isoformat(), encoding="utf-8"
    )
    return content.strip()

def main() -> int:
    try:
        rows = parse_archive()
        target, train = isolate_pre_target(rows)
        messages = build_messages(target, train, os.environ.get("TASK_TEXT", ""))
        report = call_model(messages)
        proposal_document = parse_hypothesis_proposals(report)
        PROPOSALS.write_text(
            json.dumps(proposal_document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        proposal_note = (
            "\n\n## Structured hypothesis proposals\n\n"
            f"- Proposal parser status: {proposal_document['status']}\n"
            f"- Validated proposal count: {len(proposal_document['proposals'])}\n"
            "- Execution ready: NO\n"
            "- Method registry modified: NO\n"
            "- Hypotheses are unverified proposals only; no implementation or scoring was run.\n"
        )
        usage_note = (
            "\n\n## NVIDIA usage observation\n\n"
            "- Provider quota checked before request: NO; no account-level remaining-quota API "
            "was identified in the reviewed public documentation.\n"
            "- Request count made by this run: 1; automatic retries: 0.\n"
            "- Provider rate-limit headers/token usage, if returned, are in "
            "`open_research_usage.json`.\n"
            "- Missing usage/limit data is UNKNOWN, not unlimited or proof of free use.\n"
        )
        header = (
            "# GENERAL Blank-Slate Research Report\n\n"
            f"- Runner version: {VERSION}\n"
            f"- Generated (UTC): {datetime.now(timezone.utc).isoformat()}\n"
            f"- Isolated target date: {target}\n"
            f"- Pre-target draws supplied: {len(train)}\n"
            "- Target numbers sent to model: NO\n"
            "- Target/future records sent to model: NO\n"
            "- Legacy method registry, candidate files, evidence catalog, and project memory loaded: NO\n"
            "- Status: EXPLORATORY HYPOTHESES ONLY; NOT VALIDATED; NOT CANONICAL\n\n"
        )
        REPORT.write_text(header + report + proposal_note + usage_note, encoding="utf-8")
        print("OPEN RESEARCH: completed")
        print("Target date isolated:", target)
        print("Pre-target draws supplied:", len(train))
        print("Target numbers exposed: NO")
        print("Legacy research context loaded: NO")
        print("Provider quota checked before request: NO; no documented account quota API was identified")
        print("Request count:", 1, "| automatic retries:", 0)
        print("Usage observation:", USAGE_REPORT.name)
        print("Report status: EXPLORATORY ONLY; NOT VALIDATED")
        print("Proposal status:", proposal_document["status"])
        print("Validated proposals:", len(proposal_document["proposals"]))
        print("Execution ready: NO | Method registry modified: NO")
        print("Proposal file:", PROPOSALS.name)
        print("Report path:", REPORT.name)
        return 0
    except Exception as exc:
        print("OPEN RESEARCH FAILED:", str(exc))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
