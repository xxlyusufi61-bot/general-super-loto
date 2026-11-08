# GENERAL Automation State Branch

This branch is the authoritative source of truth for GENERAL's four migrated runtime-state objects. Runtime workflows hydrate these objects from this branch and publish validated updates back to it.

## Current authority and branch roles

- Operational authority: **`general-automation-state`** for the four registered runtime objects below.
- Store status: **ACTIVE**. The current generation and exact object versions are defined by `manifest.json`; the generation is expected to advance as approved state updates are published.
- Runtime readers use the verified state-store snapshot. Runtime writers publish through the transactional state writer with optimistic-generation checks.
- Copies of these four files on `main` are legacy/historical comparison snapshots only. They are not authoritative and must not be used as runtime inputs, research data, archive data, or evaluation state.
- `main` remains the source-code and workflow branch. Immutable sealed evaluation snapshots remain on `main` and are not part of this mutable state-store contract.
- The global NVIDIA safety lock remains on `main` and is not copied here. Its readers and writers must continue to use the main-branch lock contract.
- The shared NVIDIA 40-requests-per-minute ledger remains on `general-nvidia-rate-limit-state` and is not copied here.

## Registered objects

| Manifest key | Path | Scope |
|---|---|---|
| `archive` | `super_loto_archive.json` | Canonical draw archive; target isolation remains mandatory |
| `evaluation_ledger` | `general_evaluation_ledger.json` | Sealed evaluation ledger |
| `autonomous_research_state` | `general_autonomous_research_state.json` | Scheduler and authorization state |
| `research_memory` | `general_research_memory.json` | GENERAL-origin sanitized research memory |

The allowlisted `nvidia_lock` object is deliberately not registered. It must remain on `main` until every global-lock reader and writer can be switched together without creating two competing authorities.

## Integrity and change-control contract

1. Never place API keys, tokens, credentials, or other secrets in this branch.
2. Only the fixed allowlist in `general_automation_state.py` may be registered.
3. Each manifest entry binds the exact Git blob SHA, SHA-256 digest, byte length, generation, and UTC update timestamp.
4. Manifest updates must use optimistic concurrency against the expected generation. A stale writer must stop and reload.
5. A state publication commits all selected objects and the manifest atomically. Partial object publication is not an accepted state.
6. Readers must fail closed on missing objects, malformed JSON, hash/length mismatch, unknown schema, unknown object keys, unexpected files, or wrong branch identity.
7. The global NVIDIA lock remains fail-closed on `main`; it must never be silently dropped, copied stale, or downgraded.
8. Archive and sealed-evaluation material must remain isolated from model prompts. Storage location does not grant permission to expose targets, holdouts, raw draw rows, or sealed data.
9. This branch is not an access-control or privacy boundary. Storage separation does not bypass code review or change project research rules.
10. No NVIDIA/provider request is required or permitted for state migration, bootstrap, documentation, or offline validation.

## Migration and cutover record

The four runtime objects were initially copied from `main` into this branch, then the coordinated workflow cutover made this branch authoritative. The initial migration commit is historical context only; the current `manifest.json` is the sole source for present object versions and generation.

The following exclusions remain intentional: global NVIDIA lock, shared NVIDIA rate-limit ledger, legacy/manual memory, method registry, source code/workflows, and immutable sealed evaluation snapshots.
