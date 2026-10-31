"""Safe, declarative hypothesis execution for GENERAL.

Only a small JSON expression language is interpreted. Model-supplied Python,
imports, dynamic evaluation, file access, network access, and arbitrary code are
never accepted or executed.
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import date
from typing import Any

VERSION = "0.1.0"
ALLOWED_POOL_SIZES = {54, 60}
MAX_AST_NODES = 32
MAX_AST_DEPTH = 8
MAX_ABS_LITERAL = 100_000
MAX_ABS_SCORE = 1_000_000_000
MAX_HISTORY_DRAWS = 64
MAX_INPUT_HISTORY_DRAWS = 10_000
MAX_VALIDATION_DRAWS = 30
DEFAULT_MIN_TRAIN_DRAWS = 40

NUMERIC_BINARY_OPS = {"add", "sub", "mul", "div", "mod", "min", "max"}
COMPARISON_OPS = {"eq", "ne", "lt", "lte", "gt", "gte"}
BOOLEAN_BINARY_OPS = {"and", "or"}
VARIABLES = {"candidate", "value", "draw_age"}


def _fail(message: str) -> None:
    raise ValueError("Safe hypothesis execution rejected: " + message)


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
    depth: int,
    state: dict[str, int],
    inside_aggregate: bool = False,
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
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or abs(value) > MAX_ABS_LITERAL
        ):
            _fail("numeric constants must be finite and within the safe bound.")
        return "number"

    if op == "var":
        if set(node) != {"op", "name"} or node.get("name") not in VARIABLES:
            _fail("variable node is invalid.")
        if node["name"] in {"value", "draw_age"} and not inside_aggregate:
            _fail("history variables may only appear inside the aggregate expression.")
        return "number"

    if op == "aggregate_history_numbers":
        if inside_aggregate:
            _fail("nested history aggregates are not supported.")
        if set(node) != {"op", "aggregate", "expression"}:
            _fail("history aggregate node keys are invalid.")
        aggregate = node.get("aggregate")
        if aggregate not in {"count", "sum"}:
            _fail("history aggregate must be count or sum.")
        state["aggregates"] += 1
        if state["aggregates"] > 1:
            _fail("exactly one history aggregate is allowed.")
        child_type = _validate_node(
            node.get("expression"),
            depth=depth + 1,
            state=state,
            inside_aggregate=True,
        )
        if aggregate == "count" and child_type != "boolean":
            _fail("count aggregate requires a boolean expression.")
        if aggregate == "sum" and child_type != "number":
            _fail("sum aggregate requires a numeric expression.")
        return "number"

    if op in {"abs", "not"}:
        if set(node) != {"op", "arg"}:
            _fail("unary node keys are invalid.")
        child_type = _validate_node(
            node.get("arg"),
            depth=depth + 1,
            state=state,
            inside_aggregate=inside_aggregate,
        )
        expected = "number" if op == "abs" else "boolean"
        if child_type != expected:
            _fail(op + " received an expression of the wrong type.")
        return expected

    if op in NUMERIC_BINARY_OPS | COMPARISON_OPS | BOOLEAN_BINARY_OPS:
        if set(node) != {"op", "left", "right"}:
            _fail("binary node keys are invalid.")
        left_type = _validate_node(
            node.get("left"),
            depth=depth + 1,
            state=state,
            inside_aggregate=inside_aggregate,
        )
        right_type = _validate_node(
            node.get("right"),
            depth=depth + 1,
            state=state,
            inside_aggregate=inside_aggregate,
        )
        if op in NUMERIC_BINARY_OPS | COMPARISON_OPS:
            if left_type != "number" or right_type != "number":
                _fail(op + " requires numeric operands.")
            return "number" if op in NUMERIC_BINARY_OPS else "boolean"
        if left_type != "boolean" or right_type != "boolean":
            _fail(op + " requires boolean operands.")
        return "boolean"

    _fail("operator is not in the allowlist: " + op)
    return "invalid"


def validate_execution_spec(value: Any) -> dict[str, Any]:
    """Validate and return a detached copy of one declarative score program."""
    if not isinstance(value, dict) or set(value) != {"schema_version", "score_expression"}:
        _fail("execution specification keys are invalid.")
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        _fail("unsupported execution specification version.")
    state = {"nodes": 0, "aggregates": 0}
    result_type = _validate_node(
        value.get("score_expression"),
        depth=1,
        state=state,
    )
    if result_type != "number":
        _fail("the final score expression must be numeric.")
    if state["aggregates"] != 1:
        _fail("the score expression must contain exactly one history aggregate.")
    # JSON round-tripping creates a detached plain-data representation.
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _validate_rows(
    rows: Any,
    *,
    pool_size: int,
    field: str,
    maximum: int,
) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not rows or len(rows) > maximum:
        _fail(field + " must be a non-empty list within the configured size limit.")
    checked: list[dict[str, Any]] = []
    previous: date | None = None
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != {"date", "numbers"}:
            _fail(field + " row " + str(index) + " has an invalid shape.")
        current = _canonical_date(row.get("date"), field + " date")
        if previous is not None and current <= previous:
            _fail(field + " must be strictly chronological with unique dates.")
        numbers = row.get("numbers")
        if (
            not isinstance(numbers, list)
            or len(numbers) != 6
            or any(type(number) is not int for number in numbers)
            or len(set(numbers)) != 6
            or any(number < 1 or number > pool_size for number in numbers)
        ):
            _fail(field + " contains an invalid six-number draw for its regime.")
        checked.append({"date": current.isoformat(), "numbers": list(numbers)})
        previous = current
    return checked


def _checked_number(value: Any) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("an expression produced a non-numeric result.")
    if not math.isfinite(value) or abs(value) > MAX_ABS_SCORE:
        _fail("an expression produced a non-finite or out-of-range score.")
    return value


def _evaluate_node(
    node: dict[str, Any],
    context: dict[str, float | int],
    history: list[dict[str, Any]],
) -> bool | float | int:
    op = node["op"]
    if op == "const":
        return node["value"]
    if op == "var":
        return context[node["name"]]
    if op == "aggregate_history_numbers":
        aggregate = node["aggregate"]
        expression = node["expression"]
        if aggregate == "count":
            total = 0
            for draw_age, row in enumerate(reversed(history)):
                for number in row["numbers"]:
                    local = {
                        "candidate": context["candidate"],
                        "value": number,
                        "draw_age": draw_age,
                    }
                    matched = _evaluate_node(expression, local, [])
                    if not isinstance(matched, bool):
                        _fail("count aggregate expression did not return a boolean.")
                    total += int(matched)
            return total
        contributions = []
        for draw_age, row in enumerate(reversed(history)):
            for number in row["numbers"]:
                local = {
                    "candidate": context["candidate"],
                    "value": number,
                    "draw_age": draw_age,
                }
                contribution = _evaluate_node(expression, local, [])
                contributions.append(_checked_number(contribution))
        return _checked_number(math.fsum(contributions))

    if op == "abs":
        return _checked_number(abs(_evaluate_node(node["arg"], context, history)))
    if op == "not":
        value = _evaluate_node(node["arg"], context, history)
        if not isinstance(value, bool):
            _fail("not received a non-boolean value.")
        return not value

    left = _evaluate_node(node["left"], context, history)
    if op == "and":
        if not isinstance(left, bool):
            _fail("and received a non-boolean left operand.")
        if not left:
            return False
        right = _evaluate_node(node["right"], context, history)
        if not isinstance(right, bool):
            _fail("and received a non-boolean right operand.")
        return right
    if op == "or":
        if not isinstance(left, bool):
            _fail("or received a non-boolean left operand.")
        if left:
            return True
        right = _evaluate_node(node["right"], context, history)
        if not isinstance(right, bool):
            _fail("or received a non-boolean right operand.")
        return right

    right = _evaluate_node(node["right"], context, history)
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
    elif op == "mod":
        if right <= 0 or not float(right).is_integer():
            _fail("modulo divisor must be a positive integer.")
        result = left % right
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


def _rank_checked_history(
    checked_history: list[dict[str, Any]],
    *,
    number_pool_size: int,
    spec: dict[str, Any],
) -> list[dict[str, Any]]:
    ranking = []
    for candidate in range(1, number_pool_size + 1):
        score = _evaluate_node(
            spec["score_expression"],
            {"candidate": candidate},
            checked_history,
        )
        ranking.append({"number": candidate, "score": _checked_number(score)})
    ranking.sort(key=lambda item: (-item["score"], item["number"]))
    return ranking


def rank_candidates(
    history: list[dict[str, Any]],
    *,
    cutoff_date: str,
    number_pool_size: int,
    execution_spec: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rank a regime's candidate numbers using only rows strictly before cutoff."""
    if type(number_pool_size) is not int or number_pool_size not in ALLOWED_POOL_SIZES:
        _fail("number pool must be R54 (54) or R60 (60).")
    cutoff = _canonical_date(cutoff_date, "cutoff_date")
    checked_history = _validate_rows(
        history,
        pool_size=number_pool_size,
        field="history",
        maximum=MAX_HISTORY_DRAWS,
    )
    if date.fromisoformat(checked_history[-1]["date"]) >= cutoff:
        _fail("history contains a draw on or after the scoring cutoff.")
    spec = validate_execution_spec(execution_spec)
    return _rank_checked_history(
        checked_history,
        number_pool_size=number_pool_size,
        spec=spec,
    )


def evaluate_holdout(
    discovery_history: list[dict[str, Any]],
    validation_draws: list[dict[str, Any]],
    *,
    cutoff_date: str,
    number_pool_size: int,
    execution_spec: dict[str, Any],
    min_train_draws: int = DEFAULT_MIN_TRAIN_DRAWS,
) -> dict[str, Any]:
    """Evaluate a fixed specification on a separate chronological holdout.

    Validation rows are never passed to the model or score expression as a
    current target. Previously scored validation rows may enter later history,
    because they are then chronologically observed.
    """
    if type(number_pool_size) is not int or number_pool_size not in ALLOWED_POOL_SIZES:
        _fail("number pool must be R54 (54) or R60 (60).")
    if type(min_train_draws) is not int or not 2 <= min_train_draws <= MAX_HISTORY_DRAWS:
        _fail("minimum training draws are outside the supported range.")
    cutoff = _canonical_date(cutoff_date, "cutoff_date")
    discovery = _validate_rows(
        discovery_history,
        pool_size=number_pool_size,
        field="discovery_history",
        maximum=MAX_INPUT_HISTORY_DRAWS,
    )
    validation = _validate_rows(
        validation_draws,
        pool_size=number_pool_size,
        field="validation_draws",
        maximum=MAX_VALIDATION_DRAWS,
    )
    if date.fromisoformat(discovery[-1]["date"]) >= date.fromisoformat(validation[0]["date"]):
        _fail("discovery history must end before the validation window begins.")
    if date.fromisoformat(validation[-1]["date"]) >= cutoff:
        _fail("validation window contains the isolated target or a later date.")
    if len(discovery) < min_train_draws:
        _fail("discovery history is too short for the required initial training window.")

    spec = validate_execution_spec(execution_spec)
    scores: list[int] = []
    for index, target in enumerate(validation):
        training = (discovery + validation[:index])[-MAX_HISTORY_DRAWS:]
        if len(training) < min_train_draws:
            _fail("a validation fold does not have enough earlier training draws.")
        ranked = rank_candidates(
            training,
            cutoff_date=target["date"],
            number_pool_size=number_pool_size,
            execution_spec=spec,
        )
        selected = {item["number"] for item in ranked[:6]}
        scores.append(len(selected.intersection(target["numbers"])))

    mean_hits = statistics.fmean(scores)
    standard_error = (
        statistics.stdev(scores) / math.sqrt(len(scores))
        if len(scores) > 1 else None
    )
    return {
        "engine_version": VERSION,
        "status": "EVALUATED",
        "protocol": "forward chronological holdout walk-forward; fixed specification; no shuffling",
        "spec_sha256": hashlib.sha256(
            json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest(),
        "number_pool_size": number_pool_size,
        "validation_draw_count": len(validation),
        "validation_start_date": validation[0]["date"],
        "validation_end_date": validation[-1]["date"],
        "minimum_training_draws": min_train_draws,
        "maximum_history_window_draws": MAX_HISTORY_DRAWS,
        "mean_hits_out_of_6": round(mean_hits, 6),
        "standard_error_of_mean_hits": (
            round(standard_error, 6) if standard_error is not None else None
        ),
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
        "interpretation": (
            "This is one fixed-specification holdout evaluation. It is not proof of a predictive mechanism. "
            "Do not tune or select candidates on this same validation window; require a fresh, non-overlapping "
            "holdout for any later confirmatory evaluation."
        ),
    }
