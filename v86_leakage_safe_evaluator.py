"""Leakage-safe audit evaluator derived from the legacy V8.6 feature vocabulary.

This module deliberately does not fetch or load the real lottery archive.
The legacy file is preserved unchanged. Only synthetic fixtures are used in tests.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from statistics import mean
from typing import Iterable, Sequence

FEATURES = (
    "posdiff", "gapdiff", "rawdiff", "parityflip", "bandmove",
    "mod3diff", "mod5diff", "mod7diff", "sumdiff", "span_diff",
    "shape_diff", "signed_core",
)
TRANSFORMS = ("exact", "sign", "absolute")


@dataclass(frozen=True)
class Draw:
    day: date
    numbers: tuple[int, ...]


def validate_history(rows: Iterable[Draw], target_date: date, locked_dates=()) -> tuple[Draw, ...]:
    """Validate chronology and return only rows strictly before target, excluding locked dates."""
    rows = tuple(rows)
    locked = set(locked_dates)
    previous = None
    seen_dates = set()
    clean = []
    for row in rows:
        if not isinstance(row, Draw):
            raise ValueError("Every archive row must be a Draw.")
        if row.day in seen_dates:
            raise ValueError("Duplicate draw date.")
        seen_dates.add(row.day)
        if previous is not None and row.day <= previous:
            raise ValueError("Archive must be strictly chronological.")
        previous = row.day
        if len(row.numbers) != 6 or len(set(row.numbers)) != 6:
            raise ValueError("Each draw must contain six unique numbers.")
        if any(not isinstance(n, int) or isinstance(n, bool) or n < 1 or n > 60 for n in row.numbers):
            raise ValueError("Draw number outside 1..60.")
        if tuple(sorted(row.numbers)) != row.numbers:
            raise ValueError("Draw numbers must be sorted.")
        if row.day < target_date and row.day not in locked:
            clean.append(row)
    return tuple(clean)


def _gaps(x):
    return tuple(x[i + 1] - x[i] for i in range(5))


def _shape(x):
    g = _gaps(x)
    middle = sorted(g)[len(g) // 2]
    return tuple(1 if v >= middle else -1 for v in g)


def state_features(a, b):
    ga, gb = _gaps(a), _gaps(b)
    pa, pb = tuple(v & 1 for v in a), tuple(v & 1 for v in b)
    ba, bb = tuple((v - 1) // 10 for v in a), tuple((v - 1) // 10 for v in b)
    sa, sb = _shape(a), _shape(b)
    return {
        "posdiff": tuple(b[i] - a[i] for i in range(6)),
        "gapdiff": tuple(gb[i] - ga[i] for i in range(5)),
        "rawdiff": tuple(b[i] - a[i] for i in range(6)),
        "parityflip": tuple(pb[i] - pa[i] for i in range(6)),
        "bandmove": tuple(bb[i] - ba[i] for i in range(6)),
        "mod3diff": tuple((b[i] % 3) - (a[i] % 3) for i in range(6)),
        "mod5diff": tuple((b[i] % 5) - (a[i] % 5) for i in range(6)),
        "mod7diff": tuple((b[i] % 7) - (a[i] % 7) for i in range(6)),
        "sumdiff": (sum(b) - sum(a),),
        "span_diff": ((b[-1] - b[0]) - (a[-1] - a[0]),),
        "shape_diff": tuple(sb[i] - sa[i] for i in range(5)),
        "signed_core": tuple(
            (b[i] - a[i]) + (gb[i - 1] - ga[i - 1] if i > 0 else 0)
            for i in range(6)
        ),
    }


def transform(values, mode):
    if mode == "exact":
        return tuple(values)
    if mode == "sign":
        return tuple(0 if x == 0 else (1 if x > 0 else -1) for x in values)
    if mode == "absolute":
        return tuple(abs(x) for x in values)
    raise ValueError("Unknown transform mode.")


def distance(a, b, mode):
    if len(a) != len(b):
        return 10**9
    if mode == "exact":
        return 0 if a == b else 10**9
    if mode == "sign":
        return sum(x != y for x, y in zip(a, b))
    if mode == "absolute":
        return sum(abs(x - y) for x, y in zip(a, b))
    raise ValueError("Unknown transform mode.")


def predict_from_known_pair(history: Sequence[Draw], feature="mod5diff", mode="sign", k=5):
    """Predict the draw after the last known pair using strictly older A->B->C triples.

    Query features use the two latest known draws A,B. Candidate triples must finish
    before A, so their next draw C is already historical and cannot overlap query A,B.
    No target draw is accepted by this function.
    """
    if feature not in FEATURES or mode not in TRANSFORMS:
        raise ValueError("Unsupported feature or transform.")
    if k < 1:
        raise ValueError("k must be positive.")
    if len(history) < 5:
        return None

    a, b = history[-2].numbers, history[-1].numbers
    query_sig = transform(state_features(a, b)[feature], mode)
    a_index = len(history) - 2
    candidates = []
    # j+2 < a_index means candidate A->B->C ends strictly before query A.
    for j in range(0, a_index - 2):
        x, y, z = history[j].numbers, history[j + 1].numbers, history[j + 2].numbers
        sig = transform(state_features(x, y)[feature], mode)
        d = distance(query_sig, sig, mode)
        candidates.append((d, j, z))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    votes = Counter()
    for d, _, next_draw in candidates[:k]:
        weight = 1.0 / (1.0 + d)
        for number in next_draw:
            votes[number] += weight
    # Stable, explicit tie-break; never rely on Counter insertion order.
    return tuple(sorted((n for n, _ in sorted(votes.items(), key=lambda p: (-p[1], p[0]))[:6])))


def hits(prediction, actual):
    return 0 if prediction is None else len(set(prediction) & set(actual))


def walk_forward(rows: Sequence[Draw], feature, mode, k, start_index, end_index):
    """Score target rows [start_index,end_index); target row is never passed to predictor."""
    scores = []
    for target_index in range(max(start_index, 2), min(end_index, len(rows))):
        history = rows[:target_index]
        pred = predict_from_known_pair(history, feature, mode, k)
        scores.append(hits(pred, rows[target_index].numbers))
    return mean(scores) if scores else 0.0, tuple(scores)


def evaluate_frozen_candidate(rows, feature, mode, k, final_start, final_end):
    """Evaluate a pre-frozen candidate on final only; selection must happen elsewhere."""
    score, values = walk_forward(rows, feature, mode, k, final_start, final_end)
    return {"feature": feature, "mode": mode, "k": k, "final_mean_hits": score,
            "n_targets": len(values), "per_target_hits": values}


def select_on_validation(rows, candidates, validation_start, validation_end):
    """Select by validation only; this function never reads a final/test interval."""
    scored = []
    for feature, mode, k in candidates:
        val, values = walk_forward(rows, feature, mode, k, validation_start, validation_end)
        scored.append((val, feature, mode, k, len(values)))
    scored.sort(key=lambda x: (-x[0], x[1], x[2], x[3]))
    return scored


def reverse_stress_test(rows, feature, mode, k, start_index, end_index):
    """Retrospective reverse-order stress test only; not predictive evidence."""
    reversed_rows = tuple(reversed(rows))
    score, values = walk_forward(reversed_rows, feature, mode, k, start_index, end_index)
    return {"protocol": "REVERSE_RETROSPECTIVE_STRESS_TEST_NOT_PREDICTIVE",
            "mean_hits": score, "n_targets": len(values)}


def fold_summary(rows, feature, mode, k, folds=4, start_index=2, end_index=None):
    """Summarize forward walk-forward scores over contiguous chronological folds."""
    end_index = len(rows) if end_index is None else min(end_index, len(rows))
    start_index = max(2, start_index)
    if folds < 1 or end_index <= start_index:
        raise ValueError("Invalid fold range.")
    edges = [start_index + (end_index - start_index) * i // folds for i in range(folds + 1)]
    result = []
    for i in range(folds):
        score, values = walk_forward(rows, feature, mode, k, edges[i], edges[i + 1])
        result.append({"fold": i + 1, "start_index": edges[i], "end_index": edges[i + 1],
                       "mean_hits": score, "n_targets": len(values)})
    return result
