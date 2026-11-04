# GENERAL Automation State Branch

This branch is a dedicated state-store namespace for future GENERAL automation-state migration.

## Current status

- Status: **BOOTSTRAP ONLY**
- Manifest schema: v1
- Generation: 0
- Registered state objects: 0
- Operational readers/writers: none
- Real project files migrated: none

The branch tip intentionally contains only this document and `manifest.json`. It is a separate data ref; `main` remains the source-code and review branch.

## Safety contract

1. Do not place API keys, tokens, credentials, or other secrets in this branch.
2. Only explicitly allowlisted state objects may be added by a future migration.
3. Every object must be recorded in the manifest with its Git blob SHA, SHA-256 content digest, byte length, and generation.
4. Manifest updates must use optimistic concurrency against the expected generation. A stale writer must stop and reload; it must not overwrite newer state.
5. A write must be validated before publishing the manifest pointer. Incomplete writes must remain unreferenced and must not be treated as committed state.
6. Readers must fail closed on missing objects, malformed JSON, hash/length mismatch, unknown schema, unknown object keys, or unexpected branch identity.
7. The NVIDIA global lock must remain fail-closed. Lock updates must never be silently dropped or downgraded during migration.
8. Archive and sealed-evaluation material must remain isolated from model prompts. Moving storage does not grant permission to expose targets, holdouts, raw draw rows, or sealed data.
9. This branch is not a substitute for PR review on `main`. It must not be used to bypass code review or change the research rules.
10. No live provider call is required or permitted for bootstrap or offline validation.

## Registered state-object identifiers

The v1 code contract defines a fixed allowlist. The manifest starts empty. Objects are not registered until a separately reviewed migration step has verified their source, destination, byte-for-byte digest, schema, chronology, and target-isolation properties.

## Change control

The branch is initialized from the reviewed `main` commit but its current tree is intentionally sparse. Changes to its manifest and state objects require deterministic validation and generation-based concurrency checks. Do not force-push this branch.
