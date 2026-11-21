# Single-active-target research continuity

## Scope

The autonomous scheduler keeps one active Super Loto target at a time. It also records the newest validated canonical archive snapshot independently. Intermediate archived draws are not queued as separate research targets.

The active target is bound to an immutable content-addressed archive prefix. The scheduler verifies its archive hash, record count and target date before materialization or research. Archive mutation beneath either the active or latest snapshot fails closed.

## Migration from the legacy queue

The first queue-capture after the code is deployed converts the legacy FIFO representation to the single-active-target schema. It does not erase attempt or event history. An in-progress, failed, or unattempted legacy FIFO head remains pinned to its exact snapshot because the legacy workflow materialized that head for offline research before reserving a provider attempt. Later queued draws are not promoted one by one. Once that active target is completed, only the then-latest canonical archive snapshot is promoted; intermediate queued draws are not promoted as separate targets. The latest full archive snapshot remains staged separately.

This migration is code in the PR, not a live state-store operation performed during PR preparation. It occurs only when the normal queue-capture workflow runs after the PR has been merged.

## Continuation and recovery

- An active target remains pinned even before its first attempt is reserved. Offline discovery or checkpoints may already exist, so a newer archive cannot replace an unattempted active snapshot.
- A claimed attempt without a terminal event remains in progress and blocks a new attempt.
- A failed attempt remains attached to the same active target and exact archive snapshot. Retry eligibility continues to follow the existing provider-delivery and manual-unlock safety rules.
- A successful sealed evaluation completes one frozen formula test, not the target's whole research lifetime. The same current target may therefore receive a later independent discovery package.
- A valid no-candidate outcome also completes a package without consuming the failed-attempt budget.
- Once a later canonical draw is observed, a terminal active package is recorded in history and only the newest archive snapshot is promoted. An unattempted, in-progress or failed target is not silently skipped; its exact snapshot remains pinned while the latest archive cursor is staged separately.
- Target retirement after exhaustion remains a separate explicit manual action. If a newer archive snapshot is staged, the operator must confirm its exact date/hash/count. If the exhausted target is still the latest draw, the successor fields are left empty; retirement releases the target and the scheduler waits for a future draw.

## Failed-attempt budget

The per-target limit is ten terminal `FAILED` attempts. Successful evaluations and `NO_ELIGIBLE_CANDIDATE` outcomes do not consume that budget. The total attempt history remains append-only, so a successful or no-candidate package is still auditable.

When ten failures have accumulated, the scheduler blocks further attempts even if an imported historical record has a later success marker. The target can only be released through the existing explicit exhausted-target retirement path, which verifies the exact active archive identity and the ten failed events.

## Hidden-target local evaluation

After the sealed historical evaluation has been persisted as `EVALUATED`, the coordinator may locally score the exact frozen forward specification against the one current hidden target. The target draw is read only inside the local evaluator. It is not added to discovery, training, cross-validation, model prompts or Telegram reports.

The immediate pre-target canonical draws are conditioning context only. They are not rescored as fresh validation targets, used to tune the frozen specification, or sent to a hosted model. The reverse specification remains retrospective-only and is never used to score the current target.

Only score-free receipt metadata is retained in the scheduler state’s `target_evaluations` list: sealed claim/attempt identifiers, target date, archive hash/count, forward hypothesis/specification identifiers, DSL version, six-candidate count, the fixed 0.600/6 R60 reference and completion timestamp. The state branch is publicly readable, so the aggregate hit count and every other target score are excluded from the receipt schema. Raw target numbers, candidate numbers, rankings and per-number matches are also rejected.

The receipt list is part of the existing autonomous scheduler state object and is publicly readable. It contains no score. The aggregate hit count exists only in the local evaluator process and its ephemeral workspace report; it is not printed, copied into model-readable research memory, uploaded as an artifact or sent to Telegram. The full local score report is excluded from uploaded GitHub Actions artifacts; the workflow prints only a number-free summary. The existing research memory remains available for GENERAL-origin formulas, synthetic tests and sanitized historical research feedback.

If a local target-scoring step fails after a sealed claim is already `EVALUATED`, the next retry first attempts to reconstruct the exact forward/reverse candidate pair from GENERAL’s validated research memory and the matching ledger claim. When that pair is unique and its specification hashes and synthetic tests match, local scoring resumes without a new model request or repeated sealed holdout scoring. If the exact pair is missing or ambiguous, the scheduler stops before provider discovery rather than substituting another formula.

## Preserved safeguards and unchanged limits

The change preserves the 2020-08-02+ 6/60 scope, true chronology, forward/reverse holdout separation, canonical adjacency checks, one-shot sealed claims, target isolation, the method-registry approval boundary and append-only research memory.

This PR did not perform a live provider or Telegram call. NVIDIA budget and
rate-limit policy is maintained independently in
`docs/autonomous-research-scheduler.md`.
