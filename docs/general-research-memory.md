# GENERAL internal research memory v0.1

## Purpose

This file documents the dedicated, persistent memory used only by GENERAL's blank-slate Super Loto formula-research runner. It allows later research cycles to learn from GENERAL's own earlier hypotheses, definitions, failed approaches, structured learning notes, safe aggregate evaluation feedback, and failure metadata.

The memory begins empty. It is not backfilled from existing Actions artifacts, generic agent memory, old reports, or any earlier manual formula experiment.

## Hard provenance boundary

The memory writer accepts only entries with the exact source `GENERAL/open-research` and research epoch `general-open-research-memory-v1`. Every entry includes a direction, target-date metadata, archive hash, runner version, report hash, hypothesis/specification hashes, a cycle identifier, and an entry content hash.

The system never imports or reads the user's pre-GENERAL Pydroid/manual formula work, its code, definitions, parameter choices, tests, scores, rankings, or reports. The new research runner does not read `general_memory.json`; that separate shared-agent memory is not a source for formula discovery. Existing Actions artifacts are not automatically imported.

The checksums detect accidental or unauthorized content changes relative to the stored values. They are integrity checks, not digital signatures proving who authored a record. The trusted write path is the repository-controlled research workflow.

## Direction separation

Forward and reverse research have distinct memory contexts:
- A forward request receives only prior `forward` entries.
- A reverse request receives only prior `reverse` entries.
- The context is bounded to at most six recent eligible entries and 18 KB.
- Only entries whose stored target date is strictly earlier than the current isolated target are eligible.
- A context entry's claims are explicitly unverified. The model must verify relevant claims using its current direction's permitted discovery history.

No forward candidate list, report, or memory item is passed to reverse discovery, and vice versa. Crossover remains subject to its separate staged evaluator and existing restrictions.

## Stored information

Each direction may retain:
- Validated structured hypothesis definitions and their direction-namespaced IDs.
- A single matching validated execution specification, if the proposal parser and restricted DSL accepted one.
- A hash of the full directional report, without retaining the raw report in this memory file.
- Bounded structured learning notes: observations, failed approaches, and useful next tests.
- Safe aggregate evaluation summaries, when the append-only evaluation ledger confirms the claim reached `EVALUATED`.
- Sanitized failure stage/type metadata when a request, parsing step, claim, or evaluation did not complete.

The record may include target-date metadata, the archive hash, and the discovery-window date range/count. It does not copy archive rows, draw-number sets, prediction values, individual holdout dates, per-draw hit records, or full candidate rankings.

## Consumed validation feedback

PR #63 remains unchanged:
- A draw date consumed by either sealed directional holdout stays excluded from all later discovery and fresh holdouts.
- The raw consumed draw rows are never placed in the model's later discovery context.
- The exact consumed dates remain authoritative only in the append-only evaluation ledger and immutable archive snapshot mechanism.
- Memory may retain only safe aggregate metrics and the direction-specific interpretation, with `holdout_consumed: true`.
- Such feedback is development history only. It is never a fresh independent validation result and must not be used to select the first frozen candidate for the current one-shot claim.
- Reverse metrics are always marked `retrospective_only: true`; they are not predictive evidence.

The memory writer checks that evaluation feedback matches the candidate/specification frozen for that direction and that the evaluation report is backed by an `EVALUATED` ledger entry for the current archive and target. If an evaluation report exists locally but the ledger is still only `CLAIMED`, its scores are discarded from memory. The claim remains consumed under the existing ledger rules.

## Persistence and immutability

`general_research_memory.json` is append-only. Existing entries cannot be edited, reordered, or deleted by the memory writer. Duplicate run/direction entries are rejected. Each entry has a SHA-256 content hash. The file has a bounded safety limit of 10,000 entries; when that limit is reached, writes fail closed rather than deleting history.

Both the manual blank-slate workflow and the scheduled autonomous workflow use the same memory writer after the research/evaluation outcome is known. The workflow commits only `general_research_memory.json`; it does not rewrite the archive, evaluation ledger, method registry, global NVIDIA lock, or autonomous authorization state as part of the memory step.

## Verification boundaries

The offline test suite covers:
- Empty initial state and absence of legacy imports.
- Strict source/epoch and direction validation.
- Rejection of number-value sets in stored hypotheses/notes/context.
- Entry-hash integrity and append-only persistence.
- Same-direction context selection and rejection of cross-direction memory.
- Binding evaluation feedback to the correct frozen candidate/specification.
- Consumed-holdout markers and retrospective-only reverse results.
- Exclusion of per-draw data and holdout date lists from memory.

Offline tests do not make hosted NVIDIA requests and do not claim a formula has a predictive advantage.

## Public repository notice

This repository is public. Persistent memory records, hypotheses, definitions, aggregate results, and failure metadata committed here are visible to repository visitors. The memory format intentionally excludes raw draw rows and predicted number values, but its research content is still public.
