"""Experimental, pure-Python mathematical proposals for legacy methods.

This module is deliberately not imported by general_core.py. Its proposal
algorithms are for deterministic synthetic tests only, not production use.
The authoritative method registry remains OPEN_DEFINITION until the user
explicitly approves a canonical definition.
"""
from collections import Counter
from datetime import date
from statistics import median

NUMBER_MIN = 1
NUMBER_MAX = 60
DRAW_SIZE = 6
ABC_K = 5
MOD5_K = 5
A_TO_T_TOP_TARGETS = 10
A_TO_T_MIN_HISTORY_DRAWS = 20
MOD5_MIN_HISTORY_DRAWS = 6

FEATURE_ORDER = (
    "posdiff",
    "gapdiff",
    "parityflip",
    "bandmove",
    "mod3diff",
    "mod5diff",
    "mod7diff",
    "sumdiff",
    "span_diff",
    "shape_diff",
    "signed_core",
)


def _parse_iso_date(value, label):
    if not isinstance(value, str):
        raise ValueError(label + " must be an ISO YYYY-MM-DD string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError(label + " must be an ISO YYYY-MM-DD string") from None
    if parsed.isoformat() != value:
        raise ValueError(label + " must use canonical YYYY-MM-DD format")
    return parsed


def _normalize_draw(values):
    if not isinstance(values, (list, tuple)) or len(values) != DRAW_SIZE:
        raise ValueError("Each historical draw must contain exactly six numbers")
    normalized = []
    for number in values:
        if isinstance(number, bool) or not isinstance(number, int):
            raise ValueError("Draw numbers must be integers, not booleans")
        if not NUMBER_MIN <= number <= NUMBER_MAX:
            raise ValueError("Draw numbers must be between 1 and 60")
        normalized.append(number)
    if len(set(normalized)) != DRAW_SIZE:
        raise ValueError("Draw numbers must be distinct")
    return tuple(sorted(normalized))


def _history_before_target(rows, target_date):
    """Read only number sets dated strictly before target_date.

    Dates are checked for canonical format, uniqueness and chronological order.
    Once a row is on/after the isolated target date, its numbers field is never
    accessed. Archive integrity must separately be checked by Data Guard first.
    """
    cutoff = _parse_iso_date(target_date, "target_date")
    if not isinstance(rows, list):
        raise ValueError("archive rows must be a list")

    history = []
    previous_day = None
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each archive row must be an object")
        draw_day = _parse_iso_date(row.get("date"), "draw date")
        if previous_day is not None and draw_day <= previous_day:
            raise ValueError("archive dates must be unique and strictly chronological")
        previous_day = draw_day

        # IMPORTANT: do not even read/validate numbers for target or later rows.
        if draw_day >= cutoff:
            continue

        history.append(_normalize_draw(row.get("numbers")))

    return tuple(history)


def _features(a, b):
    """Return the 11 non-duplicate V8.6 transition features for A -> B."""
    gap_a = tuple(a[i + 1] - a[i] for i in range(5))
    gap_b = tuple(b[i + 1] - b[i] for i in range(5))
    parity_a = tuple(x % 2 for x in a)
    parity_b = tuple(x % 2 for x in b)
    band_a = tuple((x - 1) // 10 for x in a)
    band_b = tuple((x - 1) // 10 for x in b)
    shape_a = tuple(1 if x >= median(gap_a) else -1 for x in gap_a)
    shape_b = tuple(1 if x >= median(gap_b) else -1 for x in gap_b)

    values = {
        "posdiff": tuple(b[i] - a[i] for i in range(6)),
        "gapdiff": tuple(gap_b[i] - gap_a[i] for i in range(5)),
        "parityflip": tuple(parity_b[i] - parity_a[i] for i in range(6)),
        "bandmove": tuple(band_b[i] - band_a[i] for i in range(6)),
        "mod3diff": tuple((b[i] % 3) - (a[i] % 3) for i in range(6)),
        "mod5diff": tuple((b[i] % 5) - (a[i] % 5) for i in range(6)),
        "mod7diff": tuple((b[i] % 7) - (a[i] % 7) for i in range(6)),
        "sumdiff": (sum(b) - sum(a),),
        "span_diff": ((b[-1] - b[0]) - (a[-1] - a[0]),),
        "shape_diff": tuple(shape_b[i] - shape_a[i] for i in range(5)),
        "signed_core": tuple(
            (b[i] - a[i]) + (gap_b[i - 1] - gap_a[i - 1] if i > 0 else 0)
            for i in range(6)
        ),
    }
    return tuple(component for name in FEATURE_ORDER for component in values[name])


def _sign(value):
    return 1 if value > 0 else (-1 if value < 0 else 0)


def _result(method, scores, diagnostics, reason=None):
    normalized_scores = {
        number: float(scores.get(number, 0.0))
        for number in range(NUMBER_MIN, NUMBER_MAX + 1)
    }
    ranked = sorted(
        ((number, score) for number, score in normalized_scores.items() if score > 0),
        key=lambda item: (-item[1], item[0]),
    )
    return {
        "method": method,
        "status": "scored" if ranked else "no_signal",
        "reason": reason,
        "scores": normalized_scores,
        "ranked_numbers": [number for number, _ in ranked],
        "ranked_scores": [{"number": number, "score": score} for number, score in ranked],
        "diagnostics": {
            **diagnostics,
            "target_draw_used": False,
            "post_target_draws_used": False,
        },
    }


def score_abc_transition(rows, target_date, k=ABC_K):
    """Proposed A->B->C method using the last observed A->B transition.

    For each historical triple (A_i,B_i,C_i), sign-transform the concatenated
    V8.6 transition features of A_i->B_i and compare with the current A->B
    signature by Hamming distance. Keep at most k nearest historical triples,
    sort ties by most-recent historical start index first, and score every
    number in C_i with weight 1/rank. No fallback numbers are manufactured.
    """
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")
    history = _history_before_target(rows, target_date)
    diagnostics = {
        "history_draws": len(history),
        "training_triples": max(0, len(history) - 2),
        "neighbors_used": 0,
        "k": k,
        "signature": "sign of 11 concatenated non-duplicate V8.6 transition features",
        "distance": "Hamming distance",
        "tie_break": "more recent historical triple first, then ascending number",
    }
    if len(history) < 3:
        return _result("A->B->C", {}, diagnostics, "at least 3 pre-target draws required")

    current_signature = tuple(_sign(x) for x in _features(history[-2], history[-1]))
    examples = []
    for i in range(len(history) - 2):
        signature = tuple(_sign(x) for x in _features(history[i], history[i + 1]))
        distance = sum(left != right for left, right in zip(current_signature, signature))
        examples.append((distance, -i, history[i + 2]))

    examples.sort(key=lambda item: (item[0], item[1]))
    neighbors = examples[:min(k, len(examples))]
    scores = Counter()
    for rank, (_, _, next_draw) in enumerate(neighbors, start=1):
        weight = 1.0 / rank
        for number in next_draw:
            scores[number] += weight

    diagnostics["neighbors_used"] = len(neighbors)
    diagnostics["minimum_distance"] = neighbors[0][0] if neighbors else None
    return _result("A->B->C", scores, diagnostics)


def score_a_to_t(rows, target_date):
    """Proposed A->T source-number co-occurrence model.

    Count M[x,y] over every adjacent pair within pre-target history:
    M[x,y] = number of i where x is in D_i and y is in D_(i+1).
    For each x in the latest pre-target source draw A, keep its ten observed
    targets with highest M[x,y] (ties: lower y first); add M[x,y] to each such
    target's score. This preserves the legacy generator's top-10 rule.
    """
    history = _history_before_target(rows, target_date)
    diagnostics = {
        "history_draws": len(history),
        "training_transitions": max(0, len(history) - 1),
        "current_source": "last pre-target draw",
        "top_targets_per_source_number": A_TO_T_TOP_TARGETS,
        "minimum_history_draws": A_TO_T_MIN_HISTORY_DRAWS,
        "tie_break": "higher count first, then ascending number",
    }
    if len(history) < A_TO_T_MIN_HISTORY_DRAWS:
        return _result("A->T", {}, diagnostics, "minimum 20 pre-target draws required")

    transitions = [Counter() for _ in range(NUMBER_MAX + 1)]
    for source_draw, next_draw in zip(history, history[1:]):
        for source_number in source_draw:
            transitions[source_number].update(next_draw)

    scores = Counter()
    for source_number in history[-1]:
        observed_targets = sorted(
            transitions[source_number].items(),
            key=lambda item: (-item[1], item[0]),
        )[:A_TO_T_TOP_TARGETS]
        for target_number, count in observed_targets:
            scores[target_number] += count

    diagnostics["source_numbers_with_observed_targets"] = sum(
        1 for number in history[-1] if transitions[number]
    )
    return _result("A->T", scores, diagnostics)


def _mod5_profile(draw):
    """Count the six source-draw numbers in residue classes 0,1,2,3,4."""
    return tuple(sum(1 for number in draw if number % 5 == residue) for residue in range(5))


def score_mod5_to_t(rows, target_date, k=MOD5_K):
    """Proposed MOD5-profile -> T nearest-neighbor candidate.

    Define MOD5(D) as the five-count vector of residues n % 5 for D.
    For each fully historical transition D_i -> D_(i+1), compare MOD5(D_i)
    with MOD5 of the latest pre-target source draw using L1 distance. Select
    up to k nearest historical transitions (ties: most recent source first)
    and award one vote to every number in each selected next draw.
    This is a new, explicitly provisional operational definition: it is not
    equivalent to the V8.6 mod5diff transition feature.
    """
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")
    history = _history_before_target(rows, target_date)
    diagnostics = {
        "history_draws": len(history),
        "training_transitions": max(0, len(history) - 1),
        "neighbors_used": 0,
        "k": k,
        "mod5_definition": "five counts of number residues n % 5 in the source draw",
        "distance": "L1 distance between five-count residue profiles",
        "tie_break": "more recent historical source first, then ascending number",
        "minimum_history_draws": MOD5_MIN_HISTORY_DRAWS,
    }
    if len(history) < MOD5_MIN_HISTORY_DRAWS:
        return _result("MOD5->T", {}, diagnostics, "at least 6 pre-target draws required")

    current_profile = _mod5_profile(history[-1])
    examples = []
    for i, (source_draw, next_draw) in enumerate(zip(history, history[1:])):
        profile = _mod5_profile(source_draw)
        distance = sum(abs(x - y) for x, y in zip(current_profile, profile))
        examples.append((distance, -i, next_draw))

    examples.sort(key=lambda item: (item[0], item[1]))
    neighbors = examples[:min(k, len(examples))]
    scores = Counter()
    for _, _, next_draw in neighbors:
        scores.update(next_draw)

    diagnostics["neighbors_used"] = len(neighbors)
    diagnostics["minimum_distance"] = neighbors[0][0] if neighbors else None
    return _result("MOD5->T", scores, diagnostics)
