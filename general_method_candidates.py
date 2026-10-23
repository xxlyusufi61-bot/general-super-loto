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
from collections import Counter
from datetime import date
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

import general_data_guard as guard

VERSION = "0.1.0"
RANDOM_EXPECTED_HITS = 0.6
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
    if len(history) < 6 or k < 1:
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
    if len(history) < 3 or k < 1:
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


def _blocked_cv(records: list[dict[str, Any]], folds: int) -> dict[str, Any]:
    if folds < 2:
        raise ValueError("folds must be at least 2.")
    if not records:
        return {"folds": folds, "protocol": "blocked chronological folds; no shuffling", "fold_summaries": []}
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
    return {"folds": folds, "protocol": "blocked chronological folds; no shuffling", "fold_summaries": fold_summaries}


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
    summary = guard.validate_archive(rows)
    clean_rows = list(rows)
    if any(guard.parse_date(row["date"]) >= target_day for row in clean_rows):
        raise guard.DataGuardError("Candidate engine received target-date or future data.")
    if guard.parse_date(summary["last_date"]) >= target_day:
        raise guard.DataGuardError("Candidate engine history must end strictly before isolated target.")
    draws = [_norm(row["numbers"]) for row in clean_rows]
    forward_summary, forward_records = _evaluate_forward(draws, min_train)
    usable_records = [record for record in forward_records if record["scores"]]
    return {
        "engine_version": VERSION,
        "method_status": "EXPERIMENTAL_CANDIDATES_NOT_CANONICAL",
        "isolated_target_date": isolated_target_date,
        "current_target_numbers_provided": False,
        "current_target_numbers_used_for_scoring": False,
        "current_target_numbers_exposed": False,
        "pre_target_history_records": len(draws),
        "minimum_training_draws": min_train,
        "forward_walk_forward": {
            "protocol": "expanding-window; every historical test draw uses only earlier draws",
            "baselines": forward_summary,
        },
        "reverse_retrospective_stress_test": _evaluate_reverse(draws, min_train),
        "time_ordered_cross_validation": _blocked_cv(usable_records, folds),
        "canonical_method_definitions_status": {
            "A->B->C": "OPEN_DEFINITION",
            "A->T": "OPEN_DEFINITION",
            "MOD5->T": "OPEN_DEFINITION",
        },
        "interpretation_limits": [
            "These are explicit experimental candidate definitions, not approved canonical formulas.",
            "The current isolated target's number set is not passed to the candidate functions or scored.",
            "The theoretical 0.6 mean is a random 6-of-60 overlap benchmark, not an observed experiment.",
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
