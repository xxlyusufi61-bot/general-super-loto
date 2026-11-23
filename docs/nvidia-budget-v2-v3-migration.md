# One-time NVIDIA budget ledger migration

The manual workflow `.github/workflows/general-nvidia-budget-migration.yml` is the only supported one-shot persistence path for the known schema-v2 to schema-v3 budget-ledger conversion.

## Safety contract

- Dispatch is accepted only from `main` with the exact approval input `I_APPROVE_PERSIST_NVIDIA_BUDGET_V2_TO_V3`.
- The workflow uses the dedicated NVIDIA Budget Writer token for the sole write to `general-nvidia-rate-limit-state`.
- The repository's existing scoped-writer deployment gate remains mandatory. This workflow does not bypass or modify it.
- The workflow is serialized with all other NVIDIA request/reservation work through the existing `general-nvidia-api` concurrency group.
- Only the known exact v2 ledger shapes (72/day or 200/day, with their historical category allocations and package cap 4) are accepted by the existing canonical validator.
- The converter preserves the complete rolling request history, daily reservation history, reservation IDs, package IDs, and diagnostic run IDs. It changes only the schema/policy fields to v3: 1,500/day with allocations 750/225/150/225/150, and removes the legacy package cap.
- Unknown, malformed, duplicate-key, or invalid history fails closed.
- A valid v3 ledger is an idempotent no-op. A concurrent branch advance causes the normal fast-forward push to fail; the workflow never force-pushes or retries against a changed head.
- The workflow makes no NVIDIA API request, starts no research, and does not touch runtime state, authorization, global lock, or the scoped-writer gate.

## Development and execution boundary

Pull-request CI tests synthetic known-v2, invalid-v2, history-preservation, and already-v3 cases. They do not read or mutate the production ledger.

A successful PR merge only installs the workflow and converter. The real migration occurs only when an operator separately dispatches the workflow with the exact approval input. The workflow's run summary records the migration status and reservation-history digest without exposing reservation payloads.
