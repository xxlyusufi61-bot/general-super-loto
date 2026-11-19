#!/usr/bin/env python3
"""Locally score one frozen forward specification against the isolated current target.

Target numbers and the computed score remain inside the local evaluator process.
Persistent scheduler state receives only a score-free receipt. This module makes
no network, NVIDIA, or Telegram requests.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import general_autonomous_research as autonomous
import general_execution_spec_dispatch as execution_dispatch
import general_hypothesis_execution as execution_v1
import general_hypothesis_execution_v2 as execution_v2
import general_research_memory as research_memory
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
        # this frozen specification. This is a forward local target score, not
        # another historical validation pass, so prior validation-date exclusion
        # applies to holdout selection, not to these observed conditioning states.
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


def _write_github_output(path: str | None, values: dict[str, Any]) -> None:
    if not path:
        return
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            if type(value) is bool:
                rendered = "true" if value else "false"
            elif isinstance(value, str) and "\\n" not in value and "\\r" not in value:
                rendered = value
            else:
                raise TargetEvaluationError("Recovery output contains an unsupported value.")
            stream.write(key + "=" + rendered + "\\n")


def recover_evaluated_claim_for_attempt(
    attempt_id: str,
    *,
    scheduler_state_path: str | Path,
    ledger_path: str | Path,
    memory_path: str | Path,
    proposals_path: str | Path,
    specs_path: str | Path,
    claim_output_path: str | Path,
) -> dict[str, Any]:
    """Restore the exact evaluated candidate pair after a local-score failure.

    This path is deliberately offline. It is activated only when the immediately
    preceding attempt for the immutable active target ended with
    SEALED_EVALUATION_INCOMPLETE and its exact EVALUATED candidate pair can be
    recovered from GENERAL's validated research memory.
    """
    state = autonomous.read_state(scheduler_state_path)
    attempts_by_id = {item["attempt_id"]: item for item in state["attempts"]}
    attempt = attempts_by_id.get(attempt_id)
    if attempt is None or not state["attempts"] or state["attempts"][-1]["attempt_id"] != attempt_id:
        raise TargetEvaluationError("Local-score recovery must bind to the newest reserved target attempt.")
    if any(event["attempt_id"] == attempt_id for event in state["events"]):
        raise TargetEvaluationError("Local-score recovery cannot run after the current attempt is terminal.")

    active = autonomous.target_queue.active_item(state["target_queue"])
    if active is None or (
        active["target_date"] != attempt["target_date"]
        or active["archive_sha256"] != attempt["archive_sha256"]
        or active["archive_record_count"] != attempt["archive_record_count"]
    ):
        raise TargetEvaluationError("Local-score recovery does not match the immutable active target.")

    prior_attempts = [
        item for item in state["attempts"]
        if item["target_date"] == attempt["target_date"]
        and item["archive_sha256"] == attempt["archive_sha256"]
        and item["archive_record_count"] == attempt["archive_record_count"]
    ]
    if len(prior_attempts) < 2 or prior_attempts[-1]["attempt_id"] != attempt_id:
        return {
            "status": "NO_LOCAL_SCORE_RECOVERY_REQUIRED",
            "recovery_required": False,
            "recovery_available": False,
            "claim_id": None,
        }
    prior_attempt = prior_attempts[-2]
    prior_event = next(
        (item for item in state["events"] if item["attempt_id"] == prior_attempt["attempt_id"]),
        None,
    )
    if (
        prior_event is None
        or prior_event["outcome"] != "FAILED"
        or prior_event["reason_code"] != "SEALED_EVALUATION_INCOMPLETE"
    ):
        return {
            "status": "NO_LOCAL_SCORE_RECOVERY_REQUIRED",
            "recovery_required": False,
            "recovery_available": False,
            "claim_id": None,
        }

    ledger = sealed.load_ledger(ledger_path)
    memory = research_memory.load_memory(memory_path)
    evaluated_claims = [
        entry for entry in ledger["entries"]
        if entry.get("state") == "EVALUATED"
        and entry.get("target_date") == attempt["target_date"]
        and entry.get("archive_sha256") == attempt["archive_sha256"]
        and entry.get("archive_record_count") == attempt["archive_record_count"]
        and not any(
            receipt["claim_id"] == entry.get("claim_id")
            for receipt in state["target_evaluations"]
        )
    ]
    memory_groups: dict[str, dict[str, dict[str, Any]]] = {}
    for entry in memory["entries"]:
        feedback = entry.get("evaluation_feedback")
        if (
            entry.get("target_date") != attempt["target_date"]
            or entry.get("archive_sha256") != attempt["archive_sha256"]
            or entry.get("status") != "EVALUATED"
            or not isinstance(feedback, dict)
            or not isinstance(feedback.get("claim_id"), str)
        ):
            continue
        memory_groups.setdefault(feedback["claim_id"], {})[entry["direction"]] = entry

    matches = []
    for claim in evaluated_claims:
        group = memory_groups.get(claim["claim_id"], {})
        forward = group.get("forward")
        reverse = group.get("reverse")
        if forward is None or reverse is None:
            continue
        if (
            forward["execution_spec_sha256"] != claim.get("spec_sha256")
            or reverse["execution_spec_sha256"] != claim.get("reverse_spec_sha256")
            or forward["evaluation_feedback"]["spec_sha256"] != claim.get("spec_sha256")
            or reverse["evaluation_feedback"]["spec_sha256"] != claim.get("reverse_spec_sha256")
            or research_memory.execution_spec_version(forward["execution_spec"])
                != claim.get("execution_spec_dsl_version")
            or research_memory.execution_spec_version(reverse["execution_spec"])
                != claim.get("reverse_execution_spec_dsl_version")
        ):
            continue
        matches.append((claim, forward, reverse))

    if len(matches) != 1:
        # Do not guess between multiple frozen candidates and never fall back
        # to a new model request for a target whose local evaluation failed.
        return {
            "status": "LOCAL_SCORE_RECOVERY_REQUIRED_BUT_UNAVAILABLE",
            "recovery_required": True,
            "recovery_available": False,
            "claim_id": None,
        }

    claim, forward, reverse = matches[0]
    proposal_forward = copy.deepcopy(forward["proposals"][0])
    proposal_reverse = copy.deepcopy(reverse["proposals"][0])
    proposal_forward["hypothesis_id"] = claim["hypothesis_id"]
    proposal_reverse["hypothesis_id"] = claim["reverse_hypothesis_id"]
    spec_forward = copy.deepcopy(forward["execution_spec"])
    spec_reverse = copy.deepcopy(reverse["execution_spec"])

    try:
        for proposal, spec, expected_id, expected_hash, expected_direction in (
            (proposal_forward, spec_forward, claim["hypothesis_id"], claim["spec_sha256"], "forward"),
            (proposal_reverse, spec_reverse, claim["reverse_hypothesis_id"], claim["reverse_spec_sha256"], "reverse"),
        ):
            checked_proposal = research_memory._validate_proposal(proposal, canonical=True)
            checked_spec = research_memory.validate_execution_spec(spec)
            synthetic = research_memory.run_synthetic_contract_tests(checked_spec)
            if (
                checked_proposal != proposal
                or checked_spec != spec
                or proposal["hypothesis_id"] != expected_id
                or proposal["discovery_direction"] != expected_direction
                or research_memory._sha(research_memory._canonical(spec)) != expected_hash
                or not research_memory.execution_spec_matches_proposal(proposal, spec)
                or synthetic.get("status") != "PASS"
                or synthetic.get("real_archive_used") is not False
            ):
                raise TargetEvaluationError("Recovered frozen candidate failed its original schema/hash/synthetic checks.")
    except (TypeError, ValueError, KeyError) as exc:
        raise TargetEvaluationError("Recovered frozen candidate failed validation: " + str(exc)) from None

    proposals_doc = {
        "schema_version": 2,
        "status": "VALID_PROPOSALS",
        "proposals": [proposal_forward, proposal_reverse],
    }
    specs_doc = {
        "schema_version": 1,
        "status": "VALIDATED_SPECS",
        "dsl_validated": True,
        "synthetic_tests_passed": True,
        "specifications": [
            {
                "hypothesis_id": claim["hypothesis_id"],
                "execution_spec": spec_forward,
                "dsl_validated": True,
                "synthetic_tests_passed": True,
            },
            {
                "hypothesis_id": claim["reverse_hypothesis_id"],
                "execution_spec": spec_reverse,
                "dsl_validated": True,
                "synthetic_tests_passed": True,
            },
        ],
    }
    recovered_claim = {
        "status": "RECOVERED_EVALUATED_CLAIM",
        "claim_created": False,
        "claim_resumed": False,
        "claim_reused": True,
        "claim_id": claim["claim_id"],
        "target_date": claim["target_date"],
        "archive_sha256": claim["archive_sha256"],
        "archive_record_count": claim["archive_record_count"],
        "hypothesis_id": claim["hypothesis_id"],
        "spec_sha256": claim["spec_sha256"],
        "execution_spec_dsl_version": claim["execution_spec_dsl_version"],
        "reverse_hypothesis_id": claim["reverse_hypothesis_id"],
        "reverse_spec_sha256": claim["reverse_spec_sha256"],
        "reverse_execution_spec_dsl_version": claim["reverse_execution_spec_dsl_version"],
        "validation_start_date": claim["validation_start_date"],
        "validation_end_date": claim["validation_end_date"],
        "validation_draw_count": claim["validation_count"],
        "reverse_validation_start_date": claim["reverse_validation_start_date"],
        "reverse_validation_end_date": claim["reverse_validation_end_date"],
        "reverse_validation_draw_count": claim["reverse_validation_count"],
        "archive_snapshot_path": claim["archive_snapshot_path"],
        "current_target_included": False,
        "current_target_numbers_exposed": False,
        "duplicate_holdout_scoring_performed": False,
        "local_target_score_recovery": True,
    }
    _write_json_atomic(Path(proposals_path), proposals_doc)
    _write_json_atomic(Path(specs_path), specs_doc)
    _write_json_atomic(Path(claim_output_path), recovered_claim)
    return {
        "status": "EXACT_EVALUATED_CANDIDATE_PAIR_RECOVERED",
        "recovery_required": True,
        "recovery_available": True,
        "claim_id": claim["claim_id"],
    }


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
        raise TargetEvaluationError("Score-free target-evaluation receipt could not be persisted: " + str(exc)) from None
    report["persistent_result_status"] = "ALREADY_RECORDED" if existing else "RECORDED"
    if output_path is not None:
        _write_json_atomic(Path(output_path), report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recover-candidates", action="store_true")
    parser.add_argument("--claim-id")
    parser.add_argument("--attempt-id")
    parser.add_argument("--ledger", type=Path, default=Path("general_evaluation_ledger.json"))
    parser.add_argument("--proposals", type=Path, default=Path("open_research_proposals.json"))
    parser.add_argument("--specs", type=Path, default=Path("open_research_execution_specs.json"))
    parser.add_argument("--state", type=Path, default=Path("general_autonomous_research_state.json"))
    parser.add_argument("--memory", type=Path, default=Path("general_research_memory.json"))
    parser.add_argument("--claim-output", type=Path, default=Path("open_research_evaluation_claim.json"))
    parser.add_argument("--github-output")
    parser.add_argument("--snapshot-directory", type=Path, default=Path("general_evaluation_snapshots"))
    parser.add_argument("--output", type=Path, default=Path("open_research_target_evaluation_report.json"))
    args = parser.parse_args(argv)
    try:
        if args.recover_candidates:
            if not args.attempt_id:
                raise TargetEvaluationError("--attempt-id is required for recovery.")
            result = recover_evaluated_claim_for_attempt(
                args.attempt_id,
                scheduler_state_path=args.state,
                ledger_path=args.ledger,
                memory_path=args.memory,
                proposals_path=args.proposals,
                specs_path=args.specs,
                claim_output_path=args.claim_output,
            )
            _write_github_output(args.github_output, {
                "recovery_required": result["recovery_required"],
                "recovery_available": result["recovery_available"],
                "claim_id": result["claim_id"] or "",
            })
            print(json.dumps({
                "status": result["status"],
                "recovery_required": result["recovery_required"],
                "recovery_available": result["recovery_available"],
            }, ensure_ascii=False, sort_keys=True))
            return 0
        if not args.claim_id or not args.attempt_id:
            raise TargetEvaluationError("--claim-id and --attempt-id are required for local scoring.")
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
