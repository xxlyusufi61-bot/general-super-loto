# Guarded one-time NVIDIA budget ledger migration

The manual workflow `.github/workflows/general-nvidia-budget-migration.yml` is the only supported persistence path for the known schema-v2 to schema-v3 budget-ledger conversion.

## Two explicit modes

- `preflight` is the default. It validates the dedicated GitHub App, exact App permissions, installation-token repository scope, the active budget-branch Ruleset, and a pinned ledger conversion preview. It does not commit or push.
- `migrate` requires the exact approval input `I_APPROVE_PERSIST_NVIDIA_BUDGET_V2_TO_V3`. It repeats every preflight check in the same serialized run before it can write.

## Writer isolation

- The global `GENERAL_SCOPED_WRITERS_VALIDATED` gate is not changed or enabled.
- Before that global gate is enabled, the token action permits one narrow exception only: the exact manual migration workflow on `main`, with the configured NVIDIA Budget Writer client ID. All other pre-validation writer operations remain blocked.
- The workflow obtains a short-lived token for only `general-super-loto` and requests only `Contents: write`.
- A read-only `GITHUB_TOKEN` with `Administration: read` inspects Ruleset `27814116`. The App's own signed JWT is used to inspect the App's declared permissions. The preflight requires the Ruleset's sole Integration bypass actor to equal the App ID, the App slug to be `general-nvidia-budget-writer`, and the exact App permissions to be `contents: write` plus default `metadata: read`.
- The installation token must expose exactly one repository: `xxlyusufi61-bot/general-super-loto`.
- The Ruleset must be active and protect only `general-nvidia-rate-limit-state`, with exactly deletion, update, and non-fast-forward protections.

## Ledger and concurrency guarantees

- The workflow shares the existing `general-nvidia-api` concurrency group with NVIDIA reservation work.
- The budget branch head is fetched and pinned before conversion. The converter and validator are copied from the trusted `main` checkout before switching to the state branch, preventing stale or missing code on the state branch from being executed.
- Only exact known V2 formats (72/day or 200/day with their historical allocations and package cap 4) are accepted.
- The complete rolling request history, daily reservation history, reservation IDs, package IDs, and diagnostic run IDs are preserved. V3 policy is 1,500/day with allocations 750/225/150/225/150, and the legacy package cap is removed.
- Unknown, malformed, duplicate-key, or invalid history fails closed.
- The workflow stages only `general_nvidia_rate_limit.json`. A concurrent branch advance causes a normal fast-forward push to fail; it never force-pushes or retries against a changed head.
- A valid V3 ledger is an idempotent no-op.

## Execution boundary

Merging the code does not run the migration. A preflight dispatch is read-only with respect to repository state. Persisting V3 requires a separate manual dispatch in `migrate` mode with the exact approval phrase. Neither mode sends NVIDIA API requests or starts research, and neither changes runtime state, authorization, the global lock, or the global writer gate.
