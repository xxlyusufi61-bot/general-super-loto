"""Blank-slate Super Loto research runner.

This module intentionally does not import the legacy method registry, candidate
implementations, evidence catalog, or project memory. It validates the archive,
isolates the latest draw locally, and sends only strictly pre-target draws plus
governance rules to the research model. Its output is a hypothesis report, not
a verified formula or prediction.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import general_data_guard as guard

VERSION = "0.1.0"
MODEL = "z-ai/glm-5.3-flash"
BASE = Path(__file__).resolve().parent
ARCHIVE = BASE / os.environ.get("GENERAL_ARCHIVE_FILE", "super_loto_archive.json")
REPORT = BASE / "open_research_report.md"
API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

GOVERNANCE_RULES = (
    "Keep the target draw hidden: do not use target or later draw values for discovery, "
    "training, feature construction, tuning, or selection.",
    "Use only draws strictly earlier than the isolated target date.",
    "Stop if archive structure, dates, chronology, number sets, or target partition fail validation.",
    "Explore the data without presupposing any previously known formula, feature family, transform, "
    "legacy implementation, prior weight, or historical result.",
    "Do not read or rely on legacy formula files, method registries, candidate implementations, "
    "evidence catalogs, or previous project-memory reports.",
    "Treat all ideas as hypotheses until tested with reproducible code and chronological evaluation.",
    "Do not invent data, tests, results, or mathematical definitions; say when a test has not been run.",
    "Separate discovery/selection data from a frozen chronological evaluation interval.",
    "Use forward walk-forward evaluation and time-ordered cross-validation; reverse chronology, if used, "
    "must be labelled retrospective stress testing and not forward predictive evidence.",
    "Compare candidates with an appropriate random baseline and report uncertainty and search limits.",
    "A valid outcome may be that no robust signal is found. Never promise lottery predictions.",
    "If a governance rule blocks the research or a change is proposed, do not alter it. Produce a structured "
    "rule-change request marked PENDING_USER_APPROVAL. Do not claim it was sent to Telegram: automated "
    "Telegram request/approval handling is not implemented by this runner. Until that integration exists "
    "and the user explicitly approves the exact request, keep the existing rule.",
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
    # Construct a fresh payload from only pre-target rows; never pass the source
    # archive or target record to the model.
    clean_train = [{"date": row["date"], "numbers": list(row["numbers"])} for row in train]
    return target, clean_train

def build_messages(target: str, train: list[dict[str, Any]], focus: str = "") -> list[dict[str, str]]:
    system = (
        "You are the GENERAL blank-slate research agent for Super Loto.\n"
        "You have no prior formulas or legacy research context. Discover candidate "
        "relationships from the supplied historical data rather than assuming a known method.\n"
        "Governance rules:\n- " + "\n- ".join(GOVERNANCE_RULES) + "\n"
        "The target date is metadata only; its numbers and every record on or after it are absent. "
        "Do not infer or fabricate those values. The data supplied is discovery history, not a frozen "
        "final test set. Do not claim a candidate is validated or predictive based on inspection alone.\n"
        "Return a concise report with: (1) observations, (2) clearly labelled candidate hypotheses, "
        "(3) exact reproducible tests needed, (4) leakage/overfitting risks, and (5) what was not tested. "
        "Do not output winning-number predictions. A no-signal conclusion is acceptable. "
        "If and only if you recommend changing a binding rule, include exactly one structured block using "
        "the markers shown below, with every field filled by a factual, concise string. Do not state that "
        "the request was sent or approved. No rule changes during this research call.\\n"
        "<RULE_CHANGE_REQUEST_JSON>\\n"
        "{\\\"rule_id\\\":\\\"stable rule identifier\\\",\\\"current_rule\\\":\\\"exact current wording\\\","
        "\\\"proposed_rule\\\":\\\"proposed wording\\\",\\\"reason\\\":\\\"why change is needed\\\","
        "\\\"evidence\\\":\\\"available evidence and its limitations\\\","
        "\\\"risk_assessment\\\":\\\"risks and safeguards\\\","
        "\\\"tests_required\\\":\\\"tests required before implementation\\\","
        "\\\"rollback_plan\\\":\\\"how to revert\\\"}\\n"
        "</RULE_CHANGE_REQUEST_JSON>\\n"
        "If no rule change is needed, omit the entire block. Request proposals are submitted separately "
        "for user approval; malformed or incomplete blocks are rejected."
    )
    user = {
        "task": "Perform exploratory, blank-slate hypothesis discovery only. Do not output number predictions.",
        "user_research_focus_untrusted": str(focus)[:1000],
        "target_date_metadata_only": target,
        "pre_target_draw_count": len(train),
        "pre_target_history": train,
        "target_numbers_included": False,
        "post_target_records_included": False,
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user, ensure_ascii=False, separators=(",", ":"))},
    ]

def call_model(messages: list[dict[str, str]]) -> str:
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
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Research model HTTP error: " + str(exc.code)) from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("Research model connection/timeout error.") from None
    choices = result.get("choices", [])
    message = choices[0].get("message", {}) if choices else {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Research model returned no final content.")
    return content.strip()

def main() -> int:
    try:
        rows = parse_archive()
        target, train = isolate_pre_target(rows)
        messages = build_messages(target, train, os.environ.get("TASK_TEXT", ""))
        report = call_model(messages)
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
        REPORT.write_text(header + report + "\n", encoding="utf-8")
        print("OPEN RESEARCH: completed")
        print("Target date isolated:", target)
        print("Pre-target draws supplied:", len(train))
        print("Target numbers exposed: NO")
        print("Legacy research context loaded: NO")
        print("Report status: EXPLORATORY ONLY; NOT VALIDATED")
        print("Report path:", REPORT.name)
        return 0
    except Exception as exc:
        print("OPEN RESEARCH FAILED:", str(exc))
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
