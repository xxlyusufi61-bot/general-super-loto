# One-time recovery for the 2026-11-12 FIFO target

## Scope and status

This is a narrowly scoped manual recovery route for the exact failed autonomous
workflow attempt on the 2026-11-12 FIFO target. It is not a general retry policy,
does not reactivate the retired Cycle #55 route, and does not rewrite the
original terminal event.

The original attempt remains permanently recorded as
`FAILED / WORKFLOW_FAILED`:

- Target: `2026-11-12`
- Archive: 984 canonical rows
- Archive SHA-256:
  `e85b8b5de47355b83f2c070f9ffbfe8dc97541ec52b93a849c3e285f776d781e`
- Prior attempt:
  `503c5ea28d5ce30d1dd20a0f35b1d0f754e9bd09d9559bd06941d0497998b57d`
- Prior workflow run: `44040345546`
- Failure event time: `2026-11-17T07:50:58.480457+00:00`
- Failure reason: `WORKFLOW_FAILED`

## Dispatch guard

The route is available only through `workflow_dispatch` on `main`, from
actor `xxlyusufi61-bot` (actor ID `345811504`), using this exact confirmation:

`GENERAL_RECOVER_2026_11_12_WORKFLOW_44040345546_ONCE`

It requires the existing, unexpired free-only authorization and a clear
NVIDIA global safety lock. It cannot activate authorization, clear a lock, or
override a failed authorization/lock check.

## Preflight and claim

Before reserving an attempt, the workflow verifies all of the following:

1. The archive is byte-identified by the pinned SHA-256 and has 984 rows.
2. The exact target remains the immutable FIFO head.
3. The exact prior attempt is still the final attempt and its terminal event
   remains the pinned `FAILED / WORKFLOW_FAILED` event.
4. Exactly five attempts exist for this target; the next attempt is number six.
5. No sealed-ledger entry exists for this target, including a pending claim.
6. The current sealed preflight succeeds using the current adjacency-aware
   partition implementation, with disjoint forward/reverse holdouts, fresh
   canonical neighbours, target isolation, and the fixed discovery/validation
   minimums.
7. The prior one-time scoped recovery journal is empty.

The preflight is repeated immediately before the claim. Only then does the
workflow append a new attempt and one consumed entry to
`scoped_recovery_history`. It does not edit the prior attempt/event, move the
FIFO, modify the Cycle #55 `manual_retry_history`, or change standard retry
classification.

## After the one-time recovery

The newly claimed attempt is ordinary attempt six under the existing ten-attempt
per-target limit. It proceeds through the existing forward/reverse research,
sealed evaluation, memory, state-store, and reporting gates. If this new attempt
ends as `WORKFLOW_FAILED`, the regular scheduler still blocks automatic retry.
The special authorization cannot be replayed.

The journal stores only attempt/run identifiers, chronology, actor/workflow
provenance, partition metadata, counts and cryptographic digests. It does not
store draw-number sets, candidate number values, rankings or per-draw hits.

## Operational boundary

CI and unit tests use offline fixtures only. Merging this PR does not dispatch
the recovery workflow. A later, separate user-initiated workflow dispatch is
required to use this route. No NVIDIA request, Telegram message, lock change or
runtime state-store write is performed by PR validation.
