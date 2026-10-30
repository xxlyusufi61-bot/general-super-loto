"""Synthetic-only prototypes for proposed GENERAL method definitions.

These functions are isolated from the canonical method registry and contain no
archive file I/O, network access, model calls, or target-draw access. They
implement PROPOSAL ONLY working semantics. Do not use them on the real archive
until the user explicitly approves the exact semantics and equations.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import general_data_guard as guard

R54_TO_R60_CUTOFF = "2020-08-02"
BETA_ALPHA = 1
BETA_BETA = 1


def _validated_history(rows: list[dict[str, Any]]) -> tuple[str, int]:
    if not isinstance(rows, list) or not rows:
        raise ValueError("Pre-target history must be a non-empty list.")

    try:
        checked = guard.validate_archive(rows)
    except Exception as exc:
        raise ValueError("Synthetic history failed Data Guard validation.") from exc

    if checked.get("valid") is not True:
        raise ValueError("Synthetic history failed Data Guard validation.")

    dates = [row["date"] for row in rows]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError("History must be strictly chronological with unique dates.")

    for row in rows:
        numbers = guard.validate_numbers(row["numbers"])
        if numbers != sorted(numbers):
            raise ValueError("Each draw must list its six numbers in ascending order.")

    old_regime = [day < R54_TO_R60_CUTOFF for day in dates]
    if any(old_regime) and not all(old_regime):
        raise ValueError("History crosses the R54/R60 boundary; transitions are blocked.")

    regime = "R54" if all(old_regime) else "R60"
    universe_size = 54 if regime == "R54" else 60

    if regime == "R54" and any(
        number > 54 for row in rows for number in row["numbers"]
    ):
        raise ValueError("R54 history contains a number above 54.")

    return regime, universe_size


def _rank(scores: dict[int, float], regime: str) -> dict[str, Any]:
    return {
        "status": "PROPOSAL_ONLY",
        "execution_ready": False,
        "real_archive_evaluation_performed": False,
        "regime": regime,
        "ranking": [
            {"number": number, "score": scores[number]}
            for number in sorted(scores, key=lambda value: (-scores[value], value))
        ],
    }


def _probability(successes: int, exposures: int) -> float:
    return (successes + BETA_ALPHA) / (
        exposures + BETA_ALPHA + BETA_BETA
    )


def score_a_to_t(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """PROPOSAL: one-step P(next-draw T | antecedent number A)."""
    regime, universe_size = _validated_history(rows)
    if len(rows) < 2:
        raise ValueError("A->T requires at least two pre-target draws.")

    exposures: dict[int, int] = defaultdict(int)
    successes: dict[tuple[int, int], int] = defaultdict(int)

    for source, successor in zip(rows, rows[1:]):
        successor_set = set(successor["numbers"])
        for antecedent in source["numbers"]:
            exposures[antecedent] += 1
            for target in successor_set:
                successes[(antecedent, target)] += 1

    current_a = rows[-1]["numbers"]
    scores = {
        target: sum(
            _probability(
                successes[(antecedent, target)],
                exposures[antecedent],
            )
            for antecedent in current_a
        ) / len(current_a)
        for target in range(1, universe_size + 1)
    }

    result = _rank(scores, regime)
    result.update({
        "method": "A->T",
        "parameters": {"beta_alpha": BETA_ALPHA, "beta_beta": BETA_BETA},
        "support": {
            "current_antecedent_count": len(current_a),
            "current_antecedents_with_zero_exposure": sum(
                exposures[a] == 0 for a in current_a
            ),
            "exposure_count_by_antecedent": {
                str(a): exposures[a] for a in current_a
            },
        },
    })
    return result


def score_mod5_to_t(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """PROPOSAL: one-step P(next-draw T | source-draw residue MOD5)."""
    regime, universe_size = _validated_history(rows)
    if len(rows) < 2:
        raise ValueError("MOD5->T requires at least two pre-target draws.")

    exposures: dict[int, int] = defaultdict(int)
    successes: dict[tuple[int, int], int] = defaultdict(int)

    for source, successor in zip(rows, rows[1:]):
        source_residues = {number % 5 for number in source["numbers"]}
        successor_set = set(successor["numbers"])
        for residue in source_residues:
            exposures[residue] += 1
            for target in successor_set:
                successes[(residue, target)] += 1

    current_residues = sorted({number % 5 for number in rows[-1]["numbers"]})
    scores = {
        target: sum(
            _probability(
                successes[(residue, target)],
                exposures[residue],
            )
            for residue in current_residues
        ) / len(current_residues)
        for target in range(1, universe_size + 1)
    }

    result = _rank(scores, regime)
    result.update({
        "method": "MOD5->T",
        "parameters": {"beta_alpha": BETA_ALPHA, "beta_beta": BETA_BETA},
        "support": {
            "active_residues": current_residues,
            "exposure_count_by_residue": {
                str(residue): exposures[residue] for residue in range(5)
            },
        },
    })
    return result


def score_a_b_c(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """PROPOSAL: two-step P(C | antecedent A, intermediate B)."""
    regime, universe_size = _validated_history(rows)
    if len(rows) < 3:
        raise ValueError("A->B->C requires at least three pre-target draws.")

    exposures: dict[tuple[int, int], int] = defaultdict(int)
    successes: dict[tuple[int, int, int], int] = defaultdict(int)

    for source, middle, successor in zip(rows, rows[1:], rows[2:]):
        successor_set = set(successor["numbers"])
        for antecedent in source["numbers"]:
            for intermediate in middle["numbers"]:
                pair = (antecedent, intermediate)
                exposures[pair] += 1
                for target in successor_set:
                    successes[(antecedent, intermediate, target)] += 1

    current_a = rows[-2]["numbers"]
    current_b = rows[-1]["numbers"]
    contexts = [
        (antecedent, intermediate)
        for antecedent in current_a
        for intermediate in current_b
    ]
    scores = {
        target: sum(
            _probability(
                successes[(antecedent, intermediate, target)],
                exposures[(antecedent, intermediate)],
            )
            for antecedent, intermediate in contexts
        ) / len(contexts)
        for target in range(1, universe_size + 1)
    }

    result = _rank(scores, regime)
    result.update({
        "method": "A->B->C",
        "parameters": {"beta_alpha": BETA_ALPHA, "beta_beta": BETA_BETA},
        "support": {
            "current_context_count": len(contexts),
            "current_contexts_with_history": sum(
                exposures[pair] > 0 for pair in contexts
            ),
            "current_contexts_without_history": sum(
                exposures[pair] == 0 for pair in contexts
            ),
        },
    })
    return result
