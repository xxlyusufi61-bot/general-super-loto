#!/usr/bin/env python3
"""Locally score one frozen forward specification against the isolated current target.

Target numbers are read only in this local process. Persistent scheduler state
receives only the aggregate hit count and non-sensitive formula/archive hashes.
This module makes no network, NVIDIA, or Telegram requests.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import general_autonomous_research as autonomous
import general_execution_spec_dispatch as execution_dispatch
import general_hypothesis_execution as execution_v1
import general_hypothesis_execution_v2 as execution_v2
import general_sealed_evaluation as sealed
import general_target_evaluation_ledger as target_eval_schema


class TargetEvaluationError(RuntimeError):
    """Raised when a frozen target evaluation cannot be proven safe."""


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_exact_claim(claim_id: str, ledger_path: str | Path) -> dict[str, Any]:
    ledger = sealed.load_ledger(ledger_path)
    matches = [entry for entry in ledger["entries"] if entry.get("claim_id") == claim_id]
    if len(matches) != 1:
        raise TargetEvaluationError("The evaluation claim is missing or not unique.")
    entry = matches[0]
    if entry.get("state") != "EVALUATED":
        raise TargetEvaluationError("The sealed historical evaluation is not persisted as EVALUATED.")
    if (
        entry.get("numbers_stored_in_ledger") is not False
        or not isinstance(entry.get("target_date"), str)
        or not isinstance(entry.get("archive_sha256"), str)
        or type(entry.get("archive_record_count")) is not int
    ):
        raise TargetEvaluationError("The sealed claim metadata is incomplete or unsafe.")
    return entry


def _load_bound_snapshot(entry: dict[str, Any], snapshot_directory: str | Path) -> dict[str, Any]:
    root = Path(snapshot_directory).resolve()
    relative = entry.get("archive_snapshot_path")
    if not isinstance(relative, str) or not relative:
        raise TargetEvaluationError("The sealed claim has no immutable archive snapshot path.")
    path = Path(relative)
    resolved = path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise TargetEvaluationError("The sealed snapshot path escapes its approved directory.") from None
    if resolved.name != entry["archive_sha256"] + ".json":
        raise TargetEvaluationError("The sealed snapshot filename is not bound to the archive hash.")
    archive = sealed.load_archive(resolved)
    if (
        archive["archive_sha256"] != entry["archive_sha256"]
        or archive["record_count"] != entry["archive_record_count"]
        or archive["last_date"] != entry["target_date"]
        or not archive["rows"]
        or archive["rows"][-1]["date"] != entry["target_date"]
    ):
        raise TargetEvaluationError("The immutable target snapshot does not match its sealed claim.")
    return archive


def _rank_frozen_forward_candidate(
    candidate: dict[str, Any],
    pre_target_rows: list[dict[str, Any]],
    target_date: str,
) -> list[dict[str, Any]]:
    spec = execution_dispatch.validate_execution_spec(candidate.get("execution_spec"))
    version = execution_dispatch.execution_spec_version(spec)
    if version == 2:
        if spec.get("direction") != "forward":
            raise TargetEvaluationError("Only a forward DSL v2 specification can score the current target.")
        if len(pre_target_rows) < 2:
            raise TargetEvaluationError("Two immediately preceding canonical states are required.")
        # The immediately preceding draws are conditioning context only. They
        # are not rescored as holdout targets, fed into discovery, or used to tune
        # this frozen specification.
        return execution_v2.rank_candidates_for_pair(
            pre_target_rows[-2:],
            target_date=target_date,
            number_pool_size=60,
            execution_spec=spec,
            canonical_history=pre_target_rows,
            excluded_dates=(),
        )
    history = pre_target_rows[-execution_v1.MAX_HISTORY_DRAWS:]
    if not history:
        raise TargetEvaluationError("Legacy forward ranking has no pre-target history.")
    return execution_v1.rank_candidates(
        history,
        cutoff_date=target_date,
        number_pool_size=60,
        execution_spec=spec,
    )


def evaluate_frozen_target(
    claim_id: str,
    attempt_id: str,
    ledger_path: str | Path,
    proposals_path: str | Path,
    specs_path: str | Path,
    *,
    scheduler_state_path: str | Path = "general_autonomous_research_state.json",
    snapshot_directory: str | Path = "general_evaluation_snapshots",
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    entry = _load_exact_claim(claim_id, ledger_path)
    archive = _load_bound_snapshot(entry, snapshot_directory)
    rows = archive["rows"]
    target = rows[-1]
    pre_target_rows = [row for row in rows if row["date"] < entry["target_date"]]
    if len(pre_target_rows) != len(rows) - 1 or any(
        row["date"] >= entry["target_date"] for row in pre_target_rows
    ):
        raise TargetEvaluationError("The pre-target partition is not the exact chronological archive prefix.")

    candidate = sealed._select_one_frozen_candidate(proposals_path, specs_path)
    if candidate is None:
        raise TargetEvaluationError("The exact frozen directional candidate pair is unavailable.")
    if (
        candidate["hypothesis_id"] != entry.get("hypothesis_id")
        or candidate["spec_sha256"] != entry.get("spec_sha256")
        or candidate["execution_spec_dsl_version"] != entry.get("execution_spec_dsl_version")
        or candidate["reverse_hypothesis_id"] != entry.get("reverse_hypothesis_id")
        or candidate["reverse_spec_sha256"] != entry.get("reverse_spec_sha256")
        or candidate["reverse_execution_spec_dsl_version"] != entry.get("reverse_execution_spec_dsl_version")
    ):
        raise TargetEvaluationError("The forward specification no longer matches the persisted sealed claim.")

    ranking = _rank_frozen_forward_candidate(candidate, pre_target_rows, entry["target_date"])
    if not isinstance(ranking, list) or len(ranking) != 60:
        raise TargetEvaluationError("The frozen forward evaluator did not rank the complete 1..60 pool.")
    top_six = ranking[:6]
    if (
        len(top_six) != 6
        or any(not isinstance(item, dict) or type(item.get("number")) is not int for item in top_six)
        or len({item["number"] for item in top_six}) != 6
        or any(not 1 <= item["number"] <= 60 for item in top_six)
    ):
        raise TargetEvaluationError("The frozen forward evaluator returned an invalid top-six set.")

    target_values = set(target["numbers"])
    candidate_values = {item["number"] for item in top_six}
    hit_count = len(target_values.intersection(candidate_values))
    if type(hit_count) is not int or not 0 <= hit_count <= 6:
        raise TargetEvaluationError("The local aggregate hit count is outside 0..6.")

    report = {
        "schema_version": 1,
        "status": "LOCAL_TARGET_EVALUATION_COMPLETED",
        "claim_id": claim_id,
        "attempt_id": attempt_id,
        "target_date": entry["target_date"],
        "archive_sha256": entry["archive_sha256"],
        "archive_record_count": entry["archive_record_count"],
        "forward_hypothesis_id": candidate["hypothesis_id"],
        "forward_spec_sha256": candidate["spec_sha256"],
        "execution_spec_dsl_version": candidate["execution_spec_dsl_version"],
        "candidate_count": 6,
        "hit_count": hit_count,
        "uniform_random_expected_hits_out_of_6": 0.6,
        "evaluation_scope": "ONE_CURRENT_HIDDEN_TARGET",
        "prior_draws_used_only_as_conditioning_context": True,
        "prior_holdouts_reused_as_evaluation_targets": False,
        "prior_draws_reused_for_discovery_or_tuning": False,
        "target_numbers_exposed": False,
        "target_numbers_reported": False,
        "candidate_numbers_exposed": False,
        "candidate_numbers_reported": False,
        "predicted_number_values_reported": False,
        "per_number_matches_reported": False,
        "target_values_sent_to_model": False,
        "target_score_sent_to_model": False,
        "target_score_saved_to_research_memory": False,
        "reverse_spec_used_for_prediction": False,
        "live_hosted_model_request_made": False,
        "telegram_request_made": False,
        "method_registry_modified": False,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    try:
        result_ledger = autonomous.read_state(scheduler_state_path)
        existing = any(item["claim_id"] == claim_id for item in result_ledger["target_evaluations"])
        result_ledger = autonomous.record_target_evaluation(result_ledger, report)
        autonomous.write_state(scheduler_state_path, result_ledger)
    except Exception as exc:
        raise TargetEvaluationError("Private local target result could not be persisted: " + str(exc)) from None
    report["persistent_result_status"] = "ALREADY_RECORDED" if existing else "RECORDED"
    if output_path is not None:
        _write_json_atomic(Path(output_path), report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claim-id", required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--ledger", type=Path, default=Path("general_evaluation_ledger.json"))
    parser.add_argument("--proposals", type=Path, default=Path("open_research_proposals.json"))
    parser.add_argument("--specs", type=Path, default=Path("open_research_execution_specs.json"))
    parser.add_argument("--state", type=Path, default=Path("general_autonomous_research_state.json"))
    parser.add_argument("--snapshot-directory", type=Path, default=Path("general_evaluation_snapshots"))
    parser.add_argument("--output", type=Path, default=Path("open_research_target_evaluation_report.json"))
    args = parser.parse_args(argv)
    try:
        report = evaluate_frozen_target(
            args.claim_id, args.attempt_id, args.ledger, args.proposals, args.specs,
            scheduler_state_path=args.state,
            snapshot_directory=args.snapshot_directory,
            output_path=args.output,
        )
        print(json.dumps({
            "status": report["status"],
            "target_date": report["target_date"],
            "claim_id": report["claim_id"],
            "attempt_id": report["attempt_id"],
            "candidate_count": report["candidate_count"],
            "target_numbers_exposed": report["target_numbers_exposed"],
            "candidate_numbers_exposed": report["candidate_numbers_exposed"],
            "persistent_result_status": report["persistent_result_status"],
        }, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as exc:
        print("LOCAL TARGET EVALUATION BLOCKED: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
