"""Bidirectional full-history walk-forward diagnostics over a pre-registered safe DSL catalog.

This catalog is an exploratory control, not a definition or promotion of A->B->C,
A->T, MOD5->T, or any legacy method. For each scored date, candidate selection
uses only earlier rows in the forward direction. Reverse-time selection uses
only later rows and is retrospective-only, never predictive evidence.

Reports include target dates, hit counts, and matched 1-based rank positions
among the six selected columns. They never include target number sets or the
predicted number values themselves.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from datetime import date
from typing import Any

from general_hypothesis_execution import (
    DEFAULT_MIN_TRAIN_DRAWS,
    MAX_HISTORY_DRAWS,
    MAX_INPUT_HISTORY_DRAWS,
    _rank_checked_history,
    _validate_rows,
    validate_execution_spec,
)

VERSION = "0.1.0"
REGIME_BOUNDARY = date(2020, 8, 2)
MIN_SELECTION_DRAWS = 2
MAX_SELECTION_DRAWS = 5
CATALOG_ID = "GENERAL-SAFE-DSL-CATALOG-v1"


def _var(name: str) -> dict[str, Any]:
    return {"op": "var", "name": name}


def _const(value: int) -> dict[str, Any]:
    return {"op": "const", "value": value}


def _binary(op: str, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {"op": op, "left": left, "right": right}


def _count(expression: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "score_expression": {
            "op": "aggregate_history_numbers",
            "aggregate": "count",
            "expression": expression,
        },
    }


def _same_number() -> dict[str, Any]:
    return _binary("eq", _var("value"), _var("candidate"))


def _same_modulus(modulus: int) -> dict[str, Any]:
    return _binary(
        "eq",
        _binary("mod", _var("value"), _const(modulus)),
        _binary("mod", _var("candidate"), _const(modulus)),
    )


def fixed_candidate_catalog() -> list[dict[str, Any]]:
    """Return immutable-in-protocol candidate definitions; no archive values are used here."""
    recent_gate = _binary("lt", _var("draw_age"), _const(5))
    recent_exact = _binary("and", _same_number(), recent_gate)
    recent_mod5 = _binary("and", _same_modulus(5), recent_gate)
    adjacent = _binary(
        "eq",
        {"op": "abs", "arg": _binary("sub", _var("value"), _var("candidate"))},
        _const(1),
    )
    raw = [
        ("EXACT_FREQUENCY_ALL", _count(_same_number())),
        ("EXACT_FREQUENCY_RECENT_5_DRAWS", _count(recent_exact)),
        ("MOD5_CLASS_FREQUENCY", _count(_same_modulus(5))),
        ("UNIT_DIGIT_FREQUENCY", _count(_same_modulus(10))),
        ("ADJACENT_NUMBER_FREQUENCY", _count(adjacent)),
        ("MOD5_CLASS_RECENT_5_DRAWS", _count(recent_mod5)),
    ]
    catalog = []
    for spec_id, raw_spec in raw:
        spec = validate_execution_spec(raw_spec)
        canonical = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        catalog.append({
            "spec_id": spec_id,
            "execution_spec": spec,
            "spec_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        })
    return catalog


def _rank_catalog_history(
    history: list[dict[str, Any]],
    *,
    number_pool_size: int,
    candidate: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fast deterministic implementation, contract-tested against the DSL interpreter."""
    spec_id = candidate["spec_id"]
    counts = [0] * (number_pool_size + 1)
    recent_counts = [0] * (number_pool_size + 1)
    mod5_counts = [0] * 5
    recent_mod5_counts = [0] * 5
    unit_counts = [0] * 10
    for age, row in enumerate(reversed(history)):
        is_recent = age < 5
        for value in row["numbers"]:
            counts[value] += 1
            mod5_counts[value % 5] += 1
            unit_counts[value % 10] += 1
            if is_recent:
                recent_counts[value] += 1
                recent_mod5_counts[value % 5] += 1
    ranking = []
    for number in range(1, number_pool_size + 1):
        if spec_id == "EXACT_FREQUENCY_ALL":
            score = counts[number]
        elif spec_id == "EXACT_FREQUENCY_RECENT_5_DRAWS":
            score = recent_counts[number]
        elif spec_id == "MOD5_CLASS_FREQUENCY":
            score = mod5_counts[number % 5]
        elif spec_id == "UNIT_DIGIT_FREQUENCY":
            score = unit_counts[number % 10]
        elif spec_id == "ADJACENT_NUMBER_FREQUENCY":
            score = counts[number - 1] + (counts[number + 1] if number < number_pool_size else 0)
        elif spec_id == "MOD5_CLASS_RECENT_5_DRAWS":
            score = recent_mod5_counts[number % 5]
        else:
            raise ValueError("Candidate is not in the pre-registered safe catalog.")
        ranking.append({"number": number, "score": score})
    ranking.sort(key=lambda item: (-item["score"], item["number"]))
    return ranking


def _matched_positions(ranking: list[dict[str, Any]], target_numbers: list[int]) -> list[int]:
    actual = set(target_numbers)
    return [
        position
        for position, item in enumerate(ranking[:6], start=1)
        if item["number"] in actual
    ]


def _checked_rank_from_later_history(
    later_history: list[dict[str, Any]],
    *,
    target_date: str,
    number_pool_size: int,
    execution_spec: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rank using dates strictly after target_date; only allowed for reverse stress tests."""
    if not later_history:
        raise ValueError("Reverse history must not be empty.")
    checked = _validate_rows(
        later_history,
        pool_size=number_pool_size,
        field="reverse_later_history",
        maximum=MAX_HISTORY_DRAWS,
    )
    target_day = date.fromisoformat(target_date)
    if any(date.fromisoformat(row["date"]) <= target_day for row in checked):
        raise ValueError("Reverse reference history contains the test target or an earlier draw.")
    return _rank_checked_history(
        checked,
        number_pool_size=number_pool_size,
        spec=validate_execution_spec(execution_spec),
    )


def _choose_forward_spec(
    history: list[dict[str, Any]],
    *,
    target_date: str,
    number_pool_size: int,
    catalog: list[dict[str, Any]],
    min_train_draws: int,
) -> tuple[dict[str, Any], float, int]:
    if len(history) < min_train_draws + MIN_SELECTION_DRAWS:
        raise ValueError("Forward candidate selection has insufficient prior history.")
    selection_count = min(MAX_SELECTION_DRAWS, len(history) - min_train_draws)
    training_base = history[:-selection_count]
    selection_rows = history[-selection_count:]
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for catalog_index, candidate in enumerate(catalog):
        hits: list[int] = []
        for index, validation_target in enumerate(selection_rows):
            training = (training_base + selection_rows[:index])[-MAX_HISTORY_DRAWS:]
            if len(training) < min_train_draws:
                raise ValueError("Forward inner selection fell below the minimum training history.")
            ranking = _rank_catalog_history(
                training,
                number_pool_size=number_pool_size,
                candidate=candidate,
            )
            hits.append(len(_matched_positions(ranking, validation_target["numbers"])))
        scored.append((statistics.fmean(hits), -catalog_index, candidate))
    best_score, _tie_break, selected = max(scored, key=lambda item: (item[0], item[1]))
    if date.fromisoformat(history[-1]["date"]) >= date.fromisoformat(target_date):
        raise ValueError("Forward candidate selection history contains target/future rows.")
    return selected, best_score, selection_count


def _choose_reverse_spec(
    later_history: list[dict[str, Any]],
    *,
    target_date: str,
    number_pool_size: int,
    catalog: list[dict[str, Any]],
    min_train_draws: int,
) -> tuple[dict[str, Any], float, int]:
    if len(later_history) < min_train_draws + MIN_SELECTION_DRAWS:
        raise ValueError("Reverse candidate selection has insufficient later history.")
    if any(date.fromisoformat(row["date"]) <= date.fromisoformat(target_date) for row in later_history):
        raise ValueError("Reverse candidate selection received the target or an earlier date.")
    selection_count = min(MAX_SELECTION_DRAWS, len(later_history) - min_train_draws)
    selection_rows = later_history[:selection_count]
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for catalog_index, candidate in enumerate(catalog):
        hits: list[int] = []
        for index, validation_target in enumerate(selection_rows):
            reference = later_history[index + 1:][-MAX_HISTORY_DRAWS:]
            if len(reference) < min_train_draws:
                raise ValueError("Reverse inner selection fell below the minimum later-history size.")
            _checked_rows = _validate_rows(
                reference,
                pool_size=number_pool_size,
                field="reverse_selection_reference",
                maximum=MAX_HISTORY_DRAWS,
            )
            if any(date.fromisoformat(row["date"]) <= date.fromisoformat(validation_target["date"]) for row in _checked_rows):
                raise ValueError("Reverse selection reference contains the target or an earlier draw.")
            ranking = _rank_catalog_history(
                _checked_rows,
                number_pool_size=number_pool_size,
                candidate=candidate,
            )
            hits.append(len(_matched_positions(ranking, validation_target["numbers"])))
        scored.append((statistics.fmean(hits), -catalog_index, candidate))
    best_score, _tie_break, selected = max(scored, key=lambda item: (item[0], item[1]))
    return selected, best_score, selection_count


def _direction_summary(
    direction: str,
    rows: list[dict[str, Any]],
    *,
    pool_size: int,
    predictive_evidence: bool,
) -> dict[str, Any]:
    draw_count = len(rows)
    total_hits = sum(row["hit_count"] for row in rows)
    hit_histogram = {str(hits): 0 for hits in range(7)}
    selected_spec_counts: dict[str, int] = {}
    for row in rows:
        hit_histogram[str(row["hit_count"])] += 1
        selected_spec_counts[row["selected_spec_id"]] = selected_spec_counts.get(row["selected_spec_id"], 0) + 1
    return {
        "status": "EVALUATED" if draw_count else "NOT_EVALUABLE_INSUFFICIENT_HISTORY",
        "direction": direction,
        "predictive_evidence": predictive_evidence,
        "evaluation_order": "oldest_to_newest" if direction == "forward" else "newest_to_oldest",
        "evaluated_draw_count": draw_count,
        "first_test_date": rows[0]["test_date"] if rows else None,
        "last_test_date": rows[-1]["test_date"] if rows else None,
        "mean_hits_out_of_6": round(total_hits / draw_count, 6) if draw_count else None,
        "uniform_random_expected_hits_out_of_6": round(36 / pool_size, 6),
        "hit_distribution": hit_histogram,
        "selected_spec_counts": selected_spec_counts,
        "minimum_training_or_later_history_draws": DEFAULT_MIN_TRAIN_DRAWS,
        "maximum_scoring_history_draws": MAX_HISTORY_DRAWS,
        "per_draw_detail_present": True,
        "target_number_sets_reported": False,
        "predicted_number_values_reported": False,
        "interpretation": (
            "Per-date selection uses a fixed, pre-registered safe DSL catalog and only data available "
            "in the stated direction before scoring that date. This is an exploratory adaptive baseline, "
            "not proof of a lottery mechanism; repeated overlapping selection windows and multiple testing "
            "must be considered."
            if direction == "forward"
            else
            "Later historical draws are used to select and score candidates. This is a retrospective "
            "stress test only, not predictive evidence."
        ),
    }


def evaluate_bidirectional_full_history(
    rows_by_regime: dict[str, list[dict[str, Any]]],
    *,
    isolated_target_date: str,
    min_train_draws: int = DEFAULT_MIN_TRAIN_DRAWS,
) -> dict[str, Any]:
    """Run a per-cutoff adaptive baseline forward and in reverse over eligible history."""
    try:
        target_day = date.fromisoformat(isolated_target_date)
    except (TypeError, ValueError):
        raise ValueError("Isolated target date must be a canonical ISO date.") from None
    if target_day.isoformat() != isolated_target_date:
        raise ValueError("Isolated target date must be canonical YYYY-MM-DD.")
    if type(min_train_draws) is not int or not 2 <= min_train_draws <= MAX_HISTORY_DRAWS:
        raise ValueError("Minimum training history is outside the supported range.")
    if not isinstance(rows_by_regime, dict) or set(rows_by_regime) != {"R54", "R60"}:
        raise ValueError("Both separate R54 and R60 histories are required.")
    catalog = fixed_candidate_catalog()
    results = {}
    all_forward_rows = []
    all_reverse_rows = []
    for regime, pool_size in (("R54", 54), ("R60", 60)):
        source_rows = rows_by_regime[regime]
        if not isinstance(source_rows, list):
            raise ValueError(regime + " history must be a list.")
        if source_rows:
            checked = _validate_rows(
                source_rows,
                pool_size=pool_size,
                field=regime + "_pre_target_history",
                maximum=MAX_INPUT_HISTORY_DRAWS,
            )
            if any(date.fromisoformat(row["date"]) >= target_day for row in checked):
                raise ValueError(regime + " history contains the isolated target or a later draw.")
            for original, validated in zip(source_rows, checked):
                if original.get("numbers") != sorted(original.get("numbers", [])):
                    raise ValueError(regime + " number sets must already be sorted.")
                if original != validated:
                    raise ValueError(regime + " history changed during validation.")
            rows = checked
        else:
            rows = []
        forward_details = []
        reverse_details = []

        for index, target in enumerate(rows):
            history = rows[:index]
            if len(history) < min_train_draws + MIN_SELECTION_DRAWS:
                continue
            chosen, selection_mean, selection_count = _choose_forward_spec(
                history,
                target_date=target["date"],
                number_pool_size=pool_size,
                catalog=catalog,
                min_train_draws=min_train_draws,
            )
            scoring_history = history[-MAX_HISTORY_DRAWS:]
            ranking = _rank_catalog_history(
                scoring_history,
                number_pool_size=pool_size,
                candidate=chosen,
            )
            matched = _matched_positions(ranking, target["numbers"])
            forward_details.append({
                "test_date": target["date"],
                "direction": "forward",
                "selected_spec_id": chosen["spec_id"],
                "selected_spec_sha256": chosen["spec_sha256"],
                "training_draw_count": len(history),
                "scoring_history_draw_count": len(scoring_history),
                "selection_window_draw_count": selection_count,
                "selection_mean_hits_out_of_6": round(selection_mean, 6),
                "hit_column_positions_1_to_6": matched,
                "hit_count": len(matched),
                "target_numbers_reported": False,
                "predicted_number_values_reported": False,
            })

        for index in range(len(rows) - 1, -1, -1):
            target = rows[index]
            later_history = rows[index + 1:]
            if len(later_history) < min_train_draws + MIN_SELECTION_DRAWS:
                continue
            chosen, selection_mean, selection_count = _choose_reverse_spec(
                later_history,
                target_date=target["date"],
                number_pool_size=pool_size,
                catalog=catalog,
                min_train_draws=min_train_draws,
            )
            scoring_history = later_history[-MAX_HISTORY_DRAWS:]
            scoring_history = _validate_rows(
                scoring_history,
                pool_size=pool_size,
                field="reverse_scoring_history",
                maximum=MAX_HISTORY_DRAWS,
            )
            if any(date.fromisoformat(row["date"]) <= date.fromisoformat(target["date"]) for row in scoring_history):
                raise ValueError("Reverse scoring history contains the target or an earlier draw.")
            ranking = _rank_catalog_history(
                scoring_history,
                number_pool_size=pool_size,
                candidate=chosen,
            )
            matched = _matched_positions(ranking, target["numbers"])
            reverse_details.append({
                "test_date": target["date"],
                "direction": "reverse_retrospective",
                "selected_spec_id": chosen["spec_id"],
                "selected_spec_sha256": chosen["spec_sha256"],
                "later_training_draw_count": len(later_history),
                "scoring_later_history_draw_count": len(scoring_history),
                "selection_window_draw_count": selection_count,
                "selection_mean_hits_out_of_6": round(selection_mean, 6),
                "hit_column_positions_1_to_6": matched,
                "hit_count": len(matched),
                "target_numbers_reported": False,
                "predicted_number_values_reported": False,
            })

        # Forward is oldest->newest; reverse is newest->oldest, matching the required protocol.
        forward_details.sort(key=lambda item: item["test_date"])
        reverse_details.sort(key=lambda item: item["test_date"], reverse=True)
        results[regime] = {
            "regime": regime,
            "number_pool_size": pool_size,
            "catalog_id": CATALOG_ID,
            "catalog_specifications": [
                {"spec_id": item["spec_id"], "spec_sha256": item["spec_sha256"]}
                for item in catalog
            ],
            "forward": _direction_summary("forward", forward_details, pool_size=pool_size, predictive_evidence=True),
            "reverse_retrospective": _direction_summary(
                "reverse_retrospective", reverse_details, pool_size=pool_size, predictive_evidence=False
            ),
            "forward_per_draw": forward_details,
            "reverse_per_draw": reverse_details,
            "forward_and_reverse_tested_date_intersection_count": len(
                {row["test_date"] for row in forward_details}.intersection(
                    {row["test_date"] for row in reverse_details}
                )
            ),
        }
        all_forward_rows.extend(forward_details)
        all_reverse_rows.extend(reverse_details)

    return {
        "schema_version": 1,
        "status": "EVALUATED_BIDIRECTIONAL_ADAPTIVE_BASELINE",
        "evaluator_version": VERSION,
        "catalog_id": CATALOG_ID,
        "protocol": (
            "For each eligible historical target, independently select among a frozen safe DSL catalog "
            "using only earlier draws in forward mode or only later draws in reverse mode. The isolated "
            "latest archive target is never a test row or reference-history row."
        ),
        "isolated_target_date": isolated_target_date,
        "isolated_target_numbers_exposed": False,
        "isolated_target_in_forward_or_reverse_test": False,
        "archive_validation_required_before_call": True,
        "min_train_draws": min_train_draws,
        "min_selection_draws": MIN_SELECTION_DRAWS,
        "max_selection_draws": MAX_SELECTION_DRAWS,
        "r54_r60_kept_separate": True,
        "regime_boundary": REGIME_BOUNDARY.isoformat(),
        "regimes": results,
        "overall_cross_regime_pooling": {
            "performed": False,
            "reason": "R54 and R60 use different number-pool sizes and must be reported separately.",
        },
        "binding_limitations": [
            "This pre-registered generic DSL catalog is a separate exploratory baseline, not an implementation or approval of A->B->C, A->T, or MOD5->T.",
            "The reverse direction uses later historical data and is strictly retrospective, not predictive evidence.",
            "Selection windows overlap over time; scores are correlated and multiple testing means a high score is not proof.",
            "Each detail reports hit-column positions 1 through 6 and hit count only; target and predicted number values are omitted.",
        ],
    }


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.archive.read_text(encoding="utf-8"))
    source_rows = payload.get("draws", payload.get("records")) if isinstance(payload, dict) else payload
    if not isinstance(source_rows, list) or not source_rows:
        raise SystemExit("Archive must have a non-empty draw list.")
    from general_data_guard import validate_archive, isolate_target
    guard_result = validate_archive(source_rows)
    if not isinstance(guard_result, dict) or guard_result.get("valid") is not True:
        raise SystemExit("Data Guard rejected archive.")
    target_date = guard_result["last_date"]
    isolated = isolate_target(source_rows, target_date)
    if isolated.get("isolation_pass") is not True or isolated.get("target_present_in_archive") is not True:
        raise SystemExit("Target isolation failed.")
    pretarget = isolated["train"]
    by_regime = {
        "R54": [row for row in pretarget if date.fromisoformat(row["date"]) < REGIME_BOUNDARY],
        "R60": [row for row in pretarget if date.fromisoformat(row["date"]) >= REGIME_BOUNDARY],
    }
    result = evaluate_bidirectional_full_history(by_regime, isolated_target_date=target_date)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": result["status"],
        "isolated_target_date": target_date,
        "regimes": {
            name: {
                "forward_draws": item["forward"]["evaluated_draw_count"],
                "reverse_draws": item["reverse_retrospective"]["evaluated_draw_count"],
            }
            for name, item in result["regimes"].items()
        },
        "target_numbers_reported": False,
    }, ensure_ascii=False, indent=2))
