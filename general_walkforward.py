"""Leakage-aware walk-forward and time-ordered baseline evaluation.

This module deliberately evaluates only draws strictly before the dynamically
selected latest archive draw. It does not implement A->B->C, A->T, or MOD5->T;
those methods require their established definitions and implementations.
No draw-number sets are printed or included in the report.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import general_data_guard as guard

VERSION = "0.2.0"
DEFAULT_MIN_TRAIN = 100
DEFAULT_CV_FOLDS = 5
REGIME_NUMBER_POOL_SIZES = {"R54": 54, "R60": 60}
METHOD_NAMES = (
    "frequency_top6",
    "frequency_bottom6",
    "adjacent_draw_overlap",
)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("Archive could not be read; evaluation stopped.") from None

    if isinstance(payload, dict):
        if "draws" in payload and "records" in payload:
            raise ValueError("Archive format is ambiguous; evaluation stopped.")
        rows = payload.get("draws", payload.get("records"))
    else:
        rows = payload

    if not isinstance(rows, list) or not rows:
        raise ValueError("Archive must contain a non-empty record list.")
    return rows


def _frequency_rank(rows: list[dict[str, Any]], hottest: bool) -> list[int]:
    counts = {number: 0 for number in range(1, 61)}
    for row in rows:
        for number in row["numbers"]:
            counts[number] += 1
    if hottest:
        return sorted(counts, key=lambda number: (-counts[number], number))[:6]
    return sorted(counts, key=lambda number: (counts[number], number))[:6]


def _hit_count(predicted: list[int], observed: list[int]) -> int:
    return len(set(predicted).intersection(observed))


def _score_summary(scores: list[int]) -> dict[str, Any]:
    if not scores:
        raise ValueError("A baseline produced no evaluation folds.")
    return {
        "evaluated_draws": len(scores),
        "mean_hits_out_of_6": round(sum(scores) / len(scores), 6),
        "hit_distribution": {
            "0_hits": sum(score == 0 for score in scores),
            "1_hit": sum(score == 1 for score in scores),
            "2_hits": sum(score == 2 for score in scores),
            "3_or_more_hits": sum(score >= 3 for score in scores),
        },
    }


def _summarize_score_sets(score_sets: dict[str, list[int]]) -> dict[str, Any]:
    return {name: _score_summary(scores) for name, scores in score_sets.items()}


def _random_uniform_expected_hits(number_pool_size: int) -> float:
    if type(number_pool_size) is not int or number_pool_size < 6:
        raise ValueError("Number-pool size must be an integer of at least 6.")
    # The expected overlap of two independent uniform 6-of-N sets is 36/N.
    return round(36 / number_pool_size, 6)


def _forward_walk_forward(
    history: list[dict[str, Any]], min_train: int, number_pool_size: int
) -> dict[str, Any]:
    scores = {name: [] for name in METHOD_NAMES}
    tested_dates: list[str] = []

    # At each historical test draw, every fitted frequency is based only
    # on rows with earlier dates. The current hidden target is not in history.
    for index in range(min_train, len(history)):
        training = history[:index]
        target = history[index]
        scores["frequency_top6"].append(
            _hit_count(_frequency_rank(training, hottest=True), target["numbers"])
        )
        scores["frequency_bottom6"].append(
            _hit_count(_frequency_rank(training, hottest=False), target["numbers"])
        )
        scores["adjacent_draw_overlap"].append(
            _hit_count(history[index - 1]["numbers"], target["numbers"])
        )
        tested_dates.append(target["date"])

    return {
        "protocol": "expanding-window; each test draw uses only earlier draws",
        "evaluated_draws": len(tested_dates),
        "first_test_date": tested_dates[0],
        "last_test_date": tested_dates[-1],
        "number_pool_size": number_pool_size,
        "random_uniform_expected_hits_out_of_6": _random_uniform_expected_hits(number_pool_size),
        "baselines": _summarize_score_sets(scores),
    }


def _reverse_retrospective_stress(
    history: list[dict[str, Any]], min_train: int, number_pool_size: int
) -> dict[str, Any]:
    scores = {name: [] for name in METHOD_NAMES}
    tested_dates: list[str] = []

    # Intentional reverse-time diagnostic. For each older historical draw,
    # the reference set is chronologically later. This is retrospective
    # stress testing only and must never be presented as predictive evidence.
    for index in range(len(history) - min_train - 1, -1, -1):
        training = history[index + 1:]
        target = history[index]
        scores["frequency_top6"].append(
            _hit_count(_frequency_rank(training, hottest=True), target["numbers"])
        )
        scores["frequency_bottom6"].append(
            _hit_count(_frequency_rank(training, hottest=False), target["numbers"])
        )
        scores["adjacent_draw_overlap"].append(
            _hit_count(history[index + 1]["numbers"], target["numbers"])
        )
        tested_dates.append(target["date"])

    return {
        "protocol": "reverse-time retrospective stress test; later draws are used",
        "predictive_evidence": False,
        "evaluated_draws": len(tested_dates),
        "first_test_date_in_reverse_order": tested_dates[0],
        "last_test_date_in_reverse_order": tested_dates[-1],
        "number_pool_size": number_pool_size,
        "random_uniform_expected_hits_out_of_6": _random_uniform_expected_hits(number_pool_size),
        "baselines": _summarize_score_sets(scores),
    }


def _blocked_time_cv(
    history: list[dict[str, Any]], min_train: int, folds: int, number_pool_size: int
) -> dict[str, Any]:
    evaluation_count = len(history) - min_train
    if folds < 2 or folds > evaluation_count:
        raise ValueError("CV fold count must be between 2 and available test draws.")

    base_size, extra = divmod(evaluation_count, folds)
    scores = {name: [] for name in METHOD_NAMES}
    fold_summaries = []
    start = min_train

    for fold_index in range(folds):
        block_size = base_size + (1 if fold_index < extra else 0)
        end = start + block_size
        training = history[:start]
        hot_prediction = _frequency_rank(training, hottest=True)
        cold_prediction = _frequency_rank(training, hottest=False)
        fold_scores = {name: [] for name in METHOD_NAMES}

        for index in range(start, end):
            target = history[index]
            # The frequency models remain fixed during this contiguous holdout
            # block. The adjacent-draw baseline uses only the immediately
            # preceding, already observed historical draw.
            values = {
                "frequency_top6": _hit_count(hot_prediction, target["numbers"]),
                "frequency_bottom6": _hit_count(cold_prediction, target["numbers"]),
                "adjacent_draw_overlap": _hit_count(
                    history[index - 1]["numbers"], target["numbers"]
                ),
            }
            for name, value in values.items():
                scores[name].append(value)
                fold_scores[name].append(value)

        fold_summaries.append({
            "fold": fold_index + 1,
            "training_draws": len(training),
            "validation_draws": block_size,
            "validation_start_date": history[start]["date"],
            "validation_end_date": history[end - 1]["date"],
            "baselines": _summarize_score_sets(fold_scores),
        })
        start = end

    return {
        "protocol": "blocked chronological cross-validation; no shuffling",
        "folds": folds,
        "evaluated_draws": evaluation_count,
        "number_pool_size": number_pool_size,
        "random_uniform_expected_hits_out_of_6": _random_uniform_expected_hits(number_pool_size),
        "baselines": _summarize_score_sets(scores),
        "fold_summaries": fold_summaries,
    }


def evaluate_rows(
    rows: list[dict[str, Any]],
    min_train: int = DEFAULT_MIN_TRAIN,
    cv_folds: int = DEFAULT_CV_FOLDS,
) -> dict[str, Any]:
    if type(min_train) is not int or min_train < 2:
        raise ValueError("Minimum training size must be an integer of at least 2.")

    checked = guard.validate_archive(rows)
    if checked.get("valid") is not True:
        raise ValueError("Data Guard rejected the archive.")

    # The latest valid date is the isolated target. It is not passed into any
    # scoring function below; only its date and the pre-target rows are used.
    target_date = checked["last_date"]
    split = guard.isolate_target(rows, target_date)
    if split.get("isolation_pass") is not True:
        raise ValueError("Data Guard target isolation failed.")
    if split.get("target_present_in_archive") is not True:
        raise ValueError("Latest target record is missing from archive.")
    if split.get("excluded_dates") != [target_date]:
        raise ValueError("Expected exactly the latest target to be excluded.")

    history = split.get("train")
    if not isinstance(history, list) or not history:
        raise ValueError("No pre-target history available for evaluation.")

    if any(row["date"] >= target_date for row in history):
        raise ValueError("Leakage detected: target/future date in evaluation history.")
    history_dates = [row["date"] for row in history]
    if history_dates != sorted(history_dates) or len(history_dates) != len(set(history_dates)):
        raise ValueError("Evaluation history is not strictly chronological and unique.")

    # Evaluate R54 and R60 independently. A regime without enough history is
    # reported as insufficient; its rows are never pooled into the other regime.
    regimes = {
        "R54": [row for row in history if row["date"] < "2020-08-02"],
        "R60": [row for row in history if row["date"] >= "2020-08-02"],
    }
    if sum(len(values) for values in regimes.values()) != len(history):
        raise ValueError("R54/R60 partition count mismatch; evaluation stopped.")

    regime_reports: dict[str, Any] = {}
    evaluable_regimes = 0
    for name, regime_history in regimes.items():
        number_pool_size = REGIME_NUMBER_POOL_SIZES[name]
        expected_random_hits = _random_uniform_expected_hits(number_pool_size)
        if len(regime_history) <= min_train:
            regime_reports[name] = {
                "number_pool_size": number_pool_size,
                "random_uniform_expected_hits_out_of_6": expected_random_hits,
                "status": "INSUFFICIENT_PRETARGET_HISTORY",
                "pre_target_history_records": len(regime_history),
                "minimum_training_draws": min_train,
                "evaluated": False,
                "reason": "This regime does not contain more pre-target draws than the required initial training window.",
            }
            continue

        regime_dates = [row["date"] for row in regime_history]
        if regime_dates != sorted(regime_dates) or len(regime_dates) != len(set(regime_dates)):
            raise ValueError(name + " history is not strictly chronological and unique.")
        if name == "R54" and any(day >= "2020-08-02" for day in regime_dates):
            raise ValueError("R54 contains a date at or after the regime boundary.")
        if name == "R60" and any(day < "2020-08-02" for day in regime_dates):
            raise ValueError("R60 contains a date before the regime boundary.")

        regime_reports[name] = {
            "number_pool_size": number_pool_size,
            "random_uniform_expected_hits_out_of_6": expected_random_hits,
            "status": "EVALUATED",
            "pre_target_history_records": len(regime_history),
            "minimum_training_draws": min_train,
            "evaluated": True,
            "forward_walk_forward": _forward_walk_forward(regime_history, min_train, number_pool_size),
            "reverse_retrospective_stress_test": _reverse_retrospective_stress(regime_history, min_train, number_pool_size),
            "time_ordered_cross_validation": _blocked_time_cv(regime_history, min_train, cv_folds, number_pool_size),
        }
        evaluable_regimes += 1

    if evaluable_regimes == 0:
        raise ValueError("Neither R54 nor R60 has sufficient pre-target history for baseline evaluation.")

    return {
        "engine_version": VERSION,
        "target_date": target_date,
        "archive_records": checked["record_count"],
        "pre_target_history_records": len(history),
        "minimum_training_draws": min_train,
        "regime_boundary": "2020-08-02",
        "regime_counts": {name: len(values) for name, values in regimes.items()},
        "regimes_evaluated_separately": True,
        "current_target_excluded_from_scoring": True,
        "current_target_numbers_used_for_scoring": False,
        "current_target_numbers_exposed": False,
        "regimes": regime_reports,
        "implemented_baselines": list(METHOD_NAMES),
        "required_methods_not_implemented": ["A->B->C", "A->T", "MOD5->T"],
        "interpretation_limits": [
            "The regime-specific random benchmark is the theoretical expected overlap 36/N for two uniform 6-of-N sets: R54 uses N=54 (0.666667), and R60 uses N=60 (0.600000); it is not an observed experiment.",
            "Baseline results are descriptive historical results, not proof of a predictive mechanism.",
            "R54 and R60 scores are computed independently; no draw rows are pooled across the 2020-08-02 boundary.",
            "Reverse-time results use later historical draws within the same regime and are retrospective stress tests, not predictive evidence.",
            "A->B->C, A->T, and MOD5->T remain OPEN_DEFINITION and were not executed by this baseline engine.",
            "A regime with insufficient pre-target history is reported as such; no synthetic or cross-regime substitute is used.",
            "The latest archive target and its numbers are excluded from all scoring."
        ],
    }


def evaluate_archive(
    path: Path,
    min_train: int = DEFAULT_MIN_TRAIN,
    cv_folds: int = DEFAULT_CV_FOLDS,
) -> dict[str, Any]:
    rows = _read_rows(path)
    return evaluate_rows(rows, min_train=min_train, cv_folds=cv_folds)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=Path("super_loto_archive.json"))
    parser.add_argument("--min-train", type=int, default=DEFAULT_MIN_TRAIN)
    parser.add_argument("--folds", type=int, default=DEFAULT_CV_FOLDS)
    args = parser.parse_args()

    try:
        report = evaluate_archive(args.archive, args.min_train, args.folds)
    except Exception as exc:
        # Exceptions from validation contain no draw-number sets. Do not dump
        # archive rows or model payloads to logs when an evaluation fails.
        message = str(exc).replace("\n", " ")[:240]
        raise SystemExit("WALK-FORWARD FAILED: " + message) from None

    print("GENERAL WALK-FORWARD BASELINE REPORT")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
