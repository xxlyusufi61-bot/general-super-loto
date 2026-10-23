"""Real-archive chronological evaluation harness for GENERAL.

The latest archive row is treated as the isolated target and is never passed into
feature selection, prediction, validation, or final scoring. The report contains
aggregate hit counts only; it does not print predicted or actual number sets.

This is an exploratory legacy-feature benchmark, not an approved canonical
implementation of A->B->C, A->T, or MOD5->T.
"""
from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Sequence

from v86_leakage_safe_evaluator import (
    Draw, FEATURES, TRANSFORMS, validate_history, predict_from_known_pair, hits
)

ROOT = Path(__file__).resolve().parent
ARCHIVE_PATH = ROOT / "super_loto_archive.json"
REPORT_PATH = ROOT / "real_archive_evaluation_report.json"
LOCKED_DATES = {date(2021, 1, 26), date(2021, 1, 28)}
K_VALUES = (1, 3, 5, 10)
RNG_SEED = 20261024
MIN_HISTORY = 5


@dataclass(frozen=True)
class Candidate:
    feature: str
    mode: str
    k: int


def load_and_validate_archive(path: Path = ARCHIVE_PATH) -> tuple[Draw, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload.get("draws")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Archive must contain a non-empty 'draws' list.")
    rows = []
    seen = set()
    previous = None
    for item in raw:
        if not isinstance(item, dict) or set(("date", "numbers")) - set(item):
            raise ValueError("Archive row must contain date and numbers.")
        day = date.fromisoformat(item["date"])
        if day in seen:
            raise ValueError("Duplicate archive date.")
        if previous is not None and day <= previous:
            raise ValueError("Archive is not strictly chronological.")
        seen.add(day)
        previous = day
        nums = item["numbers"]
        if (not isinstance(nums, list) or len(nums) != 6
                or any(not isinstance(n, int) or isinstance(n, bool) for n in nums)
                or len(set(nums)) != 6 or any(n < 1 or n > 60 for n in nums)
                or nums != sorted(nums)):
            raise ValueError("Invalid six-number draw record.")
        rows.append(Draw(day, tuple(nums)))
    return tuple(rows)


def candidate_grid() -> tuple[Candidate, ...]:
    return tuple(Candidate(f, m, k) for f in FEATURES for m in TRANSFORMS for k in K_VALUES)


def precompute_signatures(rows: Sequence[Draw]):
    """Compute each adjacent-draw feature once; reuse it across all candidates."""
    result = {(feature, mode): [] for feature in FEATURES for mode in TRANSFORMS}
    for left, right in zip(rows[:-1], rows[1:]):
        raw_features = state_features(left.numbers, right.numbers)
        for feature in FEATURES:
            values = raw_features[feature]
            for mode in TRANSFORMS:
                result[(feature, mode)].append(transform(values, mode))
    return {key: tuple(values) for key, values in result.items()}


def predict_at(rows: Sequence[Draw], target_index: int, candidate: Candidate, signature_cache=None):
    # The target row is intentionally not passed to the predictor.
    history = tuple(rows[:target_index])
    if signature_cache is None:
        return predict_from_known_pair(history, candidate.feature, candidate.mode, candidate.k)

    # Match the legacy-safe evaluator's exact query/triple geometry:
    # query is transition rows[target_index-2] -> rows[target_index-1];
    # candidate A->B->C triples must finish strictly before query A.
    if target_index < MIN_HISTORY:
        return None
    query_transition_index = target_index - 2
    key = (candidate.feature, candidate.mode)
    signatures = signature_cache[key]
    query_signature = signatures[query_transition_index]
    ranked = []
    for j in range(0, target_index - 4):
        d = distance(query_signature, signatures[j], candidate.mode)
        ranked.append((d, j))
    ranked.sort(key=lambda pair: (pair[0], pair[1]))
    chosen = ranked[:candidate.k]
    votes = Counter()
    for d, j in chosen:
        weight = 1.0 / (1.0 + d)
        for number in rows[j + 2].numbers:
            votes[number] += weight
    if len(votes) < 6:
        return None
    winners = sorted(votes.items(), key=lambda pair: (-pair[1], pair[0]))[:6]
    return tuple(sorted(number for number, _ in winners))


def score_window(rows: Sequence[Draw], start: int, end: int, candidate: Candidate, signature_cache=None):
    if signature_cache is None:
        signature_cache = precompute_signatures(rows)
    values = []
    for idx in range(max(start, MIN_HISTORY), min(end, len(rows))):
        pred = predict_at(rows, idx, candidate, signature_cache)
        values.append(hits(pred, rows[idx].numbers))
    return values


def select_candidate(rows: Sequence[Draw], candidates, start: int, end: int):
    scored = []
    signature_cache = precompute_signatures(rows)
    for c in candidates:
        values = score_window(rows, start, end, c, signature_cache)
        scored.append((mean(values) if values else 0.0, c, len(values)))
    # Deterministic tie break; final interval is never read here.
    scored.sort(key=lambda x: (-x[0], x[1].feature, x[1].mode, x[1].k))
    return scored[0], scored


def random_baseline_mean_hits(n_targets: int) -> float:
    # Exact expectation for independent uniform 6-of-60 vs actual 6-of-60.
    # E[intersection] = 6 * 6 / 60 = 0.6; no simulated target sets needed.
    return 0.6 if n_targets > 0 else 0.0


def main():
    all_rows = load_and_validate_archive()
    # The last archive row is the isolated target, and is not included below.
    isolated_target_date = all_rows[-1].day
    pre_target = tuple(r for r in all_rows[:-1] if r.day not in LOCKED_DATES)

    # Binding project cutoff: keep R54 and R60 regimes separate at 2020-08-02.
    # Do not infer the regime boundary from the first draw containing a 55+ number.
    r60_start = date(2020, 8, 2)
    r54 = tuple(r for r in pre_target if r.day < r60_start)
    if any(max(r.numbers) > 54 for r in r54):
        raise ValueError("R54 regime contains a number above 54.")
    r60 = tuple(r for r in pre_target if r.day >= r60_start)
    if len(r60) < 100:
        raise ValueError("Insufficient R60 history for chronological evaluation.")

    # Chronological split: initial 60% is model warm-up/training history;
    # next 20% is validation for candidate selection; final 20% is frozen test.
    n = len(r60)
    validation_start = max(MIN_HISTORY, int(n * 0.60))
    final_start = max(validation_start + 1, int(n * 0.80))
    final_end = n
    candidates = candidate_grid()
    (best_val, chosen, n_validation), _ = select_candidate(
        r60, candidates, validation_start, final_start
    )
    signature_cache = precompute_signatures(r60)
    final_hits = score_window(r60, final_start, final_end, chosen, signature_cache)
    reversed_r60 = tuple(reversed(r60))
    reverse_hits = score_window(reversed_r60, MIN_HISTORY, max(MIN_HISTORY, n - final_start), chosen)

    # Chronological fold summaries use the selected validation-only candidate.
    fold_edges = [validation_start + (final_start - validation_start) * i // 4 for i in range(5)]
    folds = []
    for i in range(4):
        vals = score_window(r60, fold_edges[i], fold_edges[i + 1], chosen, signature_cache)
        folds.append({
            "fold": i + 1,
            "target_count": len(vals),
            "mean_hits": round(mean(vals), 6) if vals else None,
        })

    report = {
        "schema_version": 1,
        "project": "GENERAL Super Loto",
        "evaluation": "real_archive_legacy_feature_benchmark_v0.1",
        "status": "COMPLETED_NO_PREDICTION_FOR_ISOLATED_TARGET",
        "binding_rules_changed": False,
        "official_source_verification_required": False,
        "archive_integrity": {
            "row_count": len(all_rows),
            "first_date": all_rows[0].day.isoformat(),
            "isolated_target_present": True,
            "isolated_target_excluded_from_training_and_scoring": True,
            "isolated_target_numbers_written_to_report": False,
            "locked_dates_excluded": True,
            "r54_row_count_before_target": len(r54),
            "r60_start_date_rule": "binding cutoff 2020-08-02",
            "r60_start_date": r60_start.isoformat(),
            "r60_row_count_before_target": len(r60),
        },
        "protocol": {
            "candidate_count": len(candidates),
            "features": list(FEATURES),
            "transforms": list(TRANSFORMS),
            "k_values": list(K_VALUES),
            "validation_range_indices": [validation_start, final_start],
            "frozen_final_range_indices": [final_start, final_end],
            "candidate_selected_on_validation_only": True,
            "target_numbers_passed_to_predictor": False,
            "random_baseline": "exact_expected_hits_for_uniform_random_6_of_60",
            "reverse_protocol": "RETROSPECTIVE_STRESS_TEST_NOT_PREDICTIVE",
        },
        "selected_candidate": {
            "feature": chosen.feature,
            "transform": chosen.mode,
            "k": chosen.k,
            "validation_mean_hits": round(best_val, 6),
            "validation_target_count": n_validation,
        },
        "frozen_final_test": {
            "target_count": len(final_hits),
            "mean_hits": round(mean(final_hits), 6) if final_hits else None,
            "median_hits": sorted(final_hits)[len(final_hits) // 2] if final_hits else None,
            "random_baseline_expected_mean_hits": random_baseline_mean_hits(len(final_hits)),
            "beats_random_expected_mean": bool(final_hits and mean(final_hits) > 0.6),
            "per_target_actual_or_predicted_numbers_in_report": False,
        },
        "chronological_validation_folds": folds,
        "reverse_stress_test": {
            "label": "RETROSPECTIVE_STRESS_TEST_NOT_PREDICTIVE",
            "target_count": len(reverse_hits),
            "mean_hits": round(mean(reverse_hits), 6) if reverse_hits else None,
        },
        "limitations": [
            "This evaluates only legacy transition features; it does not define or promote canonical A->B->C, A->T, or MOD5->T.",
            "The selected candidate was chosen from many candidates; this single holdout is exploratory and does not establish a predictive mechanism.",
            "No uncertainty interval or multiple-hypothesis correction is applied in this initial benchmark.",
            "The isolated latest draw is excluded completely from all calculations and its numbers are never written to the report.",
            "R60 regime start is inferred from the first draw containing a number above 54; archive integrity checks do not establish official-source truth."
        ]
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "archive_row_count": len(all_rows),
        "isolated_target_excluded": report["archive_integrity"]["isolated_target_excluded_from_training_and_scoring"],
        "isolated_target_numbers_written": report["archive_integrity"]["isolated_target_numbers_written_to_report"],
        "r60_row_count": len(r60),
        "candidate_count": len(candidates),
        "validation_target_count": n_validation,
        "final_target_count": len(final_hits),
        "selected_feature": chosen.feature,
        "selected_transform": chosen.mode,
        "selected_k": chosen.k,
        "final_mean_hits": report["frozen_final_test"]["mean_hits"],
        "random_baseline_expected_mean_hits": 0.6,
        "reverse_stress_label": report["reverse_stress_test"]["label"],
        "report_path": str(REPORT_PATH.name),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
