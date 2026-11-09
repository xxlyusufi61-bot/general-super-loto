"""Bounded offline symbolic discovery, chronological testing, and GENERAL-only memory.

This module never imports a hosted-model client and never makes network requests.
It consumes only the sealed pre-target discovery partition. Development-window
dates are tracked in a dedicated append-only sub-ledger so they cannot be reused.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import general_hypothesis_execution as engine
import general_sealed_evaluation as sealed
from general_hypothesis_proposals import (
    END_MARKER,
    MODEL_PROPOSAL_KEYS,
    SCHEMA_VERSION as PROPOSAL_SCHEMA_VERSION,
    START_MARKER,
    execution_spec_matches_proposal,
    parse_hypothesis_proposals,
)

VERSION = "0.1.0"
MEMORY_SCHEMA_VERSION = 1
MIN_TRAIN_DRAWS = engine.DEFAULT_MIN_TRAIN_DRAWS
DEVELOPMENT_WINDOW_DRAWS = 10
CV_FOLDS = 5
MAX_CANDIDATES = 16
MAX_MEMORY_ENTRIES = 10000
SOURCE = "GENERAL/offline-symbolic-discovery"
PURPOSE = (
    "Append-only memory and development-date consumption ledger for GENERAL's "
    "bounded offline symbolic hypothesis engine. No legacy experiments, raw "
    "archive rows, draw-number sets, per-draw scores, or target values."
)
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class OfflineResearchError(RuntimeError):
    """Raised when offline discovery or its append-only memory is unsafe."""


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key.")
        result[key] = value
    return result


def _read_json(path: str | Path, description: str) -> Any:
    try:
        payload = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError("Non-finite JSON number: " + value)
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise OfflineResearchError(description + " is missing or invalid JSON.") from None
    return payload


def _write_json_atomic(path: str | Path, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)


def _canonical_day(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise OfflineResearchError(field + " must be an ISO date.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise OfflineResearchError(field + " is not a valid ISO date.") from None
    if parsed.isoformat() != value:
        raise OfflineResearchError(field + " must use YYYY-MM-DD.")
    return value


def _var(name: str) -> dict[str, Any]:
    return {"op": "var", "name": name}


def _const(value: int | float) -> dict[str, Any]:
    return {"op": "const", "value": value}


def _binary(op: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {"op": op, "left": left, "right": right}


def _and(*items: dict[str, Any]) -> dict[str, Any]:
    if not items:
        raise OfflineResearchError("An empty boolean conjunction is not a hypothesis.")
    result = items[0]
    for item in items[1:]:
        result = _binary("and", result, item)
    return result


def _not(item: dict[str, Any]) -> dict[str, Any]:
    return {"op": "not", "arg": item}


def _number_count(expression: dict[str, Any]) -> dict[str, Any]:
    return {
        "op": "aggregate_history_numbers",
        "aggregate": "count",
        "expression": expression,
    }


def _triple_count(expression: dict[str, Any]) -> dict[str, Any]:
    return {
        "op": "aggregate_history_triples",
        "aggregate": "count",
        "expression": expression,
    }


def _make_spec(expression: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "score_expression": expression}


def _candidate_templates() -> list[tuple[str, str, str, dict[str, Any]]]:
    a = _var("a_contains_candidate")
    b = _var("b_contains_candidate")
    c = _var("c_contains_candidate")
    value = _var("value")
    candidate = _var("candidate")
    draw_age = _var("draw_age")
    same_value = _binary("eq", value, candidate)
    same_mod5 = _binary(
        "eq", _binary("mod", value, _const(5)),
        _binary("mod", candidate, _const(5)),
    )
    templates: list[tuple[str, str, str, dict[str, Any]]] = [
        (
            "A→B→C", "abc_persistent_membership",
            "Count consecutive A/B/C triples in which the candidate is present in all three draw states.",
            _make_spec(_triple_count(_and(a, b, c))),
        ),
        (
            "A→B→C", "abc_disappearing_membership",
            "Count consecutive A/B/C triples in which the candidate is present in A and B but absent in C.",
            _make_spec(_triple_count(_and(a, b, _not(c)))),
        ),
        (
            "A→B→C", "abc_new_persistent_membership",
            "Count consecutive A/B/C triples in which the candidate is absent in A and present in B and C.",
            _make_spec(_triple_count(_and(_not(a), b, c))),
        ),
        (
            "A→B→C", "abc_reappearance_membership",
            "Count consecutive A/B/C triples in which the candidate is present in A and C but absent in B.",
            _make_spec(_triple_count(_and(a, _not(b), c))),
        ),
        (
            "A→T", "at_exact_historical_frequency",
            "Count exact occurrences of the candidate across the permitted earlier draw history.",
            _make_spec(_number_count(same_value)),
        ),
        (
            "A→T", "at_recent_exact_frequency",
            "Count exact candidate occurrences within the ten most recent available earlier draws.",
            _make_spec(_number_count(_and(
                same_value, _binary("lte", draw_age, _const(9))
            ))),
        ),
        (
            "A→T", "at_neighbourhood_one",
            "Count earlier draw values whose absolute distance from the candidate is at most one.",
            _make_spec(_number_count(_binary(
                "lte",
                {"op": "abs", "arg": _binary("sub", value, candidate)},
                _const(1),
            ))),
        ),
        (
            "A→T", "at_same_parity",
            "Count earlier draw values with the same modulo-two remainder as the candidate.",
            _make_spec(_number_count(_binary(
                "eq",
                _binary("mod", value, _const(2)),
                _binary("mod", candidate, _const(2)),
            ))),
        ),
        (
            "A→T", "at_same_final_digit",
            "Count earlier draw values with the same modulo-ten remainder as the candidate.",
            _make_spec(_number_count(_binary(
                "eq",
                _binary("mod", value, _const(10)),
                _binary("mod", candidate, _const(10)),
            ))),
        ),
        (
            "MOD5→T", "mod5_same_residue_frequency",
            "Count earlier values in the candidate's explicitly defined non-negative modulo-five residue class.",
            _make_spec(_number_count(same_mod5)),
        ),
        (
            "MOD5→T", "mod5_recent_same_residue_frequency",
            "Count same-residue modulo-five values within the ten most recent available earlier draws.",
            _make_spec(_number_count(_and(
                same_mod5, _binary("lte", draw_age, _const(9))
            ))),
        ),
    ]
    if len(templates) > MAX_CANDIDATES:
        raise OfflineResearchError("The bounded symbolic candidate catalogue exceeded its hard limit.")
    return templates


def _parse_one_proposal(
    hypothesis_id: str,
    direction: str,
    family: str,
    rationale: str,
    spec: dict[str, Any],
) -> dict[str, Any]:
    raw = {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "proposals": [{
            "hypothesis_id": hypothesis_id,
            "discovery_direction": direction,
            "proposed_family": family,
            "research_rationale": rationale,
            "known_risks": [
                "A descriptive historical association may not persist in later draws.",
                "The bounded DSL cannot represent every possible interpretation of this research-family label.",
                "Selection and evaluation are exploratory and do not establish predictive advantage.",
            ],
            "formal_execution_spec": spec,
        }],
    }
    report = START_MARKER + json.dumps(raw, ensure_ascii=False, allow_nan=False) + END_MARKER
    parsed = parse_hypothesis_proposals(report)
    if parsed.get("status") != "VALID_PROPOSALS" or len(parsed.get("proposals", [])) != 1:
        raise OfflineResearchError("A generated proposal failed the existing proposal contract.")
    proposal = parsed["proposals"][0]
    if not execution_spec_matches_proposal(proposal, spec):
        raise OfflineResearchError("Generated proposal semantics do not exactly match its safe DSL AST.")
    return proposal


def generate_hypotheses(training_history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate a fixed, bounded DSL search catalogue from training data only."""
    checked = engine._validate_rows(
        training_history,
        pool_size=60,
        field="offline_discovery_history",
        maximum=engine.MAX_INPUT_HISTORY_DRAWS,
    )
    if len(checked) < MIN_TRAIN_DRAWS:
        raise OfflineResearchError("At least the existing 40-draw minimum is required for hypothesis generation.")
    output = []
    for index, (family, name, rationale, raw_spec) in enumerate(_candidate_templates(), start=1):
        spec = engine.validate_execution_spec(raw_spec)
        synthetic = engine.run_synthetic_contract_tests(spec)
        if synthetic.get("status") != "PASS":
            raise OfflineResearchError("A generated DSL candidate failed deterministic synthetic execution.")
        proposal = _parse_one_proposal(
            "HYP-" + str(index).zfill(3),
            "forward",
            family,
            rationale,
            spec,
        )
        output.append({
            "hypothesis_id": proposal["hypothesis_id"],
            "family": family,
            "template_id": name,
            "proposal": proposal,
            "execution_spec": spec,
            "specification_sha256": _sha(_canonical(spec)),
            "synthetic_tests": {
                "status": synthetic["status"],
                "synthetic_cases": synthetic["synthetic_cases"],
                "ranked_values_checked": synthetic["ranked_values_checked"],
                "target_values_used": False,
                "real_archive_used": False,
            },
        })
    return output


def _metric_summary(report: dict[str, Any]) -> dict[str, Any]:
    required = (
        "mean_hits_out_of_6",
        "uniform_random_expected_hits_out_of_6",
        "validation_draw_count",
    )
    if report.get("status") != "EVALUATED":
        raise OfflineResearchError("A chronological evaluation did not complete.")
    result: dict[str, Any] = {}
    for key in required:
        value = report.get(key)
        if key == "validation_draw_count":
            if type(value) is not int or value < 1:
                raise OfflineResearchError("Evaluation draw count is invalid.")
        elif type(value) not in {int, float} or not math.isfinite(float(value)):
            raise OfflineResearchError("Evaluation aggregate metric is invalid.")
        result[key] = value
    standard_error = report.get("standard_error_of_mean_hits")
    if standard_error is not None and (
        type(standard_error) not in {int, float} or not math.isfinite(float(standard_error))
    ):
        raise OfflineResearchError("Evaluation uncertainty summary is invalid.")
    result["standard_error_of_mean_hits"] = standard_error
    result["predictive_claim"] = False
    return result


def _reverse_metric_summary(report: dict[str, Any]) -> dict[str, Any]:
    if (
        report.get("status") != "EVALUATED_RETROSPECTIVE_ONLY"
        or report.get("predictive_evidence") is not False
        or report.get("current_isolated_target_included") is not False
    ):
        raise OfflineResearchError("Reverse diagnostics failed their retrospective-only safety contract.")
    return {
        "status": report["status"],
        "mean_hits_out_of_6": report["mean_hits_out_of_6"],
        "standard_error_of_mean_hits": report["standard_error_of_mean_hits"],
        "evaluated_draw_count": report["evaluated_draw_count"],
        "uniform_random_expected_hits_out_of_6": report["uniform_random_expected_hits_out_of_6"],
        "predictive_evidence": False,
    }


def evaluate_hypotheses(
    training_history: list[dict[str, Any]],
    development_draws: list[dict[str, Any]],
    *,
    pre_target_discovery_history: list[dict[str, Any]],
    target_date: str,
    folds: int = CV_FOLDS,
) -> dict[str, Any]:
    """Run fixed candidates through forward walk-forward, blocked CV and reverse-only diagnostics."""
    target = _canonical_day(target_date, "target_date")
    training = engine._validate_rows(
        training_history,
        pool_size=60,
        field="offline_training_history",
        maximum=engine.MAX_INPUT_HISTORY_DRAWS,
    )
    validation = engine._validate_rows(
        development_draws,
        pool_size=60,
        field="offline_development_draws",
        maximum=engine.MAX_VALIDATION_DRAWS,
    )
    pre_target = engine._validate_rows(
        pre_target_discovery_history,
        pool_size=60,
        field="offline_pre_target_discovery_history",
        maximum=engine.MAX_INPUT_HISTORY_DRAWS,
    )
    if len(training) < MIN_TRAIN_DRAWS:
        raise OfflineResearchError("Development split leaves fewer than 40 training draws.")
    if len(validation) < folds or folds < 2:
        raise OfflineResearchError("Development interval is too short for the fixed chronological fold count.")
    if date.fromisoformat(training[-1]["date"]) >= date.fromisoformat(validation[0]["date"]):
        raise OfflineResearchError("Training data must end before the development interval begins.")
    if date.fromisoformat(validation[-1]["date"]) >= date.fromisoformat(target):
        raise OfflineResearchError("Development interval includes the isolated target or a later date.")
    if date.fromisoformat(pre_target[-1]["date"]) >= date.fromisoformat(target):
        raise OfflineResearchError("Reverse diagnostic input contains the target or a later record.")

    candidates = generate_hypotheses(training)
    results = []
    for candidate in candidates:
        spec = candidate["execution_spec"]
        forward = engine.evaluate_holdout(
            training,
            validation,
            cutoff_date=target,
            number_pool_size=60,
            execution_spec=spec,
            min_train_draws=MIN_TRAIN_DRAWS,
        )
        cross_validation = engine.evaluate_time_ordered_cv(
            training,
            validation,
            cutoff_date=target,
            number_pool_size=60,
            execution_spec=spec,
            folds=folds,
            min_train_draws=MIN_TRAIN_DRAWS,
        )
        reverse = engine.evaluate_reverse_retrospective_stress(
            pre_target,
            cutoff_date=target,
            number_pool_size=60,
            execution_spec=spec,
            test_draw_count=min(DEVELOPMENT_WINDOW_DRAWS, engine.MAX_VALIDATION_DRAWS),
            min_train_draws=MIN_TRAIN_DRAWS,
        )
        if cross_validation.get("current_isolated_target_included") is not False:
            raise OfflineResearchError("Cross-validation did not preserve target isolation.")
        results.append({
            "hypothesis_id": candidate["hypothesis_id"],
            "family": candidate["family"],
            "template_id": candidate["template_id"],
            "proposal": candidate["proposal"],
            "execution_spec": spec,
            "specification_sha256": candidate["specification_sha256"],
            "synthetic_tests": candidate["synthetic_tests"],
            "forward_walk_forward": _metric_summary(forward),
            "time_ordered_cross_validation": _metric_summary(cross_validation),
            "reverse_retrospective_only": _reverse_metric_summary(reverse),
        })
    return {
        "schema_version": 1,
        "runner_version": VERSION,
        "status": "COMPLETED_EXPLORATORY_ONLY",
        "target_date": target,
        "direction": "forward",
        "research_family_labels": ["A→B→C", "A→T", "MOD5→T"],
        "training_draw_count": len(training),
        "training_start_date": training[0]["date"],
        "training_end_date": training[-1]["date"],
        "development_draw_count": len(validation),
        "development_window_sha256": _sha(_canonical([row["date"] for row in validation])),
        "candidates": results,
        "candidate_selection": "NONE",
        "automatic_candidate_selection": False,
        "method_registry_modified": False,
        "target_numbers_exposed": False,
        "target_or_future_rows_used": False,
        "development_interval_is_confirmatory": False,
        "interpretation": (
            "All candidates were generated from the earlier training prefix before the development interval was evaluated. "
            "The development interval is consumed for exploratory walk-forward/CV feedback and must not be reused. "
            "Walk-forward and CV overlap by design and are not independent confirmation. Reverse results are retrospective only. "
            "No candidate was selected, promoted, or declared predictive."
        ),
    }


def empty_memory() -> dict[str, Any]:
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "purpose": PURPOSE,
        "entries": [],
        "consumed_development_windows": [],
    }


def _validate_candidate(candidate: Any) -> dict[str, Any]:
    keys = {
        "hypothesis_id", "family", "template_id", "proposal",
        "execution_spec", "specification_sha256", "synthetic_tests",
        "forward_walk_forward", "time_ordered_cross_validation",
        "reverse_retrospective_only",
    }
    if not isinstance(candidate, dict) or set(candidate) != keys:
        raise OfflineResearchError("Stored offline hypothesis has invalid fields.")
    proposal = candidate["proposal"]
    if not isinstance(proposal, dict):
        raise OfflineResearchError("Stored proposal is invalid.")
    model_view = {key: proposal.get(key) for key in MODEL_PROPOSAL_KEYS}
    reparsed = _parse_one_proposal(
        model_view.get("hypothesis_id"),
        model_view.get("discovery_direction"),
        model_view.get("proposed_family"),
        model_view.get("research_rationale"),
        model_view.get("formal_execution_spec"),
    )
    if reparsed != proposal:
        raise OfflineResearchError("Stored proposal differs from the canonical proposal contract.")
    if proposal["discovery_direction"] != "forward":
        raise OfflineResearchError("Offline symbolic memory accepts only forward hypotheses.")
    spec = engine.validate_execution_spec(candidate["execution_spec"])
    if not execution_spec_matches_proposal(proposal, spec):
        raise OfflineResearchError("Stored proposal and execution specification do not match.")
    if candidate["specification_sha256"] != _sha(_canonical(spec)):
        raise OfflineResearchError("Stored specification hash is invalid.")
    synthetic = candidate["synthetic_tests"]
    if (
        not isinstance(synthetic, dict)
        or synthetic.get("status") != "PASS"
        or synthetic.get("target_values_used") is not False
        or synthetic.get("real_archive_used") is not False
    ):
        raise OfflineResearchError("Stored synthetic-test metadata is invalid.")
    for field in ("forward_walk_forward", "time_ordered_cross_validation"):
        report = candidate[field]
        if not isinstance(report, dict) or report.get("predictive_claim") is not False:
            raise OfflineResearchError("Stored development metrics are not marked exploratory.")
        for key in ("mean_hits_out_of_6", "uniform_random_expected_hits_out_of_6"):
            if type(report.get(key)) not in {int, float} or not math.isfinite(float(report[key])):
                raise OfflineResearchError("Stored aggregate metric is invalid.")
        if type(report.get("validation_draw_count")) is not int or report["validation_draw_count"] < 1:
            raise OfflineResearchError("Stored validation count is invalid.")
    reverse = candidate["reverse_retrospective_only"]
    if (
        not isinstance(reverse, dict)
        or reverse.get("status") != "EVALUATED_RETROSPECTIVE_ONLY"
        or reverse.get("predictive_evidence") is not False
    ):
        raise OfflineResearchError("Stored reverse metrics are not retrospective-only.")
    return candidate


def _entry_hash(entry: dict[str, Any]) -> str:
    payload = {key: value for key, value in entry.items() if key != "content_sha256"}
    return _sha(_canonical(payload))


def _validate_entry(entry: Any, expected_id: int) -> dict[str, Any]:
    keys = {
        "entry_id", "cycle_id", "source", "direction", "created_at_utc",
        "target_date", "archive_sha256", "runner_version", "status",
        "discovery_window", "development_window", "candidates",
        "candidate_selection", "automatic_candidate_selection",
        "method_registry_modified", "target_numbers_stored",
        "archive_rows_stored", "per_draw_scores_stored", "sealed_holdout_used",
        "content_sha256",
    }
    if not isinstance(entry, dict) or set(entry) != keys:
        raise OfflineResearchError("Offline memory entry has an invalid schema.")
    if type(entry["entry_id"]) is not int or entry["entry_id"] != expected_id:
        raise OfflineResearchError("Offline memory entry IDs must be continuous.")
    if not isinstance(entry["cycle_id"], str) or not SHA_RE.fullmatch(entry["cycle_id"]):
        raise OfflineResearchError("Offline memory cycle identifier is invalid.")
    if entry["source"] != SOURCE or entry["direction"] != "forward":
        raise OfflineResearchError("Offline memory source or direction is invalid.")
    if not isinstance(entry["created_at_utc"], str):
        raise OfflineResearchError("Offline memory timestamp is missing.")
    try:
        datetime.fromisoformat(entry["created_at_utc"].replace("Z", "+00:00"))
    except ValueError:
        raise OfflineResearchError("Offline memory timestamp is invalid.") from None
    target = _canonical_day(entry["target_date"], "memory target date")
    if not isinstance(entry["archive_sha256"], str) or not SHA_RE.fullmatch(entry["archive_sha256"]):
        raise OfflineResearchError("Offline memory archive provenance is invalid.")
    if entry["runner_version"] != VERSION or entry["status"] != "EXPLORATORY_ONLY":
        raise OfflineResearchError("Offline memory runner version/status is invalid.")
    discovery = entry["discovery_window"]
    if not isinstance(discovery, dict) or set(discovery) != {"start_date", "end_date", "draw_count"}:
        raise OfflineResearchError("Offline discovery-window metadata is invalid.")
    start = _canonical_day(discovery["start_date"], "discovery start date")
    end = _canonical_day(discovery["end_date"], "discovery end date")
    if start > end or end >= target or type(discovery["draw_count"]) is not int or discovery["draw_count"] < MIN_TRAIN_DRAWS:
        raise OfflineResearchError("Offline discovery-window chronology/count is invalid.")
    development = entry["development_window"]
    if not isinstance(development, dict) or set(development) != {"draw_count", "window_sha256", "dates_stored"}:
        raise OfflineResearchError("Offline development-window metadata is invalid.")
    if (
        type(development["draw_count"]) is not int
        or development["draw_count"] < CV_FOLDS
        or not isinstance(development["window_sha256"], str)
        or not SHA_RE.fullmatch(development["window_sha256"])
        or development["dates_stored"] is not False
    ):
        raise OfflineResearchError("Offline development-window metadata is invalid.")
    if not isinstance(entry["candidates"], list) or not 1 <= len(entry["candidates"]) <= MAX_CANDIDATES:
        raise OfflineResearchError("Offline candidate list is invalid.")
    seen = set()
    for candidate in entry["candidates"]:
        _validate_candidate(candidate)
        if candidate["hypothesis_id"] in seen:
            raise OfflineResearchError("Offline memory contains duplicate hypothesis identifiers.")
        seen.add(candidate["hypothesis_id"])
    if (
        entry["candidate_selection"] != "NONE"
        or entry["automatic_candidate_selection"] is not False
        or entry["method_registry_modified"] is not False
        or entry["target_numbers_stored"] is not False
        or entry["archive_rows_stored"] is not False
        or entry["per_draw_scores_stored"] is not False
        or entry["sealed_holdout_used"] is not False
    ):
        raise OfflineResearchError("Offline memory contains a forbidden state or safety flag.")
    if entry["content_sha256"] != _entry_hash(entry):
        raise OfflineResearchError("Offline memory entry hash does not match its content.")
    return entry


def load_memory(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return empty_memory()
    payload = _read_json(target, "Offline research memory")
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "purpose", "entries", "consumed_development_windows"}
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != MEMORY_SCHEMA_VERSION
        or payload.get("purpose") != PURPOSE
        or not isinstance(payload.get("entries"), list)
        or len(payload["entries"]) > MAX_MEMORY_ENTRIES
        or not isinstance(payload.get("consumed_development_windows"), list)
        or len(payload["consumed_development_windows"]) > MAX_MEMORY_ENTRIES
    ):
        raise OfflineResearchError("Offline memory root schema is invalid; failing closed.")
    if len(payload["entries"]) != len(payload["consumed_development_windows"]):
        raise OfflineResearchError("Offline memory and development-consumption ledger are out of sync.")
    previous_target: str | None = None
    previous_dates: set[str] = set()
    previous_end: str | None = None
    cycles: set[str] = set()
    windows_by_cycle: dict[str, dict[str, Any]] = {}
    for window in payload["consumed_development_windows"]:
        if not isinstance(window, dict) or set(window) != {"cycle_id", "dates", "window_sha256"}:
            raise OfflineResearchError("Consumed development-window entry is malformed.")
        cycle_id = window["cycle_id"]
        dates = window["dates"]
        if not isinstance(cycle_id, str) or not SHA_RE.fullmatch(cycle_id) or cycle_id in cycles:
            raise OfflineResearchError("Consumed development-window cycle ID is invalid or duplicated.")
        if not isinstance(dates, list) or len(dates) < CV_FOLDS:
            raise OfflineResearchError("Consumed development-window dates are invalid.")
        checked_dates = [_canonical_day(item, "consumed development date") for item in dates]
        if checked_dates != sorted(set(checked_dates)):
            raise OfflineResearchError("Consumed development dates must be chronological and unique.")
        if previous_end is not None and checked_dates[0] <= previous_end:
            raise OfflineResearchError("Development windows must advance chronologically without overlap.")
        window_hash = _sha(_canonical(checked_dates))
        if window["window_sha256"] != window_hash:
            raise OfflineResearchError("Consumed development-window hash is invalid.")
        if previous_dates.intersection(checked_dates):
            raise OfflineResearchError("A development draw was consumed more than once.")
        previous_dates.update(checked_dates)
        previous_end = checked_dates[-1]
        cycles.add(cycle_id)
        windows_by_cycle[cycle_id] = window
    for index, entry in enumerate(payload["entries"], start=1):
        _validate_entry(entry, index)
        if previous_target is not None and entry["target_date"] < previous_target:
            raise OfflineResearchError("Offline memory target chronology moved backwards.")
        previous_target = entry["target_date"]
        cycle = windows_by_cycle.get(entry["cycle_id"])
        if (
            cycle is None
            or cycle["window_sha256"] != entry["development_window"]["window_sha256"]
            or len(cycle["dates"]) != entry["development_window"]["draw_count"]
        ):
            raise OfflineResearchError("Offline memory entry does not match its consumed development window.")
    return payload


def memory_context(
    path: str | Path,
    *,
    direction: str,
    target_date: str,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Return only prior same-direction offline GENERAL learning, never date-ledger rows."""
    if direction != "forward":
        return []
    target = _canonical_day(target_date, "context target date")
    if type(limit) is not int or not 1 <= limit <= 6:
        raise OfflineResearchError("Offline memory context limit is invalid.")
    memory = load_memory(path)
    eligible = [
        entry for entry in memory["entries"]
        if entry["direction"] == direction and entry["target_date"] <= target
    ]
    return [
        {
            "target_date": entry["target_date"],
            "candidate_count": len(entry["candidates"]),
            "candidate_selection": "NONE",
            "candidates": [
                {
                    "hypothesis_id": item["hypothesis_id"],
                    "family": item["family"],
                    "proposal": item["proposal"],
                    "execution_spec": item["execution_spec"],
                    "forward_walk_forward": item["forward_walk_forward"],
                    "time_ordered_cross_validation": item["time_ordered_cross_validation"],
                    "reverse_retrospective_only": item["reverse_retrospective_only"],
                }
                for item in entry["candidates"]
            ],
            "interpretation": "Prior exploratory development feedback only; not confirmatory or canonical.",
        }
        for entry in eligible[-limit:]
    ]


def _build_memory_entry(
    *,
    entry_id: int,
    cycle_id: str,
    target_date: str,
    archive_sha256: str,
    training_history: list[dict[str, Any]],
    evaluation: dict[str, Any],
) -> dict[str, Any]:
    candidates = evaluation["candidates"]
    entry: dict[str, Any] = {
        "entry_id": entry_id,
        "cycle_id": cycle_id,
        "source": SOURCE,
        "direction": "forward",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "target_date": target_date,
        "archive_sha256": archive_sha256,
        "runner_version": VERSION,
        "status": "EXPLORATORY_ONLY",
        "discovery_window": {
            "start_date": training_history[0]["date"],
            "end_date": training_history[-1]["date"],
            "draw_count": len(training_history),
        },
        "development_window": {
            "draw_count": evaluation["development_draw_count"],
            "window_sha256": evaluation["development_window_sha256"],
            "dates_stored": False,
        },
        "candidates": candidates,
        "candidate_selection": "NONE",
        "automatic_candidate_selection": False,
        "method_registry_modified": False,
        "target_numbers_stored": False,
        "archive_rows_stored": False,
        "per_draw_scores_stored": False,
        "sealed_holdout_used": False,
    }
    entry["content_sha256"] = _entry_hash(entry)
    return _validate_entry(entry, entry_id)


def run_cycle(
    *,
    archive_path: str | Path,
    sealed_ledger_path: str | Path,
    memory_path: str | Path,
    report_path: str | Path,
    folds: int = CV_FOLDS,
) -> dict[str, Any]:
    """Run one no-provider cycle against a FIFO-materialized archive snapshot."""
    archive = sealed.load_archive(archive_path)
    sealed_ledger = sealed.load_ledger(sealed_ledger_path)
    memory = load_memory(memory_path)
    consumed_dates = sealed.get_used_validation_dates(sealed_ledger)
    offline_dates = {
        day
        for window in memory["consumed_development_windows"]
        for day in window["dates"]
    }
    all_used_dates = consumed_dates | offline_dates
    partition = sealed.split_archive(
        archive["rows"],
        archive_sha256=archive["archive_sha256"],
        used_validation_dates=all_used_dates,
    )
    if (
        partition.get("target_numbers_exposed") is not False
        or partition.get("target_in_discovery") is not False
        or partition.get("target_in_validation") is not False
        or partition.get("target_in_reverse_validation") is not False
    ):
        raise OfflineResearchError("Sealed archive partition failed target isolation.")
    regime = partition.get("regimes", {}).get("R60", {})
    if regime.get("status") != "READY":
        result = {
            "status": "WAITING_FOR_FRESH_SEALED_DISCOVERY_HISTORY",
            "target_date": partition["target_date"],
            "candidate_count": 0,
            "target_numbers_exposed": False,
        }
        _write_json_atomic(report_path, result)
        return result

    sequence_by_date = {
        row["date"]: index for index, row in enumerate(archive["rows"])
    }
    discovery_rows = [
        {
            "date": row["date"],
            "numbers": list(row["numbers"]),
            "sequence_index": sequence_by_date[row["date"]],
        }
        for row in regime["discovery_rows"]
    ]
    previous_consumed_end = None
    if memory["consumed_development_windows"]:
        previous_consumed_end = memory["consumed_development_windows"][-1]["dates"][-1]
    fresh_rows = [
        row for row in discovery_rows
        if previous_consumed_end is None or row["date"] > previous_consumed_end
    ]
    if len(fresh_rows) < DEVELOPMENT_WINDOW_DRAWS:
        result = {
            "status": "WAITING_FOR_NEW_DEVELOPMENT_DRAWS",
            "target_date": partition["target_date"],
            "candidate_count": 0,
            "fresh_development_draws": len(fresh_rows),
            "required_development_draws": DEVELOPMENT_WINDOW_DRAWS,
            "target_numbers_exposed": False,
        }
        _write_json_atomic(report_path, result)
        return result

    development = fresh_rows[-DEVELOPMENT_WINDOW_DRAWS:]
    training = [
        row for row in discovery_rows
        if row["date"] < development[0]["date"]
    ]
    if len(training) < MIN_TRAIN_DRAWS:
        result = {
            "status": "WAITING_FOR_MINIMUM_TRAINING_HISTORY",
            "target_date": partition["target_date"],
            "candidate_count": 0,
            "training_draws": len(training),
            "required_training_draws": MIN_TRAIN_DRAWS,
            "target_numbers_exposed": False,
        }
        _write_json_atomic(report_path, result)
        return result

    evaluation = evaluate_hypotheses(
        training,
        development,
        pre_target_discovery_history=discovery_rows,
        target_date=partition["target_date"],
        folds=folds,
    )
    if evaluation["development_window_sha256"] != _sha(
        _canonical([row["date"] for row in development])
    ):
        raise OfflineResearchError("Development window changed during evaluation.")
    cycle_id = _sha(_canonical({
        "archive_sha256": archive["archive_sha256"],
        "target_date": partition["target_date"],
        "development_window_sha256": evaluation["development_window_sha256"],
    }))
    if any(entry["cycle_id"] == cycle_id for entry in memory["entries"]):
        raise OfflineResearchError("This archive/target/development window was already recorded.")
    window_dates = [row["date"] for row in development]
    if memory["consumed_development_windows"]:
        latest_used = memory["consumed_development_windows"][-1]["dates"][-1]
        if window_dates[0] <= latest_used:
            raise OfflineResearchError("New offline development interval does not advance beyond consumed data.")
    consumption = {
        "cycle_id": cycle_id,
        "dates": window_dates,
        "window_sha256": evaluation["development_window_sha256"],
    }
    entry = _build_memory_entry(
        entry_id=len(memory["entries"]) + 1,
        cycle_id=cycle_id,
        target_date=partition["target_date"],
        archive_sha256=archive["archive_sha256"],
        training_history=training,
        evaluation=evaluation,
    )
    updated = {
        "schema_version": memory["schema_version"],
        "purpose": memory["purpose"],
        "entries": memory["entries"] + [entry],
        "consumed_development_windows": memory["consumed_development_windows"] + [consumption],
    }
    # Validate the complete proposed append before the single atomic replacement.
    parent = Path(memory_path).parent
    parent.mkdir(parents=True, exist_ok=True)
    temp = Path(memory_path).with_name(Path(memory_path).name + ".tmp")
    temp.write_text(
        json.dumps(updated, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    checked = load_memory(temp)
    if checked != updated:
        raise OfflineResearchError("Offline memory append failed its read-back validation.")
    temp.replace(memory_path)

    report = {
        "schema_version": 1,
        "runner_version": VERSION,
        "status": evaluation["status"],
        "target_date": evaluation["target_date"],
        "archive_sha256": archive["archive_sha256"],
        "training_draw_count": evaluation["training_draw_count"],
        "development_draw_count": evaluation["development_draw_count"],
        "development_window_sha256": evaluation["development_window_sha256"],
        "candidate_count": len(evaluation["candidates"]),
        "candidate_selection": "NONE",
        "method_registry_modified": False,
        "target_numbers_exposed": False,
        "target_or_future_rows_used": False,
        "sealed_holdout_used": False,
        "memory_entries_added": 1,
        "candidates": evaluation["candidates"],
        "interpretation": evaluation["interpretation"],
    }
    _write_json_atomic(report_path, report)
    return report


def _write_github_output(path: str | None, values: dict[str, Any]) -> None:
    if not path:
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            if value is None:
                continue
            rendered = "true" if value is True else "false" if value is False else str(value)
            if "\n" in rendered or "\r" in rendered:
                raise OfflineResearchError("GitHub output values must be single-line.")
            stream.write(key + "=" + rendered + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--archive", type=Path, default=Path("super_loto_archive.json"))
    run.add_argument("--sealed-ledger", type=Path, default=Path("general_evaluation_ledger.json"))
    run.add_argument("--memory", type=Path, default=Path("general_offline_research_memory.json"))
    run.add_argument("--report", type=Path, default=Path("general_offline_research_report.json"))
    run.add_argument("--github-output")
    args = parser.parse_args(argv)
    try:
        result = run_cycle(
            archive_path=args.archive,
            sealed_ledger_path=args.sealed_ledger,
            memory_path=args.memory,
            report_path=args.report,
        )
        _write_github_output(args.github_output, {
            "offline_status": result.get("status"),
            "offline_candidate_count": result.get("candidate_count"),
            "offline_memory_entries_added": result.get("memory_entries_added", 0),
        })
        print(json.dumps({
            "status": result.get("status"),
            "candidate_count": result.get("candidate_count", 0),
            "candidate_selection": result.get("candidate_selection", "NONE"),
            "target_numbers_exposed": result.get("target_numbers_exposed", False),
            "memory_entries_added": result.get("memory_entries_added", 0),
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print("OFFLINE RESEARCH BLOCKED: " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
