# GENERAL Automation State Branch

This branch is the registered storage replica for explicitly allowlisted GENERAL runtime state.

## Current status

- Store status: **ACTIVE**, generation 1.
- Registered objects: 4 of 5 allowlisted objects.
- Data source: `main` at commit `56a7318c3d50aff924255550e321505044b87ce8`.
- Migration method: one atomic state-branch commit; source object bytes preserved exactly.
- Operational authority: **`main` remains authoritative**.
- Reader/writer cutover: not performed. The existing state reader is used only for GET-only shadow comparison; no operational writer has been redirected.
- Global NVIDIA lock: remains on `main`, locked, and is not copied here.
- Shared NVIDIA 40-requests-per-minute ledger: remains on `general-nvidia-rate-limit-state` and is not copied here.

The manifest's `ACTIVE` status means that the four objects below are registered and integrity-bound. It does not mean that this branch has become the operational source of truth.

## Registered objects

| Manifest key | Path | Scope |
|---|---|---|
| `archive` | `super_loto_archive.json` | Canonical archive; target isolation remains mandatory |
| `evaluation_ledger` | `general_evaluation_ledger.json` | Sealed evaluation ledger |
| `autonomous_research_state` | `general_autonomous_research_state.json` | Scheduler state; currently paused |
| `research_memory` | `general_research_memory.json` | GENERAL-origin sanitized research memory |

The allowlisted `nvidia_lock` object is deliberately deferred. It must remain on `main` until every global-lock reader and writer can be switched together without creating two competing authorities.

## Integrity and change-control contract

1. Never place API keys, tokens, credentials, or other secrets in this branch.
2. Only the fixed allowlist in `general_automation_state.py` may be registered.
3. Each manifest entry binds the exact Git blob SHA, SHA-256 digest, byte length, generation, and UTC update timestamp.
4. Manifest updates must use optimistic concurrency against the expected generation. A stale writer must stop and reload.
5. A migration publishes all selected objects and the manifest in one commit. Partial object publication is not an accepted state.
6. Readers must fail closed on missing objects, malformed JSON, hash/length mismatch, unknown schema, unknown object keys, unexpected files, or wrong branch identity.
7. The global NVIDIA lock remains fail-closed on `main`; it must never be silently dropped, copied stale, or downgraded.
8. Archive and sealed-evaluation material must remain isolated from model prompts. Moving storage does not grant permission to expose targets, holdouts, raw draw rows, or sealed data.
9. This branch is not an access-control boundary or a privacy boundary. The repository is public; this branch only separates storage organization. It must not bypass code review or change project research rules.
10. No NVIDIA/provider request is required or permitted for state migration, bootstrap, or offline validation.

## Migration record

- Source branch: `main`
- Source commit: `56a7318c3d50aff924255550e321505044b87ce8`
- State-store generation: 1
- Registered objects: archive, evaluation ledger, paused autonomous research state, GENERAL research memory
- Exclusions: global NVIDIA lock, separate shared rate-limit ledger, legacy/manual memory, method registry, sealed snapshot directory
- Main remains authoritative until a separately reviewed reader/writer cutover.

