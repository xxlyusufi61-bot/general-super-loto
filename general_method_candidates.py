"""Experimental implementations of legacy Super Loto method families.

IMPORTANT:
- These are clearly labelled research candidates, NOT approved canonical methods.
- The current isolated draw is never passed to this module. The caller supplies
  only draws strictly before the current target date.
- No predictions are emitted; outputs contain aggregate historical scores only.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

import general_data_guard as guard

VERSION = "0.1.0"
RANDOM_EXPECTED_HITS = 0.6
R60_START_DATE = "2020-08-02"
R54_MAX_NUMBER = 54
METHODS = ("abc_transition_to_c", "a_to_t_pair_counts", "mod5_state_to_t")


def _norm(draw: Iterable[int]) -> tuple[int, ...]:
    values = tuple(sorted(draw))
    if len(values) != 6 or len(set(values)) != 6 or any(type(x) is not int or not 1 <= x <= 60 for x in values):
        raise ValueError("Each draw must contain six distinct integers from 1 to 60.")
    return values


def _gaps(draw: tuple[int, ...]) -> tuple[int, ...]:
    return tuple(draw[i + 1] - draw[i] for i in range(5))


def _bands(draw: tuple[int, ...]) -> tuple[int, ...]:
    return tuple((x - 1) // 10 for x in draw)


def _shape(draw: tuple[int, ...]) -> tuple[int, ...]:
    gaps = _gaps(draw)
    middle = median(gaps)
    return tuple(1 if value >= middle else -1 for value in gaps)


def _transition_features(a: tuple[int, ...], b: tuple[int, ...]) -> dict[str, tuple[int, ...]]:
    ga, gb = _gaps(a), _gaps(b)
    pa = tuple(x % 2 for x in a)
    pb = tuple(x % 2 for x in b)
    ba, bb = _bands(a), _bands(b)
    sa, sb = _shape(a), _shape(b)
    return {
        "posdiff": tuple(b[i] - a[i] for i in range(6)),
        "gapdiff": tuple(gb[i] - ga[i] for i in range(5)),
        "parityflip": tuple(pb[i] - pa[i] for i in range(6)),
        "bandmove": tuple(bb[i] - ba[i] for i in range(6)),
        "mod5diff": tuple((b[i] % 5) - (a[i] % 5) for i in range(6)),
        "sumdiff": (sum(b) - sum(a),),
        "span_diff": ((b[-1] - b[0]) - (a[-1] - a[0]),),
        "shape_diff": tuple(sb[i] - sa[i] for i in range(5)),
    }


def _transform(values: tuple[int, ...], mode: str) -> tuple[int, ...]:
    if mode == "exact":
        return values
    if mode == "sign":
        return tuple(0 if x == 0 else (1 if x > 0 else -1) for x in values)
    if mode == "absolute":
        return tuple(abs(x) for x in values)
    raise ValueError("Unsupported transformation mode.")


def _distance(a: tuple[int, ...], b: tuple[int, ...], mode: str) -> int:
    if len(a) != len(b):
        return 10**9
    if mode == "exact":
        return 0 if a == b else 10**9
    if mode == "sign":
        return sum(x != y for x, y in zip(a, b))
    if mode == "absolute":
        return sum(abs(x - y) for x, y in zip(a, b))
    raise ValueError("Unsupported transformation mode.")


def _top_six(scores: Counter[int]) -> tuple[int, ...] | None:
    clean = {
        n: float(score)
        for n, score in scores.items()
        if type(n) is int and 1 <= n <= 60 and score > 0
    }
    if len(clean) < 6:
        return None
    ranked = sorted(clean.items(), key=lambda item: (-item[1], item[0]))[:6]
    return tuple(sorted(n for n, _ in ranked))


def predict_abc_transition_to_c(
    history: list[tuple[int, ...]],
    feature: str = "mod5diff",
    mode: str = "sign",
    k: int = 5,
) -> tuple[int, ...] | None:
    """Experimental A->B transition-signature match, voting on following C.

    The most recent known pair (A,B) is compared with older A_i->B_i
    transitions. Only cases where their C_i is already inside history are
    eligible; the current target draw is not an argument to this function.
    """
    if k < 1:
        raise ValueError("k must be at least 1.")
    if len(history) < 6:
        return None
    if feature not in _transition_features(history[0], history[1]):
        raise ValueError("Unknown transition feature.")
    current_signature = _transform(
        _transition_features(history[-2], history[-1])[feature], mode
    )
    candidates = []
    # Require C_i to be earlier than the current A. Avoid overlapping current A/B.
    for i in range(0, len(history) - 4):
        signature = _transform(_transition_features(history[i], history[i + 1])[feature], mode)
        distance = _distance(current_signature, signature, mode)
        candidates.append((distance, -i, history[i + 2]))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    votes: Counter[int] = Counter()
    for distance, _, c_draw in candidates[:min(k, len(candidates))]:
        weight = 1.0 / (1.0 + distance)
        for number in c_draw:
            votes[number] += weight
    return _top_six(votes)


def predict_a_to_t_pair_counts(history: list[tuple[int, ...]]) -> tuple[int, ...] | None:
    """Experimental legacy source-number -> next-draw-number count mapping.

    Mirrors the legacy pair-count idea: count x->y links for each adjacent
    pair fully contained in history, then aggregate target counts for numbers
    present in the latest known source draw.
    """
    if len(history) < 3:
        return None
    transition_counts: dict[int, Counter[int]] = {}
    for source, target in zip(history[:-1], history[1:]):
        for x in source:
            counter = transition_counts.setdefault(x, Counter())
            counter.update(target)
    scores: Counter[int] = Counter()
    for x in history[-1]:
        for number, count in transition_counts.get(x, {}).items():
            scores[number] += count
    return _top_six(scores)


def predict_mod5_state_to_t(
    history: list[tuple[int, ...]],
    k: int = 5,
) -> tuple[int, ...] | None:
    """Experimental MOD5-state similarity -> historical next-draw vote.

    A state is the six-position tuple (number % 5). Historical examples are
    source draw -> its already-known next draw. Closest residue states vote
    for their subsequent draws. This is a candidate definition, not canonical.
    """
    if k < 1:
        raise ValueError("k must be at least 1.")
    if len(history) < 3:
        return None
    current_state = tuple(n % 5 for n in history[-1])
    examples = []
    for i in range(len(history) - 1):
        state = tuple(n % 5 for n in history[i])
        distance = sum(a != b for a, b in zip(current_state, state))
        examples.append((distance, -i, history[i + 1]))
    examples.sort(key=lambda item: (item[0], item[1]))
    votes: Counter[int] = Counter()
    for distance, _, target_draw in examples[:min(k, len(examples))]:
        weight = 1.0 / (1.0 + distance)
        for number in target_draw:
            votes[number] += weight
    return _top_six(votes)


def _score(prediction: tuple[int, ...] | None, actual: tuple[int, ...]) -> int | None:
    if prediction is None or len(prediction) != 6:
        return None
    return len(set(prediction) & set(actual))


def _stats(values: list[int]) -> dict[str, Any]:
    distribution = Counter(values)
    return {
        "evaluated_draws": len(values),
        "mean_hits_out_of_6": round(mean(values), 6) if values else None,
        "hit_distribution": {f"{n}_hits": distribution.get(n, 0) for n in range(7)},
        "random_uniform_expected_hits_out_of_6": RANDOM_EXPECTED_HITS,
    }


def _predict_all(history: list[tuple[int, ...]]) -> dict[str, tuple[int, ...] | None]:
    return {
        "abc_transition_to_c": predict_abc_transition_to_c(history),
        "a_to_t_pair_counts": predict_a_to_t_pair_counts(history),
        "mod5_state_to_t": predict_mod5_state_to_t(history),
    }


def _evaluate_forward(draws: list[tuple[int, ...]], min_train: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    values: dict[str, list[int]] = {name: [] for name in METHODS}
    records = []
    for target_index in range(min_train, len(draws)):
        history = draws[:target_index]
        actual = draws[target_index]
        preds = _predict_all(history)
        row_scores = {}
        for name in METHODS:
            score = _score(preds[name], actual)
            if score is not None:
                values[name].append(score)
                row_scores[name] = score
        records.append({"target_index": target_index, "scores": row_scores})
    return {name: _stats(scores) for name, scores in values.items()}, records


def _evaluate_reverse(draws: list[tuple[int, ...]], min_train: int) -> dict[str, Any]:
    # Retrospective only: later original draws are intentionally used to test earlier ones.
    reversed_draws = list(reversed(draws))
    values: dict[str, list[int]] = {name: [] for name in METHODS}
    for target_index in range(min_train, len(reversed_draws)):
        history = reversed_draws[:target_index]
        actual = reversed_draws[target_index]
        preds = _predict_all(history)
        for name in METHODS:
            score = _score(preds[name], actual)
            if score is not None:
                values[name].append(score)
    return {
        "predictive_evidence": False,
        "protocol": "reverse-time retrospective stress test; later original draws can be used",
        "baselines": {name: _stats(scores) for name, scores in values.items()},
    }


def _quantile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("Cannot compute a quantile for empty values.")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _random_prediction_null(
    draws: list[tuple[int, ...]],
    records: list[dict[str, Any]],
    score_sets: dict[str, list[int]],
    replications: int = 1000,
    seed: int = 20261023,
) -> dict[str, Any]:
    """Compare fixed historical model scores with random 6-of-60 selections.

    Only historical targets from the pre-current-target history are used here.
    This is a Monte Carlo random-prediction reference, not a permutation test
    and not a proof of independence between historical draws.
    """
    if replications < 100:
        raise ValueError("At least 100 random-baseline replications are required.")

    pool = tuple(range(1, 61))
    rng = random.Random(seed)
    methods = {}
    for method in METHODS:
        test_actuals = [
            draws[record["target_index"]]
            for record in records
            if method in record["scores"]
        ]
        observed_scores = score_sets[method]
        if len(test_actuals) != len(observed_scores) or not test_actuals:
            raise ValueError("Random baseline and method score lengths do not match.")

        null_means = []
        for _ in range(replications):
            total = 0
            for actual in test_actuals:
                random_pick = set(rng.sample(pool, 6))
                total += len(random_pick.intersection(actual))
            null_means.append(total / len(test_actuals))

        observed_mean = mean(observed_scores)
        simulated_mean = mean(null_means)
        p_upper = (1 + sum(value >= observed_mean for value in null_means)) / (replications + 1)
        methods[method] = {
            "evaluated_draws": len(test_actuals),
            "observed_mean_hits_out_of_6": round(observed_mean, 6),
            "simulated_random_mean_hits_out_of_6": round(simulated_mean, 6),
            "delta_vs_simulated_random_mean": round(observed_mean - simulated_mean, 6),
            "random_mean_95pct_interval": [
                round(_quantile(null_means, 0.025), 6),
                round(_quantile(null_means, 0.975), 6),
            ],
            "one_sided_random_pick_tail_probability": round(p_upper, 6),
            "p_value_adjusted_for_multiple_methods": False,
        }

    return {
        "protocol": "Monte Carlo: independent random 6-of-60 picks scored against the exact same historical target draws",
        "replications": replications,
        "seed": seed,
        "theoretical_expected_mean_hits_out_of_6": RANDOM_EXPECTED_HITS,
        "current_isolated_target_used": False,
        "interpretation_limit": "Descriptive comparison only; not a formal proof, and the tail probabilities are not adjusted for trying multiple hypotheses.",
        "methods": methods,
    }


def _walk_forward_fold_summaries(records: list[dict[str, Any]], folds: int) -> dict[str, Any]:
    if folds < 2:
        raise ValueError("folds must be at least 2.")
    if not records:
        return {"folds": folds, "protocol": "descriptive summaries of expanding-window walk-forward scores; not nested CV or per-fold model selection", "fold_summaries": [], "independent_fold_model_selection": False}
    fold_summaries = []
    for fold in range(folds):
        start = len(records) * fold // folds
        end = len(records) * (fold + 1) // folds
        chunk = records[start:end]
        row = {"fold": fold + 1, "validation_records": len(chunk), "methods": {}}
        for method in METHODS:
            scores = [r["scores"][method] for r in chunk if method in r["scores"]]
            row["methods"][method] = _stats(scores)
        fold_summaries.append(row)
    return {"folds": folds, "protocol": "contiguous chronological summaries of the same expanding-window walk-forward predictions; not nested CV and no per-fold parameter tuning", "fold_summaries": fold_summaries, "independent_fold_model_selection": False}


def evaluate_rows(
    rows: Iterable[dict[str, Any]],
    isolated_target_date: str,
    min_train: int = 100,
    folds: int = 5,
) -> dict[str, Any]:
    """Evaluate candidate methods using only rows strictly before target date."""
    if min_train < 6:
        raise ValueError("min_train must be at least 6.")
    if folds < 2:
        raise ValueError("folds must be at least 2.")
    target_day = guard.parse_date(isolated_target_date)
    r60_start = guard.parse_date(R60_START_DATE)
    if target_day < r60_start:
        raise ValueError("R54 evaluation is not implemented in this candidate engine; evaluation stopped.")
    # Materialize once: validate_archive consumes general iterables, so doing
    # this in the opposite order can silently empty a generator input.
    clean_rows = list(rows)
    summary = guard.validate_archive(clean_rows)
    if any(guard.parse_date(row["date"]) >= target_day for row in clean_rows):
        raise guard.DataGuardError("Candidate engine received target-date or future data.")
    if guard.parse_date(summary["last_date"]) >= target_day:
        raise guard.DataGuardError("Candidate engine history must end strictly before isolated target.")

    # Binding project rule: R54 and R60 regimes must never be pooled.
    r54_rows = [row for row in clean_rows if guard.parse_date(row["date"]) < r60_start]
    if any(any(number > R54_MAX_NUMBER for number in row["numbers"]) for row in r54_rows):
        raise guard.DataGuardError("Pre-R60 record exceeds the R54 number pool; regime audit failed.")
    r60_rows = [row for row in clean_rows if guard.parse_date(row["date"]) >= r60_start]
    if len(r60_rows) <= min_train:
        raise ValueError("Insufficient R60-only history for candidate evaluation.")
    r60_summary = guard.validate_archive(r60_rows)
    if guard.parse_date(r60_summary["last_date"]) >= target_day:
        raise guard.DataGuardError("R60 evaluation history must end strictly before target.")
    draws = [_norm(row["numbers"]) for row in r60_rows]
    forward_summary, forward_records = _evaluate_forward(draws, min_train)
    usable_records = [record for record in forward_records if record["scores"]]
    random_null = _random_prediction_null(
        draws, usable_records, {
            name: [
                record["scores"][name]
                for record in usable_records
                if name in record["scores"]
            ]
            for name in METHODS
        }
    )
    return {
        "engine_version": VERSION,
        "method_status": "EXPERIMENTAL_CANDIDATES_NOT_CANONICAL",
        "isolated_target_date": isolated_target_date,
        "current_target_numbers_provided": False,
        "current_target_numbers_used_for_scoring": False,
        "current_target_numbers_exposed": False,
        "pre_target_history_records_before_regime_filter": len(clean_rows),
        "active_regime": "R60",
        "active_regime_start_date": R60_START_DATE,
        "active_regime_history_records": len(draws),
        "excluded_prior_R54_regime_records": len(r54_rows),
        "mixed_R54_R60_training_used": False,
        "minimum_training_draws": min_train,
        "forward_walk_forward": {
            "protocol": "expanding-window; every historical test draw uses only earlier draws",
            "baselines": forward_summary,
        },
        "reverse_retrospective_stress_test": _evaluate_reverse(draws, min_train),
        "random_prediction_null": random_null,
        "chronological_fold_summaries": _walk_forward_fold_summaries(usable_records, folds),
        "full_nested_time_series_cross_validation_implemented": False,
        "canonical_method_definitions_status": {
            "A->B->C": "OPEN_DEFINITION",
            "A->T": "OPEN_DEFINITION",
            "MOD5->T": "OPEN_DEFINITION",
        },
        "interpretation_limits": [
            "These are explicit experimental candidate definitions, not approved canonical formulas.",
            "R54 records before 2020-08-02 are excluded; only the R60 regime is analyzed.",
            "The current isolated target's number set is not passed to the candidate functions or scored.",
            "A Monte Carlo random-prediction reference was computed against the same historical target draws; it is not a permutation test.",
            "Chronological fold summaries group the same walk-forward predictions; no per-fold parameter selection or nested cross-validation was performed.",
            "Reverse-time results are retrospective stress tests, not predictive evidence.",
            "Average historical overlap does not prove a predictive mechanism.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate experimental method candidates on pre-target history only.")
    parser.add_argument("--history", required=True, help="JSON file containing only pre-target draw records.")
    parser.add_argument("--target-date", required=True, help="Isolated target date; its numbers must not be included.")
    parser.add_argument("--min-train", type=int, default=100)
    parser.add_argument("--folds", type=int, default=5)
    args = parser.parse_args()
    payload = json.loads(Path(args.history).read_text(encoding="utf-8"))
    rows = payload.get("draws") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise SystemExit("CANDIDATE EVALUATION FAILED: history JSON must contain a list of draw records.")
    report = evaluate_rows(rows, args.target_date, args.min_train, args.folds)
    print("GENERAL METHOD CANDIDATE REPORT")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        # Never echo input data or exception payloads that may contain draw numbers.
        raise SystemExit("CANDIDATE EVALUATION FAILED: " + type(exc).__name__) from None
