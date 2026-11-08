"""Typed, direction-sealed state-transition DSL v2 for GENERAL.

DSL v2 is a separate evaluator. DSL v1 remains unchanged and backward compatible.
The evaluator binds exactly two adjacent archive rows to the permitted slots:
forward uses A/B while C is hidden; reverse uses B/C while A is hidden.
Reverse results are retrospective diagnostics only.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import date, timedelta
from typing import Any

VERSION = "1.0.0"
SCHEMA_VERSION = 2
ALLOWED_POOL_SIZES = {60}
RESEARCH_SCOPE_START = date(2020, 8, 2)
MAX_AST_NODES = 64
MAX_AST_DEPTH = 12
MAX_ABS_LITERAL = 100_000
MAX_ABS_SCORE = 1_000_000_000
MAX_INPUT_HISTORY_DRAWS = 10_000
MAX_VALIDATION_DRAWS = 30
DEFAULT_MIN_TRAIN_DRAWS = 40
NUMERIC_BINARY_OPS = {"add", "sub", "mul", "div", "min", "max"}
COMPARISON_OPS = {"eq", "ne", "lt", "lte", "gt", "gte"}
BOOLEAN_BINARY_OPS = {"and", "or"}
SET_BINARY_OPS = {
    "set_union",
    "set_intersection",
    "set_difference",
    "set_symmetric_difference",
}
FORWARD_SLOTS = {"A", "B"}
REVERSE_SLOTS = {"B", "C"}


class DSLV2Error(ValueError):
    """Raised when a DSL v2 specification or context is unsafe or invalid."""


def _fail(message: str) -> None:
    raise DSLV2Error("Safe hypothesis DSL v2 rejected: " + message)


def _canonical_date(value: Any, field: str) -> date:
    if not isinstance(value, str):
        _fail(field + " must be an ISO date string.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail(field + " is not a valid ISO date.")
    if parsed.isoformat() != value:
        _fail(field + " must use YYYY-MM-DD.")
    return parsed


def _validate_node(
    node: Any,
    *,
    direction: str,
    depth: int,
    state: dict[str, int],
) -> str:
    if depth > MAX_AST_DEPTH:
        _fail("expression depth exceeds the configured limit.")
    if not isinstance(node, dict) or not isinstance(node.get("op"), str):
        _fail("every expression node must be an object with an operator.")
    state["nodes"] += 1
    if state["nodes"] > MAX_AST_NODES:
        _fail("expression contains too many nodes.")
    op = node["op"]

    if op == "const":
        if set(node) != {"op", "value"}:
            _fail("const node keys are invalid.")
        value = node["value"]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail("numeric constants must be finite and within the safe bound.")
        if isinstance(value, int):
            if abs(value) > MAX_ABS_LITERAL:
                _fail("numeric constants must be finite and within the safe bound.")
        elif not math.isfinite(value) or abs(value) > MAX_ABS_LITERAL:
            _fail("numeric constants must be finite and within the safe bound.")
        return "number"

    if op == "var":
        if set(node) != {"op", "name"} or node.get("name") != "candidate":
            _fail("DSL v2 exposes only the candidate numeric variable.")
        return "number"

    if op == "state_ref":
        if set(node) != {"op", "slot"}:
            _fail("state_ref node keys are invalid.")
        allowed = FORWARD_SLOTS if direction == "forward" else REVERSE_SLOTS
        if node.get("slot") not in allowed:
            _fail("state_ref attempted to read a hidden or direction-forbidden slot.")
        return "draw_state"

    if op in SET_BINARY_OPS:
        if set(node) != {"op", "left", "right"}:
            _fail("set-operation node keys are invalid.")
        left = _validate_node(node["left"], direction=direction, depth=depth + 1, state=state)
        right = _validate_node(node["right"], direction=direction, depth=depth + 1, state=state)
        if left != "draw_state" or right != "draw_state":
            _fail("set operations require two DrawState operands.")
        return "draw_state"

    if op in {"contains_candidate", "cardinality"}:
        if set(node) != {"op", "state"}:
            _fail(op + " node keys are invalid.")
        child = _validate_node(node["state"], direction=direction, depth=depth + 1, state=state)
        if child != "draw_state":
            _fail(op + " requires a DrawState operand.")
        return "boolean" if op == "contains_candidate" else "number"

    if op in {"abs", "not", "bool_to_number"}:
        if set(node) != {"op", "arg"}:
            _fail("unary node keys are invalid.")
        child_type = _validate_node(node["arg"], direction=direction, depth=depth + 1, state=state)
        expected = "number" if op == "abs" else "boolean"
        if child_type != expected:
            _fail(op + " received an expression of the wrong type.")
        return "boolean" if op == "not" else "number"

    if op in NUMERIC_BINARY_OPS | COMPARISON_OPS | BOOLEAN_BINARY_OPS:
        if set(node) != {"op", "left", "right"}:
            _fail("binary node keys are invalid.")
        left_type = _validate_node(node["left"], direction=direction, depth=depth + 1, state=state)
        right_type = _validate_node(node["right"], direction=direction, depth=depth + 1, state=state)
        if op in NUMERIC_BINARY_OPS | COMPARISON_OPS:
            if left_type != "number" or right_type != "number":
                _fail(op + " requires numeric operands.")
            return "number" if op in NUMERIC_BINARY_OPS else "boolean"
        if left_type != "boolean" or right_type != "boolean":
            _fail(op + " requires boolean operands.")
        return "boolean"

    _fail("operator is not in the v2 allowlist: " + op)
    return "invalid"


def validate_execution_spec(value: Any) -> dict[str, Any]:
    """Validate a strict v2 expression and return a detached JSON-safe copy."""
    if not isinstance(value, dict) or set(value) != {"schema_version", "direction", "score_expression"}:
        _fail("execution specification keys are invalid.")
    if type(value.get("schema_version")) is not int or value["schema_version"] != SCHEMA_VERSION:
        _fail("unsupported execution specification version.")
    direction = value.get("direction")
    if direction not in {"forward", "reverse"}:
        _fail("direction must be forward or reverse.")
    state = {"nodes": 0}
    result_type = _validate_node(
        value.get("score_expression"),
        direction=direction,
        depth=1,
        state=state,
    )
    if result_type != "number":
        _fail("the final score expression must be numeric.")
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _validate_rows(rows: Any, *, field: str, maximum: int) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows or len(rows) > maximum:
        _fail(field + " must be a non-empty list within the configured size limit.")
    checked: list[dict[str, Any]] = []
    previous: date | None = None
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != {"date", "numbers"}:
            _fail(field + " row " + str(index) + " has an invalid shape.")
        current = _canonical_date(row.get("date"), field + " date")
        if current < RESEARCH_SCOPE_START:
            _fail(field + " contains a record before the active research scope.")
        if previous is not None and current <= previous:
            _fail(field + " must be strictly chronological with unique dates.")
        numbers = row.get("numbers")
        if (
            not isinstance(numbers, list)
            or len(numbers) != 6
            or any(type(number) is not int for number in numbers)
            or len(set(numbers)) != 6
            or any(number < 1 or number > 60 for number in numbers)
        ):
            _fail(field + " contains an invalid six-number draw for the active 6-of-60 regime.")
        checked.append({"date": current.isoformat(), "numbers": sorted(numbers)})
        previous = current
    return checked


def _checked_number(value: Any) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("an expression produced a non-numeric result.")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or abs(value) > MAX_ABS_SCORE:
        _fail("an expression produced a non-finite or out-of-range score.")
    return value


def _eval_node(
    node: dict[str, Any],
    *,
    candidate: int,
    slots: dict[str, frozenset[int]],
) -> bool | float | int | frozenset[int]:
    op = node["op"]
    if op == "const":
        return node["value"]
    if op == "var":
        return candidate
    if op == "state_ref":
        return slots[node["slot"]]
    if op in SET_BINARY_OPS:
        left = _eval_node(node["left"], candidate=candidate, slots=slots)
        right = _eval_node(node["right"], candidate=candidate, slots=slots)
        if op == "set_union":
            return frozenset(left.union(right))
        if op == "set_intersection":
            return frozenset(left.intersection(right))
        if op == "set_difference":
            return frozenset(left.difference(right))
        return frozenset(left.symmetric_difference(right))
    if op == "contains_candidate":
        state = _eval_node(node["state"], candidate=candidate, slots=slots)
        return candidate in state
    if op == "cardinality":
        state = _eval_node(node["state"], candidate=candidate, slots=slots)
        return len(state)
    if op == "abs":
        return _checked_number(abs(_eval_node(node["arg"], candidate=candidate, slots=slots)))
    if op == "not":
        return not _eval_node(node["arg"], candidate=candidate, slots=slots)
    if op == "bool_to_number":
        return 1 if _eval_node(node["arg"], candidate=candidate, slots=slots) else 0

    left = _eval_node(node["left"], candidate=candidate, slots=slots)
    if op == "and":
        return bool(left) and bool(_eval_node(node["right"], candidate=candidate, slots=slots))
    if op == "or":
        return bool(left) or bool(_eval_node(node["right"], candidate=candidate, slots=slots))
    right = _eval_node(node["right"], candidate=candidate, slots=slots)
    if op == "add":
        result = left + right
    elif op == "sub":
        result = left - right
    elif op == "mul":
        result = left * right
    elif op == "div":
        if right == 0:
            _fail("division by zero.")
        result = left / right
    elif op == "min":
        result = min(left, right)
    elif op == "max":
        result = max(left, right)
    elif op == "eq":
        return left == right
    elif op == "ne":
        return left != right
    elif op == "lt":
        return left < right
    elif op == "lte":
        return left <= right
    elif op == "gt":
        return left > right
    elif op == "gte":
        return left >= right
    else:
        _fail("operator reached an impossible execution path.")
    return _checked_number(result)


def rank_candidates_for_pair(
    pair: list[dict[str, Any]],
    *,
    target_date: str,
    number_pool_size: int,
    execution_spec: dict[str, Any],
    canonical_history: list[dict[str, Any]] | None = None,
    excluded_dates: set[str] | list[str] | tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """Rank the 6-of-60 pool using only the two permitted adjacent state rows.

    Forward maps the two immediately preceding canonical rows to A/B; reverse
    maps the first two later canonical rows to B/C. When canonical_history is
    supplied, adjacency and previously-consumed-date exclusions are enforced.
    The target row is never accepted, so target values cannot enter the expression.
    """
    if type(number_pool_size) is not int or number_pool_size not in ALLOWED_POOL_SIZES:
        _fail("only the 6-of-60 number pool is supported.")
    target = _canonical_date(target_date, "target_date")
    if target < RESEARCH_SCOPE_START:
        _fail("target date is before the active research scope.")
    spec = validate_execution_spec(execution_spec)
    checked_pair = _validate_rows(pair, field="state_pair", maximum=2)
    if len(checked_pair) != 2:
        _fail("exactly two adjacent state rows are required.")
    first = date.fromisoformat(checked_pair[0]["date"])
    second = date.fromisoformat(checked_pair[1]["date"])
    if spec["direction"] == "forward":
        if second >= target:
            _fail("forward state pair must be strictly earlier than hidden target C.")
    else:
        if target >= first:
            _fail("reverse state pair B/C must be strictly later than hidden target A.")

    if canonical_history is not None:
        checked_canonical = _validate_rows(
            canonical_history, field="canonical_history", maximum=MAX_INPUT_HISTORY_DRAWS
        )
        if spec["direction"] == "forward":
            if any(date.fromisoformat(row["date"]) >= target for row in checked_canonical):
                _fail("forward canonical context must contain only rows strictly before hidden target C.")
            expected_pair = checked_canonical[-2:]
        else:
            if any(date.fromisoformat(row["date"]) <= target for row in checked_canonical):
                _fail("reverse canonical context must contain only rows strictly after hidden target A.")
            expected_pair = checked_canonical[:2]
        if len(expected_pair) != 2 or expected_pair != checked_pair:
            _fail("state pair is not the two immediately adjacent canonical archive rows.")
        if not isinstance(excluded_dates, (set, list, tuple)):
            _fail("excluded_dates must be a set, list, or tuple.")
        if any(row["date"] in excluded_dates for row in checked_pair):
            _fail("state pair contains a previously consumed holdout row.")

    if spec["direction"] == "forward":
        slots = {
            "A": frozenset(checked_pair[0]["numbers"]),
            "B": frozenset(checked_pair[1]["numbers"]),
        }
    else:
        slots = {
            "B": frozenset(checked_pair[0]["numbers"]),
            "C": frozenset(checked_pair[1]["numbers"]),
        }
    ranking = []
    for candidate in range(1, number_pool_size + 1):
        score = _eval_node(spec["score_expression"], candidate=candidate, slots=slots)
        ranking.append({"number": candidate, "score": _checked_number(score)})
    ranking.sort(key=lambda item: (-item["score"], item["number"]))
    return ranking


def _mean_and_error(scores: list[int]) -> tuple[float, float | None]:
    mean_hits = statistics.fmean(scores)
    standard_error = statistics.stdev(scores) / math.sqrt(len(scores)) if len(scores) > 1 else None
    return mean_hits, standard_error


def evaluate_holdout(
    discovery_history: list[dict[str, Any]],
    validation_draws: list[dict[str, Any]],
    *,
    cutoff_date: str,
    number_pool_size: int,
    execution_spec: dict[str, Any],
    min_train_draws: int = DEFAULT_MIN_TRAIN_DRAWS,
    canonical_history: list[dict[str, Any]] | None = None,
    excluded_dates: set[str] | list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Forward walk-forward scoring; each target C is hidden from its A/B inputs."""
    if type(number_pool_size) is not int or number_pool_size not in ALLOWED_POOL_SIZES:
        _fail("only the 6-of-60 number pool is supported.")
    if type(min_train_draws) is not int or not 2 <= min_train_draws <= MAX_INPUT_HISTORY_DRAWS:
        _fail("minimum training draws are outside the supported range.")
    cutoff = _canonical_date(cutoff_date, "cutoff_date")
    if cutoff < RESEARCH_SCOPE_START:
        _fail("validation cutoff is before the active research scope.")
    discovery = _validate_rows(discovery_history, field="discovery_history", maximum=MAX_INPUT_HISTORY_DRAWS)
    validation = _validate_rows(validation_draws, field="validation_draws", maximum=MAX_VALIDATION_DRAWS)
    if date.fromisoformat(discovery[-1]["date"]) >= date.fromisoformat(validation[0]["date"]):
        _fail("discovery history must end before the validation window begins.")
    if date.fromisoformat(validation[-1]["date"]) >= cutoff:
        _fail("validation window contains the isolated target or a later date.")
    if len(discovery) < min_train_draws:
        _fail("discovery history is too short for the required initial training window.")
    spec = validate_execution_spec(execution_spec)
    if spec["direction"] != "forward":
        _fail("forward holdout requires a forward-direction specification.")

    scores: list[int] = []
    for index, target in enumerate(validation):
        training = discovery + validation[:index]
        if len(training) < min_train_draws or len(training) < 2:
            _fail("a validation fold does not have enough earlier training draws.")
        pair = training[-2:]
        canonical_before = None
        if canonical_history is not None:
            canonical_before = [
                row for row in canonical_history if row["date"] < target["date"]
            ]
        ranked = rank_candidates_for_pair(
            pair,
            target_date=target["date"],
            number_pool_size=number_pool_size,
            execution_spec=spec,
            canonical_history=canonical_before,
            excluded_dates=excluded_dates,
        )
        selected = {item["number"] for item in ranked[:6]}
        scores.append(len(selected.intersection(target["numbers"])))
    mean_hits, standard_error = _mean_and_error(scores)
    return {
        "engine_version": VERSION,
        "execution_dsl_version": SCHEMA_VERSION,
        "status": "EVALUATED",
        "protocol": "forward chronological walk-forward; target C hidden; only immediately preceding A/B states are exposed; no shuffling",
        "spec_sha256": hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest(),
        "number_pool_size": number_pool_size,
        "validation_draw_count": len(validation),
        "validation_start_date": validation[0]["date"],
        "validation_end_date": validation[-1]["date"],
        "minimum_training_draws": min_train_draws,
        "state_context_draw_count": 2,
        "mean_hits_out_of_6": round(mean_hits, 6),
        "standard_error_of_mean_hits": round(standard_error, 6) if standard_error is not None else None,
        "hit_distribution": {
            "0_hits": sum(score == 0 for score in scores),
            "1_hit": sum(score == 1 for score in scores),
            "2_hits": sum(score == 2 for score in scores),
            "3_or_more_hits": sum(score >= 3 for score in scores),
        },
        "uniform_random_expected_hits_out_of_6": round(36 / number_pool_size, 6),
        "current_isolated_target_included": False,
        "current_target_numbers_exposed": False,
        "per_draw_target_numbers_reported": False,
        "candidate_rankings_reported": False,
        "automatic_candidate_selection": False,
        "method_registry_modified": False,
    }


def evaluate_time_ordered_cv(
    discovery_history: list[dict[str, Any]],
    validation_draws: list[dict[str, Any]],
    *,
    cutoff_date: str,
    number_pool_size: int,
    execution_spec: dict[str, Any],
    folds: int = 5,
    min_train_draws: int = DEFAULT_MIN_TRAIN_DRAWS,
    canonical_history: list[dict[str, Any]] | None = None,
    excluded_dates: set[str] | list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Evaluate fixed v2 expression on contiguous time-ordered folds without shuffling."""
    if type(folds) is not int or folds < 2:
        _fail("cross-validation requires at least two folds.")
    checked_validation = _validate_rows(validation_draws, field="validation_draws", maximum=MAX_VALIDATION_DRAWS)
    if folds > len(checked_validation):
        _fail("cross-validation fold count exceeds the validation draw count.")
    base_size, extra = divmod(len(checked_validation), folds)
    reports = []
    start = 0
    for fold_index in range(folds):
        size = base_size + (1 if fold_index < extra else 0)
        block = checked_validation[start:start + size]
        fold_discovery = discovery_history + checked_validation[:start]
        report = evaluate_holdout(
            fold_discovery,
            block,
            cutoff_date=cutoff_date,
            number_pool_size=number_pool_size,
            execution_spec=execution_spec,
            min_train_draws=min_train_draws,
            canonical_history=canonical_history,
            excluded_dates=excluded_dates,
        )
        reports.append({
            "fold": fold_index + 1,
            "validation_start_date": block[0]["date"],
            "validation_end_date": block[-1]["date"],
            "validation_draw_count": report["validation_draw_count"],
            "mean_hits_out_of_6": report["mean_hits_out_of_6"],
            "standard_error_of_mean_hits": report["standard_error_of_mean_hits"],
        })
        start += size
    total = sum(item["validation_draw_count"] for item in reports)
    weighted = sum(item["mean_hits_out_of_6"] * item["validation_draw_count"] for item in reports)
    return {
        "engine_version": VERSION,
        "execution_dsl_version": SCHEMA_VERSION,
        "status": "EVALUATED",
        "protocol": "blocked chronological cross-validation; no shuffling; fixed directional specification",
        "folds": folds,
        "validation_draw_count": total,
        "number_pool_size": number_pool_size,
        "mean_hits_out_of_6": round(weighted / total, 6),
        "uniform_random_expected_hits_out_of_6": round(36 / number_pool_size, 6),
        "fold_reports": reports,
        "current_target_numbers_exposed": False,
        "automatic_candidate_selection": False,
    }


def rank_candidates_retrospective(
    later_history: list[dict[str, Any]],
    *,
    target_date: str,
    number_pool_size: int,
    execution_spec: dict[str, Any],
    min_train_draws: int = DEFAULT_MIN_TRAIN_DRAWS,
) -> list[dict[str, Any]]:
    """Reverse-only diagnostic: hidden A is scored using the first later B/C rows."""
    if type(number_pool_size) is not int or number_pool_size not in ALLOWED_POOL_SIZES:
        _fail("only the 6-of-60 number pool is supported.")
    if type(min_train_draws) is not int or not 2 <= min_train_draws <= MAX_INPUT_HISTORY_DRAWS:
        _fail("minimum later-history draws are outside the supported range.")
    target = _canonical_date(target_date, "target_date")
    if target < RESEARCH_SCOPE_START:
        _fail("retrospective target is before the active research scope.")
    checked = _validate_rows(later_history, field="later_history", maximum=MAX_INPUT_HISTORY_DRAWS)
    if len(checked) < min_train_draws:
        _fail("later historical reference is too short for reverse-time scoring.")
    if any(date.fromisoformat(row["date"]) <= target for row in checked):
        _fail("later_history contains the target or an earlier draw.")
    spec = validate_execution_spec(execution_spec)
    if spec["direction"] != "reverse":
        _fail("reverse retrospective scoring requires a reverse-direction specification.")
    return rank_candidates_for_pair(
        checked[:2],
        target_date=target.isoformat(),
        number_pool_size=number_pool_size,
        execution_spec=spec,
    )


def _reverse_eligible_targets(
    available_history: list[dict[str, Any]],
    canonical_history: list[dict[str, Any]],
    excluded_dates: set[str],
    *,
    min_train_draws: int,
) -> tuple[list[tuple[dict[str, Any], list[dict[str, Any]], int]], dict[str, int]]:
    """Bind reverse targets to their true next two canonical rows without bridging gaps."""
    available = _validate_rows(
        available_history, field="available_reverse_history", maximum=MAX_INPUT_HISTORY_DRAWS
    )
    canonical = _validate_rows(
        canonical_history, field="canonical_reverse_history", maximum=MAX_INPUT_HISTORY_DRAWS
    )
    canonical_by_date = {row["date"]: row for row in canonical}
    available_by_date = {row["date"]: row for row in available}
    if any(canonical_by_date.get(row["date"]) != row for row in available):
        _fail("available reverse history is not an exact subset of the canonical archive.")
    if any(day not in canonical_by_date for day in excluded_dates):
        _fail("excluded holdout dates are not present in the canonical archive.")
    if set(available_by_date).intersection(excluded_dates):
        _fail("available reverse history contains a previously consumed holdout date.")

    eligible: list[tuple[dict[str, Any], list[dict[str, Any]], int]] = []
    skipped = {
        "insufficient_later_history": 0,
        "consumed_adjacent_context": 0,
        "missing_adjacent_context": 0,
    }
    for target in reversed(available):
        later_available = [row for row in available if row["date"] > target["date"]]
        if len(later_available) < min_train_draws:
            skipped["insufficient_later_history"] += 1
            continue
        later_canonical = [row for row in canonical if row["date"] > target["date"]]
        if len(later_canonical) < 2:
            skipped["missing_adjacent_context"] += 1
            continue
        pair = later_canonical[:2]
        if any(row["date"] in excluded_dates for row in pair):
            skipped["consumed_adjacent_context"] += 1
            continue
        if any(available_by_date.get(row["date"]) != row for row in pair):
            skipped["consumed_adjacent_context"] += 1
            continue
        if later_available[:2] != pair:
            skipped["missing_adjacent_context"] += 1
            continue
        eligible.append((target, pair, len(later_available)))
    return eligible, skipped


def reverse_eligible_target_count(
    available_history: list[dict[str, Any]],
    *,
    canonical_history: list[dict[str, Any]] | None = None,
    excluded_dates: set[str] | list[str] | tuple[str, ...] = (),
    min_train_draws: int = DEFAULT_MIN_TRAIN_DRAWS,
) -> int:
    """Count reverse targets with sufficient later history and two valid adjacent B/C rows."""
    if type(min_train_draws) is not int or not 2 <= min_train_draws <= MAX_INPUT_HISTORY_DRAWS:
        _fail("minimum later-history draws are outside the supported range.")
    if not isinstance(excluded_dates, (set, list, tuple)) or any(
        not isinstance(day, str) for day in excluded_dates
    ):
        _fail("excluded_dates must contain date strings.")
    canonical = available_history if canonical_history is None else canonical_history
    eligible, _ = _reverse_eligible_targets(
        available_history, canonical, set(excluded_dates), min_train_draws=min_train_draws
    )
    return len(eligible)


def evaluate_reverse_retrospective_stress(
    history: list[dict[str, Any]],
    *,
    cutoff_date: str,
    number_pool_size: int,
    execution_spec: dict[str, Any],
    test_draw_count: int = 30,
    min_train_draws: int = DEFAULT_MIN_TRAIN_DRAWS,
    skip_recent_test_draws: int = 0,
    canonical_history: list[dict[str, Any]] | None = None,
    excluded_dates: set[str] | list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Run newest-to-oldest reverse tests using true later archive neighbours only."""
    if type(number_pool_size) is not int or number_pool_size not in ALLOWED_POOL_SIZES:
        _fail("only the 6-of-60 number pool is supported.")
    if type(min_train_draws) is not int or not 2 <= min_train_draws <= MAX_INPUT_HISTORY_DRAWS:
        _fail("minimum later-history draws are outside the supported range.")
    if type(test_draw_count) is not int or not 1 <= test_draw_count <= MAX_VALIDATION_DRAWS:
        _fail("reverse test draw count is outside the supported range.")
    if type(skip_recent_test_draws) is not int or skip_recent_test_draws < 0:
        _fail("reverse skip count must be a non-negative integer.")
    if not isinstance(excluded_dates, (set, list, tuple)) or any(
        not isinstance(day, str) for day in excluded_dates
    ):
        _fail("excluded_dates must contain date strings.")
    cutoff = _canonical_date(cutoff_date, "cutoff_date")
    checked = _validate_rows(history, field="history", maximum=MAX_INPUT_HISTORY_DRAWS)
    canonical = checked if canonical_history is None else _validate_rows(
        canonical_history, field="canonical_history", maximum=MAX_INPUT_HISTORY_DRAWS
    )
    if date.fromisoformat(canonical[-1]["date"]) >= cutoff:
        _fail("reverse stress canonical history contains the isolated target or a later date.")
    spec = validate_execution_spec(execution_spec)
    if spec["direction"] != "reverse":
        _fail("reverse stress requires a reverse-direction specification.")
    eligible, skipped = _reverse_eligible_targets(
        checked, canonical, set(excluded_dates), min_train_draws=min_train_draws
    )
    eligible_count = len(eligible)
    if skip_recent_test_draws >= eligible_count:
        _fail("reverse stress skip count exceeds the eligible historical range.")
    selected = eligible[skip_recent_test_draws:skip_recent_test_draws + test_draw_count]
    if not selected:
        _fail("no eligible reverse stress targets remain.")
    details = []
    for target, pair, later_count in selected:
        later_canonical = [row for row in canonical if row["date"] > target["date"]]
        ranking = rank_candidates_for_pair(
            pair,
            target_date=target["date"],
            number_pool_size=number_pool_size,
            execution_spec=spec,
            canonical_history=later_canonical,
            excluded_dates=excluded_dates,
        )
        actual = set(target["numbers"])
        positions = [
            position for position, item in enumerate(ranking[:6], start=1)
            if item["number"] in actual
        ]
        details.append({
            "test_date": target["date"],
            "direction": "reverse_retrospective",
            "later_training_draw_count": later_count,
            "hit_column_positions_1_to_6": positions,
            "hit_count": len(positions),
            "target_numbers_reported": False,
            "predicted_number_values_reported": False,
        })
    distribution = {str(hits): sum(row["hit_count"] == hits for row in details) for hits in range(7)}
    return {
        "engine_version": VERSION,
        "status": "EVALUATED_RETROSPECTIVE_ONLY",
        "direction": "reverse_retrospective",
        "predictive_evidence": False,
        "evaluation_order": "newest_to_oldest",
        "test_draw_count": len(details),
        "evaluated_draw_count": len(details),
        "eligible_draw_count": eligible_count,
        "ineligible_target_count": len(checked) - eligible_count,
        "skipped_context_draw_count": skipped["consumed_adjacent_context"] + skipped["missing_adjacent_context"],
        "skipped_consumed_context_draw_count": skipped["consumed_adjacent_context"],
        "skipped_missing_context_draw_count": skipped["missing_adjacent_context"],
        "skipped_insufficient_later_history_draw_count": skipped["insufficient_later_history"],
        "first_test_date": details[0]["test_date"],
        "last_test_date": details[-1]["test_date"],
        "mean_hits_out_of_6": round(sum(row["hit_count"] for row in details) / len(details), 6),
        "uniform_random_expected_hits_out_of_6": round(36 / number_pool_size, 6),
        "hit_distribution": distribution,
        "per_draw": details,
        "target_number_sets_reported": False,
        "predicted_number_values_reported": False,
        "current_isolated_target_included": False,
        "current_target_numbers_exposed": False,
        "previously_consumed_rows_used_as_context": False,
        "canonical_adjacency_enforced": True,
    }



def run_synthetic_contract_tests(execution_spec: dict[str, Any]) -> dict[str, Any]:
    """Run deterministic synthetic pair tests; no real archive or target values are used."""
    spec = validate_execution_spec(execution_spec)
    rows = []
    for index in range(4):
        day = (RESEARCH_SCOPE_START + timedelta(days=index)).isoformat()
        base = (index * 7) % 60 + 1
        numbers = sorted({((base + offset * 11 - 1) % 60) + 1 for offset in range(6)})
        if len(numbers) != 6:
            _fail("synthetic fixture construction failed.")
        rows.append({"date": day, "numbers": numbers})
    before = json.dumps(rows, sort_keys=True)
    if spec["direction"] == "forward":
        pair, target_date = rows[:2], rows[2]["date"]
    else:
        pair, target_date = rows[1:3], rows[0]["date"]
    target_day = date.fromisoformat(target_date)
    canonical_context = [
        row for row in rows
        if (date.fromisoformat(row["date"]) < target_day if spec["direction"] == "forward"
            else date.fromisoformat(row["date"]) > target_day)
    ]
    first = rank_candidates_for_pair(
        pair, target_date=target_date, number_pool_size=60,
        execution_spec=spec, canonical_history=canonical_context,
    )
    second = rank_candidates_for_pair(
        pair, target_date=target_date, number_pool_size=60,
        execution_spec=spec, canonical_history=canonical_context,
    )
    if first != second or len(first) != 60:
        _fail("synthetic execution was incomplete or non-deterministic.")
    scores = {row["number"]: row["score"] for row in first}
    expected_order = sorted(range(1, 61), key=lambda number: (-scores[number], number))
    if [row["number"] for row in first] != expected_order:
        _fail("synthetic ranking did not preserve deterministic score ordering.")
    if json.dumps(rows, sort_keys=True) != before:
        _fail("synthetic execution mutated its input fixture.")
    return {
        "status": "PASS",
        "synthetic_cases": 1,
        "ranked_values_checked": 60,
        "number_pools_checked": [60],
        "execution_dsl_version": SCHEMA_VERSION,
        "direction": spec["direction"],
        "research_scope_start_date": RESEARCH_SCOPE_START.isoformat(),
        "deterministic": True,
        "input_mutated": False,
        "real_archive_used": False,
        "target_values_used": False,
    }
