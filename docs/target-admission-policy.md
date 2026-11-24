# GENERAL target admission and insufficient-window handling

This policy defines when a canonical Super Loto archive snapshot may become the
active FIFO target. It does not alter formula families, research protocols,
archive scope, or target-isolation rules.

## Admission

1. The newest canonical archive is staged as the queue's latest snapshot.
2. The staged snapshot is materialized independently and checked with the current
   sealed-evaluation preflight.
3. Admission requires the current partition version, at least 40 discovery
   rows, at least two fresh forward validation targets, at least two fresh
   reverse validation targets, disjoint directional windows, and all target
   isolation flags set to false.
4. A snapshot that fails only a known insufficient-fresh-window condition stays
   staged and is not assigned as active. A later, newer canonical archive may
   be checked against the same append-only consumption history.
5. An already-active snapshot remains pinned. It is never replaced by a newer
   archive while it has recoverable research work.

## Safe ineligible terminal outcome

An active snapshot may be closed as
NOT_EVALUABLE_INSUFFICIENT_FRESH_WINDOW only when all of the following hold:

- The exact active date, immutable snapshot hash, and record count match.
- Current sealed preflight returns one of the enumerated insufficient-window
  errors. Malformed data, ledger corruption, identity mismatch, or any unknown
  error is not classified as insufficiency.
- The target has no attempt history, sealed claim, or local target-evaluation
  receipt.
- No target numbers, candidate predictions, or evaluation scores are produced.
- The closure does not add an attempt, terminal event, retry, or consumed
  validation date.

Targets with any attempt, claim, or local evaluation evidence are preserved for
recovery or explicit review. The new outcome is not equivalent to
NO_ELIGIBLE_CANDIDATE, FAILED, or EVALUATED.

## Research boundaries

- The isolated target and all later archive rows remain excluded from discovery
  and training.
- Chronological A→B→C, A→T, and MOD5→T research constraints are unchanged.
- Forward walk-forward validation remains the only forward evidence.
- Reverse-time research remains retrospective stress testing, not forward
  predictive evidence.
- Cross-validation remains required; no automatic method promotion or selection
  is introduced.
- Old user/manual/Pydroid formula reports are not imported into GENERAL agent
  memory. GENERAL's own validated learning remains append-only.
- The admission maintenance command is offline-only and makes zero NVIDIA
  requests. A state change is published only through the scoped automation
  state writer.

## Operational entry points

- Scheduled autonomous research invokes target maintenance before offline
  discovery or provider work.
- The manual GENERAL Sealed Target Admission Maintenance workflow performs
  the same offline operation and requires the exact dispatch confirmation
  I_APPROVE_SEALED_TARGET_ADMISSION_MAINTENANCE_NO_NVIDIA.
