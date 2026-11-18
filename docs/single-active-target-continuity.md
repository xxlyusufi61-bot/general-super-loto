# Single-active-target research continuity

## Scope

The autonomous scheduler keeps one active Super Loto target at a time. It also records the newest validated canonical archive snapshot independently. Intermediate archived draws are not queued as separate research targets.

The active target is bound to an immutable content-addressed archive prefix. The scheduler verifies its archive hash, record count and target date before materialization or research. Archive mutation beneath either the active or latest snapshot fails closed.

## Continuation and recovery

- An active target remains pinned even before its first attempt is reserved. Offline discovery or checkpoints may already exist, so a newer archive cannot replace an unattempted active snapshot.
- A claimed attempt without a terminal event remains in progress and blocks a new attempt.
- A failed attempt remains attached to the same active target and exact archive snapshot. Retry eligibility continues to follow the existing provider-delivery and manual-unlock safety rules.
- A successful sealed evaluation completes one frozen formula test, not the target's whole research lifetime. The same current target may therefore receive a later independent discovery package.
- A valid no-candidate outcome also completes a package without consuming the failed-attempt budget.
- Once a later canonical draw is observed, a terminal active package is recorded in history and only the newest archive snapshot is promoted. An unattempted, in-progress or failed target is not silently skipped; its exact snapshot remains pinned while the latest archive cursor is staged separately.
- Target retirement after exhaustion remains a separate explicit manual action.

## Failed-attempt budget

The per-target limit is ten terminal `FAILED` attempts. Successful evaluations and `NO_ELIGIBLE_CANDIDATE` outcomes do not consume that budget. The total attempt history remains append-only, so a successful or no-candidate package is still auditable.

When ten failures have accumulated, the scheduler blocks further attempts even if an imported historical record has a later success marker. The target can only be released through the existing explicit exhausted-target retirement path, which verifies the exact active archive identity and the ten failed events.

## Hidden-target local evaluation

After the sealed historical evaluation has been persisted as `EVALUATED`, the coordinator may locally score the exact frozen forward specification against the one current hidden target. The target draw is read only inside the local evaluator. It is not added to discovery, training, cross-validation, model prompts or Telegram reports.

The immediate pre-target canonical draws are conditioning context only. They are not rescored as fresh validation targets, used to tune the frozen specification, or sent to a hosted model. The reverse specification remains retrospective-only and is never used to score the current target.

Only the following number-free metadata is retained in the private scheduler state's `target_evaluations` list: sealed claim/attempt identifiers, target date, archive hash/count, forward hypothesis/specification identifiers, DSL version, six-candidate count, aggregate hit count (0–6), the fixed 0.600/6 R60 reference and completion timestamp. Raw target numbers, candidate numbers, rankings and per-number matches are rejected by the result schema.

This private result list is part of the existing autonomous scheduler state object. It is not copied into GENERAL's model-readable research memory or Telegram. The existing research memory remains available for GENERAL-origin formulas, synthetic tests and sanitized historical research feedback.

If a workflow fails after the sealed claim is already `EVALUATED`, an identical later proposal pair reuses the exact completed claim. The local scoring step can then be retried without re-running sealed holdout scoring or consuming the same validation dates twice.

## Preserved safeguards and unchanged limits

The change preserves the 2020-08-02+ 6/60 scope, true chronology, forward/reverse holdout separation, canonical adjacency checks, one-shot sealed claims, target isolation, the method-registry approval boundary and append-only research memory.

This PR does not change the NVIDIA limits: 30 normal requests/minute, 40 hard requests/minute, 200 requests per rolling 24 hours, or four requests per workflow package. It adds no live provider or Telegram calls.
