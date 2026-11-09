"""Version dispatch for safe hypothesis execution specifications.

The legacy v1 evaluator is not modified. New callers use this module to validate
and synthetic-test v1 or v2 specs without changing their recorded semantics.
"""
from __future__ import annotations

from typing import Any

import general_hypothesis_execution as v1
import general_hypothesis_execution_v2 as v2


SUPPORTED_DSL_VERSIONS = (1, 2)


def execution_spec_version(value: Any) -> int:
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int:
        raise ValueError("Execution specification must declare an integer schema_version.")
    version = value["schema_version"]
    if version not in SUPPORTED_DSL_VERSIONS:
        raise ValueError("Unsupported safe execution DSL version.")
    return version


def validate_execution_spec(value: Any) -> dict[str, Any]:
    version = execution_spec_version(value)
    if version == 1:
        return v1.validate_execution_spec(value)
    return v2.validate_execution_spec(value)


def run_synthetic_contract_tests(value: Any) -> dict[str, Any]:
    spec = validate_execution_spec(value)
    if spec["schema_version"] == 1:
        return v1.run_synthetic_contract_tests(spec)
    return v2.run_synthetic_contract_tests(spec)


def execution_spec_direction(value: Any) -> str | None:
    spec = validate_execution_spec(value)
    if spec["schema_version"] == 1:
        return None
    return spec["direction"]


def execution_spec_engine_version(value: Any) -> str:
    spec = validate_execution_spec(value)
    return v1.VERSION if spec["schema_version"] == 1 else v2.VERSION
