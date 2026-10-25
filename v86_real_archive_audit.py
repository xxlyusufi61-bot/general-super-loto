"""Leakage-safe real-archive audit for the preserved V8.6 feature vocabulary.

This script accepts ONLY a pre-target history file. The current target draw's
numbers must not be included in that file or in this process.
All output is aggregate metadata; draw-number sets and per-draw predictions
are never printed.
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import date
from itertools import product
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

import general_data_guard as guard
from v86_leakage_safe_evaluator import (
    FEATURES,
    TRANSFORMS,
    Draw,
    evaluate_frozen_candidate,
    fold_summary,
    reverse_stress_test,
    select_on_validation,
    validate_history,
)

VERSION = "0.1.0"
R60_START_DATE = date(2020, 8, 2)
DEFAULT_LOCKED_DATES = ("2021-01-26", "2021-01-28")
DEFAULT_K_VALUES = (1, 3, 5)
RANDOM_EXPECTED_HITS = 0.6


def _parse_iso_date(value: str, label: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD format.") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must use YYYY-MM-DD format.")
    return parsed


def prepare_r60_history(
    history_rows: Iterable[dict[str, Any]],
    target_date: str,
    locked_dates: Iterable[str] = DEFAULT_LOCKED_DATES,
) -> tuple[tuple[Draw, ...], dict[str, Any]]:
    """Validate pre-target rows, remove locked dates, and keep R60 only."""
    target = _parse_iso_date(target_date, "target_date")
    locked = {_parse_iso_date(value, "locked date") for value in locked_dates}
    rows = list(history_rows)
    if not rows:
        raise ValueError("Pre-target history is empty.")

    draws = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or "date" not in row or "numbers" not in row:
            raise ValueError(f"Invalid pre-target history row {index}.")
        day = _parse_iso_date(row["date"], f"row {index} date")
        # Fail closed: this process must never silently filter a target/future row.
        if day >= target:
            raise ValueError("Pre-target history contains target-date or future data.")
        numbers = guard.validate_numbers(row["numbers"])
        if tuple(numbers) != tuple(sorted(numbers)):
            raise ValueError("Draw numbers must already be sorted; no silent normalization.")
        draws.append(Draw(day=day, numbers=tuple(numbers)))

    # validate_history checks uniqueness, ordering and values and removes locked dates.
    clean = validate_history(draws, target, locked_dates=locked)
    r54_count = sum(row.day < R60_START_DATE for row in clean)
    r60_before_filter = sum(row.day >= R60_START_DATE for row in clean)
    r60 = tuple(row for row in clean if row.day >= R60_START_DATE)
    locked_present = sorted(
        day.isoformat() for day in locked
        if any(row.day == day for row in draws)
    )
    if any(row.day in locked for row in r60):
        raise ValueError("A locked historical target leaked into the audit history.")
    if any(row.day >= target for row in r60):
        raise ValueError("Target or future record leaked into the audit history.")
    if any(a.day >= b.day for a, b in zip(r60, r60[1:])):
        raise ValueError("R60 history is not strictly chronological.")
    summary = {
        "pre_target_rows_received": len(rows),
        "pre_target_rows_after_locked_date_exclusion": len(clean),
        "r54_rows_excluded_from_this_audit": r54_count,
        "r60_rows_before_locked_date_exclusion": r60_before_filter,
        "locked_dates_excluded": locked_present,
        "r60_history_rows_used": len(r60),
        "r60_first_date": r60[0].day.isoformat() if r60 else None,
        "r60_last_date": r60[-1].day.isoformat() if r60 else None,
    }
    return r60, summary


def _quantile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("Cannot compute a quantile from an empty collection.")
    index = round((len(sorted_values) - 1) * probability)
    return sorted_values[index]


def _random_reference(
    final_rows: tuple[Draw, ...],
    replications: int,
    seed: int,
) -> dict[str, Any]:
    if replications < 100:
        raise ValueError("Use at least 100 random-reference replications.")
    rng = random.Random(seed)
    all_numbers = tuple(range(1, 61))
    means = []
    for _ in range(replications):
        total_hits = 0
        for actual in final_rows:
            pick = set(rng.sample(all_numbers, 6))
            total_hits += len(pick.intersection(actual.numbers))
        means.append(total_hits / len(final_rows))
    means.sort()
    return {
        "replications": replications,
        "seed": seed,
        "mean_hits_out_of_6": round(mean(means), 6),
        "mean_hits_95pct_interval": [
            round(_quantile(means, 0.025), 6),
            round(_quantile(means, 0.975), 6),
        ],
        "theoretical_expected_hits_out_of_6": RANDOM_EXPECTED_HITS,
        "interpretation": (
            "Descriptive Monte Carlo reference only; not a formal significance test "
            "and not evidence of a predictive mechanism."
        ),
    }


def run_audit(
    history_rows: Iterable[dict[str, Any]],
    target_date: str,
    locked_dates: Iterable[str] = DEFAULT_LOCKED_DATES,
    candidates: Iterable[tuple[str, str, int]] | None = None,
    min_train: int = 100,
    validation_fraction: float = 0.75,
    folds: int = 5,
    random_replications: int = 1000,
    seed: int = 20261025,
) -> dict[str, Any]:
    """Select on an earlier validation interval and assess one frozen candidate later."""
    r60, history_summary = prepare_r60_history(history_rows, target_date, locked_dates)
    if min_train < 5:
        raise ValueError("min_train must be at least 5.")
    if not 0.55 <= validation_fraction <= 0.9:
        raise ValueError("validation_fraction must be between 0.55 and 0.9.")
    if folds < 2 or folds > 10:
        raise ValueError("folds must be between 2 and 10.")

    split = int(len(r60) * validation_fraction)
    if split <= min_train + 29 or len(r60) - split < max(30, folds * 5):
        raise ValueError(
            "Not enough R60 history for separate validation and frozen final intervals."
        )

    candidate_list = list(candidates) if candidates is not None else [
        (feature, transform, k)
        for feature, transform, k in product(FEATURES, TRANSFORMS, DEFAULT_K_VALUES)
    ]
    if not candidate_list:
        raise ValueError("Candidate grid is empty.")
    for feature, transform, k in candidate_list:
        if feature not in FEATURES or transform not in TRANSFORMS or type(k) is not int or k < 1:
            raise ValueError("Unsupported candidate configuration.")

    # Candidate selection is restricted to the validation interval.
    selection = select_on_validation(
        r60, candidate_list, validation_start=min_train, validation_end=split
    )
    if not selection:
        raise ValueError("No candidate could be evaluated on the validation interval.")
    validation_mean, feature, transform, k, validation_n = selection[0]

    # This chosen configuration is frozen before scoring the later final interval.
    final_result = evaluate_frozen_candidate(
        r60, feature, transform, k, final_start=split, final_end=len(r60)
    )
    final_values = tuple(final_result["per_target_hits"])
    if not final_values:
        raise ValueError("Frozen final evaluation contains no scored draws.")
    final_mean = float(final_result["final_mean_hits"])
    final_rows = r60[split:]
    random_reference = _random_reference(final_rows, random_replications, seed)

    chronological_folds = fold_summary(
        r60, feature, transform, k,
        folds=folds, start_index=split, end_index=len(r60)
    )
    fold_report = []
    fold_edges = [
        split + (len(r60) - split) * index // folds
        for index in range(folds + 1)
    ]
    for index, item in enumerate(chronological_folds):
        start_index, end_index = fold_edges[index], fold_edges[index + 1]
        fold_report.append({
            "fold": item["fold"],
            "start_date": r60[start_index].day.isoformat(),
            "end_date": r60[end_index - 1].day.isoformat(),
            "evaluated_draws": item["n_targets"],
            "mean_hits_out_of_6": round(item["mean_hits"], 6),
        })

    # Retrospective only: by design this reverse-order result is not forward evidence.
    reverse = reverse_stress_test(
        r60, feature, transform, k, start_index=min_train, end_index=len(r60)
    )
    locked_date_set = set(locked_dates)
    return {
        "audit_version": VERSION,
        "status": "COMPLETED_EXPERIMENTAL_AUDIT",
        "canonical_status": "NOT_CANONICAL_NOT_APPROVED",
        "current_target": {
            "date": target_date,
            "numbers_passed_to_audit_process": False,
            "numbers_used_for_scoring": False,
            "excluded_from_scoring": True,
        },
        "archive_scope": {
            "regime": "R60_ONLY",
            "r60_start_date": R60_START_DATE.isoformat(),
            "locked_dates_policy": "Exclude locked historical target dates from all audit intervals.",
            "locked_dates_configured": sorted(value.isoformat() for value in locked_date_set),
            **history_summary,
        },
        "protocol": {
            "candidate_count": len(candidate_list),
            "candidate_grid": "V8.6 feature vocabulary × exact/sign/absolute transforms × k in {1,3,5}",
            "minimum_training_draws": min_train,
            "validation_interval": {
                "start_date": r60[min_train].day.isoformat(),
                "end_date": r60[split - 1].day.isoformat(),
                "evaluated_draws": validation_n,
                "purpose": "Select one frozen candidate only; not the final score.",
            },
            "frozen_final_interval": {
                "start_date": r60[split].day.isoformat(),
                "end_date": r60[-1].day.isoformat(),
                "evaluated_draws": len(final_values),
                "selected_candidate_not_reselected": True,
            },
            "fold_summaries": "Contiguous chronological summaries of the same frozen walk-forward final predictions; not nested cross-validation.",
            "reverse_protocol": reverse["protocol"],
        },
        "selected_candidate": {
            "feature": feature,
            "transform": transform,
            "k": k,
            "validation_mean_hits_out_of_6": round(float(validation_mean), 6),
        },
        "frozen_final_result": {
            "mean_hits_out_of_6": round(final_mean, 6),
            "evaluated_draws": len(final_values),
            "delta_vs_simulated_random_mean": round(
                final_mean - random_reference["mean_hits_out_of_6"], 6
            ),
            "theoretical_random_expected_hits_out_of_6": RANDOM_EXPECTED_HITS,
            "candidate_was_selected_without_final_interval": True,
        },
        "random_prediction_reference": random_reference,
        "chronological_final_folds": fold_report,
        "reverse_retrospective_stress_test": {
            "mean_hits_out_of_6": round(float(reverse["mean_hits"]), 6),
            "evaluated_draws": reverse["n_targets"],
            "predictive_evidence": False,
        },
        "limitations": [
            "The evaluator is a leakage-safe audit candidate based on legacy V8.6 feature vocabulary, not a verified exact reconstruction of the legacy formula.",
            "Only R60 data is analyzed; R54 records are kept outside this experiment.",
            "The current isolated target draw is not passed to this process or scored.",
            "Locked historical target dates are excluded from validation, final evaluation and reverse stress testing.",
            "Candidate selection uses the validation interval; final parameters are frozen before the later final interval.",
            "Reverse-time results are retrospective stress tests, not predictive evidence.",
            "Monte Carlo results are descriptive, not formal significance testing; no predictive mechanism is proven by average hits.",
            "No per-draw predictions or draw-number sets are included in the result.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", required=True, help="JSON file containing strictly pre-target history only.")
    parser.add_argument("--target-date", required=True, help="Isolated target date; its numbers must not be in --history.")
    parser.add_argument("--locked-dates", nargs="*", default=list(DEFAULT_LOCKED_DATES))
    parser.add_argument("--min-train", type=int, default=100)
    parser.add_argument("--validation-fraction", type=float, default=0.75)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--random-replications", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=20261025)
    args = parser.parse_args()

    try:
        payload = json.loads(Path(args.history).read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            rows = payload.get("draws")
        else:
            rows = payload
        if not isinstance(rows, list):
            raise ValueError("History JSON must be a list or an object containing a draws list.")
        result = run_audit(
            rows,
            target_date=args.target_date,
            locked_dates=args.locked_dates,
            min_train=args.min_train,
            validation_fraction=args.validation_fraction,
            folds=args.folds,
            random_replications=args.random_replications,
            seed=args.seed,
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        raise SystemExit(f"V8.6 real-archive audit stopped safely: {exc}") from None

    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
