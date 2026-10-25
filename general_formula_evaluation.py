"""Leakage-aware, experimental real-archive evaluation for formula proposals.

This module is not imported by general_core.py and never calls an AI API.
The current latest draw is isolated by Data Guard; only its pre-target training
records are passed into the evaluators. Results contain aggregate metrics only.
"""
import argparse
import json
import math
import random
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from statistics import mean

import general_data_guard as data_guard
from general_formula_proposals import (
    score_abc_transition,
    score_a_to_t,
    score_mod5_to_t,
)

METHODS = {
    "A->B->C": {"score": score_abc_transition, "minimum_history": 3},
    "A->T": {"score": score_a_to_t, "minimum_history": 20},
    "MOD5->T": {"score": score_mod5_to_t, "minimum_history": 6},
}
PROTOCOL_SALTS = {
    "forward_walk_forward": 1701,
    "reverse_retrospective_stress_test": 2903,
    "time_ordered_cross_validation": 4109,
}
DEFAULT_RANDOM_REPEATS = 100
DEFAULT_BOOTSTRAP_REPLICATES = 500
DEFAULT_BLOCK_LENGTH = 20
RANDOM_DRAW_UNIVERSE = tuple(range(1, 61))


def _validate_historical_pool(rows):
    if not isinstance(rows, list) or not rows:
        raise ValueError("historical training pool is empty or invalid")
    previous = None
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("historical row must be an object")
        raw_date = row.get("date")
        try:
            parsed = date.fromisoformat(raw_date)
        except (TypeError, ValueError):
            raise ValueError("historical row has invalid date") from None
        if parsed.isoformat() != raw_date:
            raise ValueError("historical date is not canonical ISO format")
        if previous is not None and parsed <= previous:
            raise ValueError("historical rows must be unique and chronological")
        previous = parsed
        numbers = row.get("numbers")
        if not isinstance(numbers, (list, tuple)) or len(numbers) != 6:
            raise ValueError("historical draw must contain six numbers")
        if any(isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 60 for n in numbers):
            raise ValueError("historical draw contains an invalid number")
        if len(set(numbers)) != 6:
            raise ValueError("historical draw contains duplicate numbers")


def _safe_prediction(result):
    if not isinstance(result, dict) or result.get("status") != "scored":
        return None
    ranked = result.get("ranked_numbers")
    if not isinstance(ranked, list) or len(ranked) < 6:
        # No arbitrary-number fallback; a method that cannot produce six
        # scored candidates has no prediction for this target.
        return None
    prediction = ranked[:6]
    if len(set(prediction)) != 6 or any(
        isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= 60
        for n in prediction
    ):
        raise ValueError("scorer returned invalid candidate numbers")
    return set(prediction)


def _random_prediction_mean(actual, repeats, seed):
    rng = random.Random(seed)
    hits = []
    for _ in range(repeats):
        guess = set(rng.sample(RANDOM_DRAW_UNIVERSE, 6))
        hits.append(len(guess.intersection(actual)))
    return mean(hits), hits


def _block_bootstrap_interval(values, replicates, block_length, seed):
    values = [float(value) for value in values]
    if not values:
        return None
    point = mean(values)
    if len(values) == 1 or replicates <= 0:
        return {"low": point, "high": point, "method": "degenerate_single_value"}

    n = len(values)
    block = max(1, min(int(block_length), n))
    blocks_needed = math.ceil(n / block)
    rng = random.Random(seed)
    estimates = []
    for _ in range(replicates):
        sample = []
        while len(sample) < n:
            start = rng.randrange(n)
            sample.extend(values[(start + offset) % n] for offset in range(block))
        estimates.append(mean(sample[:n]))
    estimates.sort()
    low_index = max(0, int(0.025 * (len(estimates) - 1)))
    high_index = min(len(estimates) - 1, int(0.975 * (len(estimates) - 1)))
    return {
        "low": estimates[low_index],
        "high": estimates[high_index],
        "method": "circular_moving_block_bootstrap_95_percentile",
        "block_length_targets": block,
        "replicates": replicates,
    }


def _summarize_samples(samples, random_repeats, bootstrap_replicates, seed):
    n = len(samples)
    hits = [sample["hits"] for sample in samples]
    predicted_hits = [
        sample["hits"] for sample in samples if sample["prediction_made"]
    ]
    baseline_means = [sample["random_baseline_mean_hits"] for sample in samples]
    differences = [
        sample["hits"] - sample["random_baseline_mean_hits"]
        for sample in samples
    ]
    hit_histogram = Counter(str(value) for value in hits)
    all_random_hits = [
        random_hit
        for sample in samples
        for random_hit in sample["random_all_hits"]
    ]

    return {
        "eligible_target_draws": n,
        "predictions_made": sum(1 for sample in samples if sample["prediction_made"]),
        "no_signal_or_insufficient_candidates": sum(
            1 for sample in samples if not sample["prediction_made"]
        ),
        "coverage_rate": (
            sum(1 for sample in samples if sample["prediction_made"]) / n
            if n else 0.0
        ),
        "mean_hits_when_prediction_available": (
            mean(predicted_hits) if predicted_hits else None
        ),
        "mean_hits_per_eligible_target_no_signal_as_zero": mean(hits) if hits else None,
        "candidate_hit_count_distribution": {
            str(h): int(hit_histogram.get(str(h), 0)) for h in range(7)
        },
        "random_baseline": {
            "draws_per_target": 6,
            "random_predictions_per_target": random_repeats,
            "mean_hits_per_random_prediction": (
                mean(all_random_hits) if all_random_hits else None
            ),
            "mean_random_hits_per_target": mean(baseline_means) if baseline_means else None,
            "mean_random_hits_per_target_95_ci": _block_bootstrap_interval(
                baseline_means,
                bootstrap_replicates,
                DEFAULT_BLOCK_LENGTH,
                seed + 29,
            ),
            "theoretical_expected_hits_per_random_six_number_draw": 0.6,
        },
        "paired_candidate_minus_random_mean_hits_per_target": {
            "mean": mean(differences) if differences else None,
            "block_bootstrap_95_ci": _block_bootstrap_interval(
                differences,
                bootstrap_replicates,
                DEFAULT_BLOCK_LENGTH,
                seed + 53,
            ),
        },
        "candidate_mean_hits_95_ci": _block_bootstrap_interval(
            hits,
            bootstrap_replicates,
            DEFAULT_BLOCK_LENGTH,
            seed + 71,
        ),
        "interpretation": (
            "Exploratory historical evaluation only. Intervals use a circular moving-block "
            "bootstrap and do not prove future predictive ability. The current isolated "
            "target was excluded from this evaluation pool."
        ),
    }


def _sample_target(rows, target_index, method_name, scorer, random_repeats, seed, scoring_rows=None):
    target_row = rows[target_index]
    target_date = target_row["date"]
    candidates = rows if scoring_rows is None else scoring_rows
    result = scorer(candidates, target_date)
    prediction = _safe_prediction(result)

    # Only after scoring has returned do we inspect this historical holdout's outcome.
    # rows supplied to this module are strictly pre-current-target records.
    actual = set(target_row["numbers"])
    random_mean, random_hits = _random_prediction_mean(
        actual, random_repeats, seed
    )
    model_hits = len(prediction.intersection(actual)) if prediction is not None else 0

    return {
        "date": target_date,
        "hits": model_hits,
        "prediction_made": prediction is not None,
        "random_baseline_mean_hits": random_mean,
        "random_all_hits": random_hits,
    }


def _forward_samples(rows, method_name, random_repeats, seed):
    scorer = METHODS[method_name]["score"]
    minimum = METHODS[method_name]["minimum_history"]
    samples = []
    for target_index in range(minimum, len(rows)):
        target_date = rows[target_index]["date"]
        sample_seed = seed + PROTOCOL_SALTS["forward_walk_forward"] + target_index * 1009
        samples.append(
            _sample_target(
                rows, target_index, method_name, scorer, random_repeats, sample_seed
            )
        )
    return samples


def _reverse_rows(rows):
    """Reverse known pre-target draws and assign clearly synthetic monotonic dates."""
    start = date(2000, 1, 1)
    reversed_draws = list(reversed(rows))
    return [
        {
            "date": (start + timedelta(days=index)).isoformat(),
            "numbers": list(row["numbers"]),
        }
        for index, row in enumerate(reversed_draws)
    ]


def _reverse_samples(rows, method_name, random_repeats, seed):
    reverse_rows = _reverse_rows(rows)
    scorer = METHODS[method_name]["score"]
    minimum = METHODS[method_name]["minimum_history"]
    samples = []
    for target_index in range(minimum, len(reverse_rows)):
        sample_seed = seed + PROTOCOL_SALTS["reverse_retrospective_stress_test"] + target_index * 1009
        samples.append(
            _sample_target(
                reverse_rows, target_index, method_name, scorer, random_repeats, sample_seed
            )
        )
    return samples


def _split_into_contiguous_folds(indices, maximum_folds=5):
    if not indices:
        return []
    count = min(maximum_folds, len(indices))
    folds = []
    for fold_index in range(count):
        start = fold_index * len(indices) // count
        end = (fold_index + 1) * len(indices) // count
        if start < end:
            folds.append(indices[start:end])
    return folds


def _cross_validation(rows, method_name, random_repeats, seed, bootstrap_replicates):
    scorer = METHODS[method_name]["score"]
    minimum = METHODS[method_name]["minimum_history"]
    target_indices = list(range(minimum, len(rows)))
    target_folds = _split_into_contiguous_folds(target_indices)
    fold_summaries = []
    all_samples = []

    for fold_number, fold_indices in enumerate(target_folds, start=1):
        fold_start = fold_indices[0]
        fixed_training_prefix = rows[:fold_start]
        fold_samples = []
        for target_index in fold_indices:
            target_row = rows[target_index]
            # For blocked CV, exclude all outcomes inside the held-out block from
            # training; include only the current target row so its date can be checked.
            scoring_rows = fixed_training_prefix + [target_row]
            sample_seed = (
                seed
                + PROTOCOL_SALTS["time_ordered_cross_validation"]
                + fold_number * 1000003
                + target_index * 1009
            )
            fold_samples.append(
                _sample_target(
                    rows,
                    target_index,
                    method_name,
                    scorer,
                    random_repeats,
                    sample_seed,
                    scoring_rows=scoring_rows,
                )
            )
        all_samples.extend(fold_samples)
        summarized = _summarize_samples(
            fold_samples,
            random_repeats,
            bootstrap_replicates,
            seed + fold_number * 10007,
        )
        fold_summaries.append({
            "fold": fold_number,
            "target_date_start": fold_samples[0]["date"],
            "target_date_end": fold_samples[-1]["date"],
            "fixed_training_record_count": fold_start,
            **summarized,
        })

    overall = _summarize_samples(
        all_samples, random_repeats, bootstrap_replicates, seed + 991
    )
    return {"summary": overall, "folds": fold_summaries}


def evaluate_method(rows, method_name, random_repeats=DEFAULT_RANDOM_REPEATS,
                    bootstrap_replicates=DEFAULT_BOOTSTRAP_REPLICATES, seed=20261026):
    """Evaluate one provisional method against random 6/60 picks on pre-target history."""
    _validate_historical_pool(rows)
    if method_name not in METHODS:
        raise ValueError("unknown formula proposal: " + str(method_name))
    if isinstance(random_repeats, bool) or not isinstance(random_repeats, int) or random_repeats < 1:
        raise ValueError("random_repeats must be a positive integer")
    if isinstance(bootstrap_replicates, bool) or not isinstance(bootstrap_replicates, int) or bootstrap_replicates < 0:
        raise ValueError("bootstrap_replicates must be a non-negative integer")

    forward = _forward_samples(rows, method_name, random_repeats, seed)
    reverse = _reverse_samples(rows, method_name, random_repeats, seed)
    cv = _cross_validation(
        rows, method_name, random_repeats, seed, bootstrap_replicates
    )
    return {
        "minimum_history_draws": METHODS[method_name]["minimum_history"],
        "forward_walk_forward": _summarize_samples(
            forward, random_repeats, bootstrap_replicates, seed + 101
        ),
        "reverse_retrospective_stress_test": _summarize_samples(
            reverse, random_repeats, bootstrap_replicates, seed + 202
        ),
        "time_ordered_cross_validation": cv,
    }


def run_evaluation(archive_path, random_repeats=DEFAULT_RANDOM_REPEATS,
                   bootstrap_replicates=DEFAULT_BOOTSTRAP_REPLICATES, seed=20261026):
    archive_file = Path(archive_path)
    try:
        payload = json.loads(archive_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("canonical archive could not be read or parsed: " + str(error)) from None
    if not isinstance(payload, dict) or not isinstance(payload.get("draws"), list):
        raise RuntimeError("canonical archive must be an object with a draws list")

    rows = payload["draws"]
    archive_check = data_guard.validate_archive(rows)
    if not isinstance(archive_check, dict) or archive_check.get("valid") is not True:
        raise RuntimeError("Data Guard rejected the canonical archive")

    current_target_date = archive_check.get("last_date")
    if not isinstance(current_target_date, str):
        raise RuntimeError("Data Guard did not provide the latest target date")

    isolation = data_guard.isolate_target(rows, current_target_date)
    if not isinstance(isolation, dict):
        raise RuntimeError("Data Guard returned an invalid isolation result")
    if isolation.get("isolation_pass") is not True:
        raise RuntimeError("TARGET ISOLATION FAILED")
    if isolation.get("target_present_in_archive") is not True:
        raise RuntimeError("TARGET ISOLATION FAILED: target is not present exactly in archive")
    if isolation.get("target_numbers_exposed") is not False:
        raise RuntimeError("TARGET ISOLATION FAILED: target numbers exposure flag invalid")

    historical_pool = isolation.get("train")
    if not isinstance(historical_pool, list):
        raise RuntimeError("Data Guard did not return a valid pre-target training pool")
    if any(row.get("date") >= current_target_date for row in historical_pool):
        raise RuntimeError("TARGET ISOLATION FAILED: target or later date found in history")
    _validate_historical_pool(historical_pool)

    proposal_file = Path(__file__).resolve().parent / "formula_archive" / "method_definition_proposals.json"
    try:
        proposal_document = json.loads(proposal_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise RuntimeError("provisional method definitions file is missing or invalid") from None
    proposal_methods = {
        entry.get("method_id"): entry
        for entry in proposal_document.get("proposals", [])
        if isinstance(entry, dict)
    }
    if proposal_document.get("binding_policy", {}).get("execution_enabled") is not False:
        raise RuntimeError("proposals must remain disabled during evaluation")
    if proposal_document.get("binding_policy", {}).get("canonical_registry_changed") is not False:
        raise RuntimeError("proposal document must not change the canonical registry")

    registry_file = Path(__file__).resolve().parent / "general_method_registry.json"
    registry = json.loads(registry_file.read_text(encoding="utf-8"))
    for method_name in METHODS:
        proposal = proposal_methods.get(method_name)
        canonical = registry.get("methods", {}).get(method_name)
        if not isinstance(proposal, dict) or not isinstance(canonical, dict):
            raise RuntimeError("required method definition is missing: " + method_name)
        if proposal.get("execution_ready") is not False:
            raise RuntimeError("proposal unexpectedly marked execution-ready: " + method_name)
        if canonical.get("status") != "OPEN_DEFINITION" or canonical.get("canonical_definition") is not None:
            raise RuntimeError("canonical registry changed without explicit approval: " + method_name)
        if canonical.get("execution_ready") is not False:
            raise RuntimeError("canonical method unexpectedly enabled: " + method_name)

    metrics = {}
    for method_name in METHODS:
        metrics[method_name] = evaluate_method(
            historical_pool,
            method_name,
            random_repeats=random_repeats,
            bootstrap_replicates=bootstrap_replicates,
            seed=seed,
        )

    # The report intentionally contains no winning numbers, predictions, or per-draw outcomes.
    return {
        "schema_version": 1,
        "project": "GENERAL Super Loto",
        "report_type": "experimental_chronological_formula_candidate_evaluation",
        "evaluation_version": "0.1.0",
        "method_definitions_status": "EXPERIMENTAL_PROPOSALS_ONLY",
        "current_isolated_target": {
            "date": current_target_date,
            "present_in_archive": True,
            "isolation_pass": True,
            "target_numbers_used_by_evaluation": False,
            "post_target_records_used": False,
        },
        "archive_integrity": {
            "valid": True,
            "record_count": archive_check.get("record_count"),
            "first_date": archive_check.get("first_date"),
            "last_date": archive_check.get("last_date"),
            "historical_pre_target_record_count": len(historical_pool),
            "historical_pool_first_date": historical_pool[0]["date"] if historical_pool else None,
            "historical_pool_last_date": historical_pool[-1]["date"] if historical_pool else None,
            "draw_numbers_in_report": False,
        },
        "evaluation_policy": {
            "forward_walk_forward": "For each past target in chronological order, train only on dates earlier than it; score the historical outcome after generating the candidate.",
            "reverse_retrospective_stress_test": "Reverse only the known pre-current-target draw sequence and relabel order with synthetic dates; retrospective stress test only, not predictive evidence.",
            "time_ordered_cross_validation": "Five contiguous test folds where each fold uses a fixed training prefix ending before its first test target; held-out outcomes in that fold are not added to training.",
            "null_baseline": "Repeated independent uniform random six-number sets from 1..60 for each same historical target; paired per-target difference reported.",
            "uncertainty": "Circular moving-block bootstrap 95% percentile interval with block length up to 20 targets; approximate and not proof.",
            "random_predictions_per_target": random_repeats,
            "bootstrap_replicates": bootstrap_replicates,
            "canonical_registry_modified": False,
            "real_current_target_used_for_tuning": False,
            "live_ai_api_calls": False,
        },
        "methods": metrics,
        "conclusion": (
            "Historical metrics are exploratory. No proposal is promoted automatically. "
            "A positive historical difference is not proof of future predictive ability; "
            "the current target remains isolated and all three methods remain disabled."
        ),
    }


def print_safe_summary(report):
    target = report["current_isolated_target"]
    print("FORMULA CANDIDATE EVALUATION: SUCCESS")
    print("Current target date:", target["date"])
    print("Current target isolation:", "PASS" if target["isolation_pass"] else "FAIL")
    print("Current target numbers used by evaluation:", "NO")
    print("Archive records:", report["archive_integrity"]["record_count"])
    print("Pre-target records used for historical evaluation:", report["archive_integrity"]["historical_pre_target_record_count"])
    print("Method definitions status:", report["method_definitions_status"])
    for method, methods_report in report["methods"].items():
        print("\nMETHOD:", method)
        for protocol in (
            "forward_walk_forward",
            "reverse_retrospective_stress_test",
            "time_ordered_cross_validation",
        ):
            entry = methods_report[protocol]
            summary = entry.get("summary", entry)
            diff = summary["paired_candidate_minus_random_mean_hits_per_target"]["mean"]
            diff_ci = summary["paired_candidate_minus_random_mean_hits_per_target"]["block_bootstrap_95_ci"]
            ci = (
                f"[{diff_ci['low']:.3f}, {diff_ci['high']:.3f}]"
                if diff_ci and diff is not None else "unavailable"
            )
            print(
                protocol + ":",
                "targets=", summary["eligible_target_draws"],
                "coverage=", f"{summary['coverage_rate']:.3f}",
                "mean_hits/eligible=", (
                    f"{summary['mean_hits_per_eligible_target_no_signal_as_zero']:.3f}"
                    if summary["mean_hits_per_eligible_target_no_signal_as_zero"] is not None else "unavailable"
                ),
                "mean_difference_vs_random=", (
                    f"{diff:.3f}" if diff is not None else "unavailable"
                ),
                "block-bootstrap-95% CI=", ci,
            )
    print("\nCANDIDATE NUMBERS OR WINNING DRAW NUMBERS DISPLAYED: NO")
    print("CANONICAL REGISTRY CHANGED: NO")
    print("NVIDIA API CALLED: NO")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", default="super_loto_archive.json")
    parser.add_argument("--output", default="formula-evaluation-output/report.json")
    parser.add_argument("--random-repeats", type=int, default=DEFAULT_RANDOM_REPEATS)
    parser.add_argument("--bootstrap-replicates", type=int, default=DEFAULT_BOOTSTRAP_REPLICATES)
    parser.add_argument("--seed", type=int, default=20261026)
    args = parser.parse_args()

    try:
        report = run_evaluation(
            args.archive,
            random_repeats=args.random_repeats,
            bootstrap_replicates=args.bootstrap_replicates,
            seed=args.seed,
        )
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print_safe_summary(report)
        print("AGGREGATE REPORT SAVED:", str(output_path))
    except Exception as error:
        print("FORMULA CANDIDATE EVALUATION FAILED:", type(error).__name__, str(error)[:400])
        sys.exit(1)


if __name__ == "__main__":
    main()
