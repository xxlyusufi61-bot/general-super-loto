"""Offline hypothesis search for new Super Loto method families.

No AI/provider API calls are made. The bounded search space is fixed in code,
the latest archive draw is isolated by Data Guard, R54 and R60 are evaluated
separately, and all outputs are aggregate metrics only. Nothing is promoted.
"""
import argparse
import json
import random
import sys
from datetime import date
from pathlib import Path
from statistics import mean

import general_data_guard as guard

VERSION = "0.1.0"
R60_START = date(2020, 8, 2)
DRAW_SIZE = 6
NUMBER_MAX_R54 = 54
NUMBER_MAX_R60 = 60
MIN_HISTORY = 20
DEFAULT_RANDOM_REPEATS = 40
DEFAULT_BOOTSTRAP_REPLICATES = 250
DEFAULT_BLOCK_LENGTH = 20

CANDIDATES = (
    {"candidate_id": "recent_frequency_w20", "family": "recent_frequency", "window": 20, "min_history": MIN_HISTORY},
    {"candidate_id": "recent_frequency_w50", "family": "recent_frequency", "window": 50, "min_history": MIN_HISTORY},
    {"candidate_id": "recent_frequency_w100", "family": "recent_frequency", "window": 100, "min_history": MIN_HISTORY},
    {"candidate_id": "recent_frequency_w200", "family": "recent_frequency", "window": 200, "min_history": MIN_HISTORY},
    {"candidate_id": "decay_frequency_hl20", "family": "decay_frequency", "half_life": 20, "min_history": MIN_HISTORY},
    {"candidate_id": "decay_frequency_hl50", "family": "decay_frequency", "half_life": 50, "min_history": MIN_HISTORY},
    {"candidate_id": "decay_frequency_hl100", "family": "decay_frequency", "half_life": 100, "min_history": MIN_HISTORY},
    {"candidate_id": "last_seen_gap", "family": "last_seen_gap", "min_history": MIN_HISTORY},
    {"candidate_id": "profile_neighbors_k5", "family": "profile_neighbors", "k": 5, "min_history": MIN_HISTORY},
    {"candidate_id": "profile_neighbors_k10", "family": "profile_neighbors", "k": 10, "min_history": MIN_HISTORY},
    {"candidate_id": "profile_neighbors_k20", "family": "profile_neighbors", "k": 20, "min_history": MIN_HISTORY},
    {"candidate_id": "gap_neighbors_k5", "family": "gap_neighbors", "k": 5, "min_history": MIN_HISTORY},
    {"candidate_id": "gap_neighbors_k10", "family": "gap_neighbors", "k": 10, "min_history": MIN_HISTORY},
    {"candidate_id": "gap_neighbors_k20", "family": "gap_neighbors", "k": 20, "min_history": MIN_HISTORY},
    {"candidate_id": "position_neighbors_k5", "family": "position_neighbors", "k": 5, "min_history": MIN_HISTORY},
    {"candidate_id": "position_neighbors_k10", "family": "position_neighbors", "k": 10, "min_history": MIN_HISTORY},
    {"candidate_id": "position_neighbors_k20", "family": "position_neighbors", "k": 20, "min_history": MIN_HISTORY},
)

REGIMES = {
    "R54": {"start": None, "end": "2020-08-01", "universe_size": NUMBER_MAX_R54},
    "R60": {"start": "2020-08-02", "end": None, "universe_size": NUMBER_MAX_R60},
}

def _load_rows(path):
    source = Path(path)
    if not source.is_file():
        raise ValueError("Archive file is missing.")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError("Archive file is unreadable or invalid JSON.") from None
    if isinstance(payload, dict):
        if "draws" in payload and "records" in payload:
            raise ValueError("Archive has ambiguous draws/records fields.")
        rows = payload.get("draws", payload.get("records"))
    else:
        rows = payload
    if not isinstance(rows, list):
        raise ValueError("Archive must contain a list of draw records.")
    return rows

def _date(row):
    return date.fromisoformat(row["date"])

def _validate_regime_ranges(rows):
    for row in rows:
        if _date(row) < R60_START:
            if any(number > NUMBER_MAX_R54 for number in row["numbers"]):
                raise ValueError("R54 record contains a value above 54; regime audit failed.")
        if _date(row) >= R60_START and any(
            number > NUMBER_MAX_R60 for number in row["numbers"]
        ):
            raise ValueError("R60 record contains a value above 60; regime audit failed.")

def _profile(draw):
    decade = [0] * 6
    mod3 = [0] * 3
    mod5 = [0] * 5
    odd = 0
    for number in draw:
        decade[(number - 1) // 10] += 1
        mod3[number % 3] += 1
        mod5[number % 5] += 1
        odd += number % 2
    # Each component is bounded and scaled so raw sum size does not dominate.
    return tuple(
        [value / DRAW_SIZE for value in decade]
        + [odd / DRAW_SIZE]
        + [value / DRAW_SIZE for value in mod3]
        + [value / DRAW_SIZE for value in mod5]
        + [sum(draw) / 360.0, (draw[-1] - draw[0]) / 59.0]
    )

def _gap_vector(draw):
    return tuple((draw[index + 1] - draw[index]) / 59.0 for index in range(5))

def _position_vector(draw, universe_size):
    return tuple(number / float(universe_size) for number in draw)

def _l1(left, right):
    return sum(abs(a - b) for a, b in zip(left, right))

def _score(history, candidate, universe_size):
    """Return scores for one candidate using history only."""
    if len(history) < candidate["min_history"]:
        raise ValueError("Insufficient history for candidate.")
    scores = [0.0] * universe_size
    family = candidate["family"]

    if family == "recent_frequency":
        window = min(candidate["window"], len(history))
        for age, draw in enumerate(reversed(history[-window:])):
            # Newer draws all receive the same unit weight in this family.
            for number in draw:
                scores[number - 1] += 1.0
    elif family == "decay_frequency":
        half_life = float(candidate["half_life"])
        for age, draw in enumerate(reversed(history)):
            weight = 2.0 ** (-age / half_life)
            for number in draw:
                scores[number - 1] += weight
    elif family == "last_seen_gap":
        last_seen = [-1] * universe_size
        for index, draw in enumerate(history):
            for number in draw:
                last_seen[number - 1] = index
        for index, seen_at in enumerate(last_seen):
            # This is an explicit cold/overdue hypothesis, not an assumption
            # that a number is actually due to appear.
            scores[index] = len(history) if seen_at < 0 else len(history) - 1 - seen_at
    elif family in {"profile_neighbors", "gap_neighbors", "position_neighbors"}:
        k = candidate["k"]
        if family == "profile_neighbors":
            features = _profile
            current = features(history[-1])
            distance = lambda draw: _l1(current, features(draw))
        elif family == "gap_neighbors":
            current = _gap_vector(history[-1])
            distance = lambda draw: _l1(current, _gap_vector(draw))
        else:
            current = _position_vector(history[-1], universe_size)
            distance = lambda draw: _l1(current, _position_vector(draw, universe_size))

        # A historical source is eligible only when its following draw also
        # lies strictly inside the supplied training history.
        matches = []
        for index in range(len(history) - 1):
            matches.append((distance(history[index]), -index, history[index + 1]))
        matches.sort(key=lambda item: (item[0], item[1]))
        for rank, (_, _, outcome) in enumerate(matches[:min(k, len(matches))], start=1):
            weight = 1.0 / rank
            for number in outcome:
                scores[number - 1] += weight
    else:
        raise ValueError("Unknown candidate family.")

    return scores

def _ranked_top_six(scores):
    if len(scores) < DRAW_SIZE:
        raise ValueError("Score vector cannot produce six distinct candidates.")
    return [number for number, _ in sorted(
        enumerate(scores, start=1), key=lambda item: (-item[1], item[0])
    )[:DRAW_SIZE]]

def _hits(history, target, candidate, universe_size):
    ranking = _ranked_top_six(_score(history, candidate, universe_size))
    return len(set(ranking).intersection(target))

def _random_mean_hits(actual, repeats, seed, universe_size):
    rng = random.Random(seed)
    universe = range(1, universe_size + 1)
    values = [
        len(set(rng.sample(universe, DRAW_SIZE)).intersection(actual))
        for _ in range(repeats)
    ]
    return mean(values)

def _block_bootstrap_ci(values, replicates, block_length, seed):
    values = [float(value) for value in values]
    if not values:
        return None
    point = mean(values)
    # A block length equal to or large relative to the whole series can make
    # every bootstrap sample a rotation of the same data, falsely collapsing
    # the interval to a single point. Mark short series as insufficient instead.
    if len(values) < max(40, 2 * block_length):
        return None
    if replicates <= 0:
        return {"low": point, "high": point, "method": "degenerate_no_bootstrap"}
    rng = random.Random(seed)
    n = len(values)
    width = min(block_length, n // 2)
    estimates = []
    for _ in range(replicates):
        sample = []
        while len(sample) < n:
            start = rng.randrange(n)
            for offset in range(width):
                sample.append(values[(start + offset) % n])
                if len(sample) >= n:
                    break
        estimates.append(mean(sample))
    estimates.sort()
    low_index = int(0.025 * (len(estimates) - 1))
    high_index = int(0.975 * (len(estimates) - 1))
    return {
        "low": estimates[low_index],
        "high": estimates[high_index],
        "method": "circular_moving_block_bootstrap_percentile_95",
        "block_length": width,
        "replicates": replicates,
    }

def _summarize(candidate_hits, random_hits, seed, bootstrap_replicates):
    if not candidate_hits:
        return {
            "eligible_target_draws": 0,
            "mean_candidate_hits_per_target": None,
            "mean_random_hits_per_target": None,
            "mean_candidate_minus_random": None,
            "paired_difference_block_bootstrap_95_ci": None,
        }
    differences = [candidate - baseline for candidate, baseline in zip(candidate_hits, random_hits)]
    return {
        "eligible_target_draws": len(candidate_hits),
        "mean_candidate_hits_per_target": mean(candidate_hits),
        "mean_random_hits_per_target": mean(random_hits),
        "mean_candidate_minus_random": mean(differences),
        "paired_difference_block_bootstrap_95_ci": _block_bootstrap_ci(
            differences, bootstrap_replicates, DEFAULT_BLOCK_LENGTH, seed
        ),
    }

def _evaluate_forward(draws, candidate, universe_size, random_repeats, seed, bootstrap_replicates):
    hits, random_hits = [], []
    min_history = candidate["min_history"]
    for target_index in range(min_history, len(draws)):
        history = draws[:target_index]
        target = draws[target_index]
        hits.append(_hits(history, target, candidate, universe_size))
        random_hits.append(_random_mean_hits(
            target, random_repeats, seed + target_index * 1009, universe_size
        ))
    return _summarize(hits, random_hits, seed, bootstrap_replicates)

def _evaluate_reverse(draws, candidate, universe_size, random_repeats, seed, bootstrap_replicates):
    # Reverse-time results are explicitly retrospective stress tests only.
    return _evaluate_forward(
        list(reversed(draws)), candidate, universe_size, random_repeats,
        seed + 701, bootstrap_replicates
    )

def _chunks(values, count):
    size, extra = divmod(len(values), count)
    output, offset = [], 0
    for index in range(count):
        width = size + (1 if index < extra else 0)
        if width:
            output.append(values[offset:offset + width])
        offset += width
    return output

def _evaluate_cv(draws, candidate, universe_size, random_repeats, seed, bootstrap_replicates):
    target_indices = list(range(candidate["min_history"], len(draws)))
    folds = _chunks(target_indices, 5)
    fold_reports = []
    all_hits, all_random = [], []
    for fold_index, fold in enumerate(folds):
        fold_hits, fold_random = [], []
        if not fold:
            continue
        fixed_train_end = fold[0]
        fixed_history = draws[:fixed_train_end]
        for target_index in fold:
            target = draws[target_index]
            fold_hits.append(_hits(fixed_history, target, candidate, universe_size))
            fold_random.append(_random_mean_hits(
                target, random_repeats,
                seed + 13007 + fold_index * 1000003 + target_index * 1009,
                universe_size
            ))
        fold_reports.append({
            "fold": fold_index + 1,
            "eligible_target_draws": len(fold_hits),
            "mean_candidate_hits_per_target": mean(fold_hits),
            "mean_random_hits_per_target": mean(fold_random),
            "mean_candidate_minus_random": mean(
                [candidate_hit - baseline for candidate_hit, baseline in zip(fold_hits, fold_random)]
            ),
        })
        all_hits.extend(fold_hits)
        all_random.extend(fold_random)
    return {
        "summary": _summarize(all_hits, all_random, seed + 13007, bootstrap_replicates),
        "folds": fold_reports,
        "protocol_note": (
            "Five contiguous time-ordered folds; each fold uses a fixed training prefix "
            "ending before its first test target. Earlier outcomes inside a fold are not added."
        ),
    }

def _evaluate_candidate(draws, candidate, universe_size, random_repeats, bootstrap_replicates, seed):
    return {
        "forward_walk_forward": {
            **_evaluate_forward(draws, candidate, universe_size, random_repeats, seed + 11, bootstrap_replicates),
            "protocol_note": "Each target is scored using only preceding draws within the same regime.",
        },
        "reverse_retrospective_stress_test": {
            **_evaluate_reverse(draws, candidate, universe_size, random_repeats, seed + 23, bootstrap_replicates),
            "protocol_note": "Reverse order is a retrospective stress test, not forward predictive evidence.",
        },
        "time_ordered_cross_validation": _evaluate_cv(
            draws, candidate, universe_size, random_repeats, seed + 37, bootstrap_replicates
        ),
    }

def build_report(rows, random_repeats=DEFAULT_RANDOM_REPEATS,
                 bootstrap_replicates=DEFAULT_BOOTSTRAP_REPLICATES, seed=20261026):
    if random_repeats < 1 or bootstrap_replicates < 0:
        raise ValueError("Random repeats must be positive and bootstrap replicates non-negative.")
    archive_check = guard.validate_archive(rows)
    if archive_check.get("valid") is not True:
        raise ValueError("Data Guard did not validate the archive.")
    _validate_regime_ranges(rows)

    target_date = archive_check.get("last_date")
    if not isinstance(target_date, str):
        raise ValueError("Could not determine current isolated target date.")
    split = guard.isolate_target(rows, target_date)
    if not isinstance(split, dict):
        raise ValueError("Data Guard returned an invalid target split.")
    if split.get("isolation_pass") is not True:
        raise ValueError("Target isolation failed.")
    if split.get("target_present_in_archive") is not True:
        raise ValueError("The current isolated target is missing from the archive.")
    excluded = split.get("excluded_dates")
    train = split.get("train")
    if not isinstance(excluded, list) or excluded != [target_date]:
        raise ValueError("Expected exactly the latest draw to be excluded.")
    if not isinstance(train, list) or not train:
        raise ValueError("There is no pre-target training history.")
    if any(row["date"] >= target_date for row in train):
        raise ValueError("Target/future date leaked into training.")
    if train[-1]["date"] >= target_date:
        raise ValueError("Training chronology is invalid.")

    # Do not carry the target row or its numbers beyond this point.
    regime_rows = {
        "R54": [row for row in train if date.fromisoformat(row["date"]) < R60_START],
        "R60": [row for row in train if date.fromisoformat(row["date"]) >= R60_START],
    }
    regime_reports = {}
    for regime, draws in regime_rows.items():
        universe_size = REGIMES[regime]["universe_size"]
        number_draws = [tuple(row["numbers"]) for row in draws]
        if len(number_draws) < MIN_HISTORY + 1:
            regime_reports[regime] = {
                "status": "INSUFFICIENT_REGIME_HISTORY",
                "pre_target_draw_count": len(number_draws),
                "universe_size": universe_size,
                "candidate_results": {},
            }
            continue
        candidate_results = {}
        # Use identical per-target random baseline draws across candidates so
        # candidate-to-candidate comparisons are not confounded by baseline RNG.
        shared_regime_seed = seed + (54 if regime == "R54" else 60) * 10000019
        for candidate in CANDIDATES:
            candidate_results[candidate["candidate_id"]] = _evaluate_candidate(
                number_draws,
                candidate,
                universe_size,
                random_repeats,
                bootstrap_replicates,
                shared_regime_seed,
            )
        regime_reports[regime] = {
            "status": "EXPLORATORY_EVALUATION_COMPLETE",
            "pre_target_draw_count": len(number_draws),
            "universe_size": universe_size,
            "theoretical_expected_hits_per_random_six_number_draw": 36.0 / universe_size,
            "candidate_results": candidate_results,
        }

    return {
        "schema_version": 1,
        "project": "GENERAL Super Loto",
        "report_type": "offline_new_method_family_discovery",
        "runner_version": VERSION,
        "archive_integrity": {
            "valid": True,
            "record_count": archive_check["record_count"],
            "first_date": archive_check["first_date"],
            "last_date": archive_check["last_date"],
            "pre_target_draw_count": len(train),
            "pre_target_last_date": train[-1]["date"],
            "draw_numbers_in_report": False,
        },
        "current_isolated_target": {
            "date": target_date,
            "present_in_archive": True,
            "isolation_pass": True,
            "target_numbers_used_by_discovery_or_evaluation": False,
            "post_target_records_used": False,
        },
        "research_design": {
            "api_calls": 0,
            "live_ai_calls": False,
            "provider_or_paid_service_required": False,
            "predeclared_candidate_count": len(CANDIDATES),
            "candidate_families": sorted({candidate["family"] for candidate in CANDIDATES}),
            "r54_and_r60_pooled": False,
            "current_isolated_target_used_for_tuning": False,
            "canonical_registry_modified": False,
            "automatic_promotion": False,
            "multiple_testing_correction": "NOT_APPLIED; candidates are exploratory and results require independent follow-up",
            "uncertainty": "95% circular moving-block bootstrap intervals are approximate and do not prove predictive signal",
            "confidence_interval_policy": "No bootstrap interval is reported with fewer than max(40, 2 * block_length) eligible targets.",
            "random_baseline_policy": "Identical random baseline seeds are used across candidates within each regime/protocol for fairer paired comparison.",
        },
        "candidate_definitions": list(CANDIDATES),
        "regimes": regime_reports,
        "conclusion": (
            "This run tests a bounded, predeclared offline search space for new method families. "
            "It does not use the current isolated target, call an AI provider, change binding rules, "
            "promote methods, or claim a winning-number prediction. Results are exploratory; "
            "multiple testing and lottery randomness limit interpretation."
        ),
    }

def print_safe_summary(report):
    target = report["current_isolated_target"]
    print("OFFLINE HYPOTHESIS DISCOVERY: SUCCESS")
    print("Isolated target:", target["date"])
    print("Target isolation:", "PASS" if target["isolation_pass"] else "FAIL")
    print("Target numbers used: NO")
    print("Archive records:", report["archive_integrity"]["record_count"])
    print("Pre-target records:", report["archive_integrity"]["pre_target_draw_count"])
    print("Candidate families:", ", ".join(report["research_design"]["candidate_families"]))
    print("Candidates tested:", report["research_design"]["predeclared_candidate_count"])
    for regime, data in report["regimes"].items():
        print(regime + ":", data["status"], "| pre-target draws:", data["pre_target_draw_count"])
        for candidate_id, results in data["candidate_results"].items():
            forward = results["forward_walk_forward"]
            reverse = results["reverse_retrospective_stress_test"]
            cv = results["time_ordered_cross_validation"]["summary"]
            print(
                "  " + candidate_id,
                "| forward delta=", _fmt(forward["mean_candidate_minus_random"]),
                "| reverse delta=", _fmt(reverse["mean_candidate_minus_random"]),
                "| CV delta=", _fmt(cv["mean_candidate_minus_random"]),
            )
    print("Current target numbers or per-draw predictions displayed: NO")
    print("Canonical registry modified: NO")
    print("AI/provider API calls: 0")

def _fmt(value):
    return "unavailable" if value is None else f"{value:.4f}"

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", default="super_loto_archive.json")
    parser.add_argument("--output", default="formula-discovery-output/report.json")
    parser.add_argument("--random-repeats", type=int, default=DEFAULT_RANDOM_REPEATS)
    parser.add_argument("--bootstrap-replicates", type=int, default=DEFAULT_BOOTSTRAP_REPLICATES)
    parser.add_argument("--seed", type=int, default=20261026)
    args = parser.parse_args()
    try:
        rows = _load_rows(args.archive)
        report = build_report(
            rows,
            random_repeats=args.random_repeats,
            bootstrap_replicates=args.bootstrap_replicates,
            seed=args.seed,
        )
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print_safe_summary(report)
        print("AGGREGATE REPORT SAVED:", str(output))
    except Exception as error:
        print("OFFLINE HYPOTHESIS DISCOVERY FAILED:", type(error).__name__, str(error)[:300])
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
