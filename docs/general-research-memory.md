# GENERAL internal research memory v0.3

## Purpose

This file documents the dedicated, persistent memory used only by GENERAL's blank-slate Super Loto formula-research runner. Later cycles may use the safe text-only narrative portions of GENERAL's own earlier reports, validated hypotheses and definitions, failed approaches, structured learning notes, safe aggregate evaluation feedback, and failure metadata. Narrative retention is intentionally conservative: free-form narrative containing any Arabic digit is omitted. Structured text is separately screened for numeric and written-out candidate values, partial rankings, and holdout dates written with English or Turkish month names.

The memory begins empty. Its schema-v3 root contains the original sealed directional entries plus a separate offline_research namespace. It is not backfilled from existing Actions artifacts, generic agent memory, old reports, or any earlier manual formula experiment.

## Hard provenance boundary

The memory writer accepts only entries with the exact source `GENERAL/open-research` and research epoch `general-open-research-memory-v2`. Every entry includes a direction, target-date metadata, archive hash, runner version, the source report hash, sanitized narrative and its content hash when safe, hypothesis/specification hashes, a cycle identifier, and an entry content hash. Schema v2 upgraded the empty v1 store. Schema v3 adds only the offline_research namespace; an existing schema-v2 document is upgraded in memory with an empty offline namespace and is persisted only by a later legitimate generation-guarded write. The sealed directional entry format and provenance remain unchanged.

The system never imports or reads the user's pre-GENERAL Pydroid/manual formula work, its code, definitions, parameter choices, tests, scores, rankings, or reports. The new research runner does not read `general_memory.json`; that separate shared-agent memory is not a source for formula discovery. Existing Actions artifacts are not automatically imported.

The checksums detect accidental or unauthorized content changes relative to the stored values. They are integrity checks, not digital signatures proving who authored a record. The trusted write path is the repository-controlled research workflow.

## Direction separation

Forward and reverse research have distinct memory contexts:
- A forward request receives only prior `forward` entries.
- A reverse request receives only prior `reverse` entries.
- The context is bounded to at most six recent eligible entries and 18 KB.
- Full hypotheses and specifications remain stored in the append-only memory file. The prompt context includes up to the two highest-priority hypotheses per prior record with field truncation explicitly marked, plus the validated specification, learning notes, safe aggregate feedback, and provenance metadata.
- Eligible entries may have the same target date as the current run, but only when they were created by an earlier run; entries with a later target date are excluded.
- A context entry's claims are explicitly unverified. The model must verify relevant claims using its current direction's permitted discovery history.
- Memory records are carried only in the user-role JSON payload under `prior_general_research_memory_untrusted`. The system message contains policy instructions but never embeds report text or serialized memory records. The model is instructed to treat the field as untrusted data, not commands or governance.

No forward candidate list, report, or memory item is passed to reverse discovery, and vice versa. Crossover remains subject to its separate staged evaluator and existing restrictions.

## Stored information

Each direction may retain:
- Validated structured hypothesis definitions and their direction-namespaced IDs.
- A single matching validated execution specification, if the proposal parser and restricted DSL accepted one.
- The safe text-only narrative portion of the directional report, with a source-report SHA-256 and a separate hash for the stored sanitized narrative. Structured proposal/specification/learning-note blocks are extracted into their validated fields; structured rule-change requests are omitted because they use a separate approval path.
- Narrative storage fails closed when it contains a six-number set, any date in the current sealed forward or reverse validation windows (even before those dates are written to the evaluation ledger), any previously consumed validation date, secret-like text, malformed structured-block boundaries, any Arabic digit, or text exceeding the report-size limit. Numeric narrative is omitted with the status `OMITTED_NUMERIC_CONTENT`; sensitive-date/secret/number-set findings use the sensitive-content omission status. No omitted text is copied into memory.
- Structured proposals and learning notes are separately checked for explicit single candidate/predicted values and partial rankings in Arabic numerals and common English/Turkish number words, as well as complete six-number sets written with numeric or word-form values. The check is applied again when stored entries are loaded, so editing the JSON and recomputing its hash cannot bypass it. Restricted execution-spec DSL constants are validated by their own schema and are not treated as narrative predictions.
- Bounded structured learning notes: observations, failed approaches, and useful next tests.
- Safe aggregate evaluation summaries, when the append-only evaluation ledger confirms the claim reached `EVALUATED`.
- Sanitized failure stage/type metadata when a request, parsing step, claim, or evaluation did not complete.

The record may include target-date metadata, the archive hash, and the discovery-window date range/count. It does not copy archive rows, draw-number sets, prediction values, individual holdout dates, per-draw hit records, API credentials, or raw structured rule-change requests. The exact sealed validation windows are used transiently for redaction; their dates are not written into memory. Before any directional memory write, the writer verifies that the plan's target date, archive SHA-256, and archive record count match the loaded archive, then reconstructs the chronological partition using the claim's immutable prior-consumption snapshot (or the current ledger when no claim exists). Any mismatch or altered window metadata fails closed.

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

`general_research_memory.json` is append-only within the existing research_memory state object. The sealed directional entries cannot be edited, reordered, or deleted by either writer. Offline symbolic discoveries and their consumed-development ledger are isolated under offline_research and are appended only by the offline validator. The regular sealed-memory writer verifies that this namespace is unchanged before it writes. Duplicate run/direction entries are rejected. Each entry has a SHA-256 content hash. The file has a bounded safety limit of 10,000 entries; when that limit is reached, writes fail closed rather than deleting history. New entries must preserve non-decreasing target-date chronology. The writer validates only the newly appended suffix against the final persisted target date; the existing prefix is validated on load and is not rechecked as if it were new data.

The manual blank-slate and scheduled autonomous workflows publish the research_memory object through the existing generation-guarded four-object state-store writer. The offline runner updates only the offline_research namespace within this object; it adds no fifth object, branch, or direct main-branch data push. It may also stage an archive file that the preceding validated archive-refresh step already produced, so the memory entry's archive hash points to a durable source archive. The memory writer itself never edits archive rows, the method registry, the global NVIDIA lock, or autonomous authorization state.

## Offline symbolic namespace

The offline_research namespace is owned by the bounded local symbolic runner described in docs/general-offline-hypothesis-discovery.md. It stores only GENERAL-origin safe DSL candidates, specification hashes, aggregate forward walk-forward/CV summaries, reverse-retrospective summaries, archive/target provenance, and a digest of each consumed development window. Exact development dates are held only in its separate non-reuse sub-ledger; they are never copied into hypotheses or later prompt context. It cannot edit the sealed directional entries, evaluation ledger, method registry, archive, or state-store object list.

## Verification boundaries

The offline test suite covers:
- Empty initial state and absence of legacy imports.
- Strict source/epoch and direction validation.
- Rejection of complete number-value sets and explicit single candidate values/partial rankings in structured hypotheses and learning notes.
- Entry-hash integrity and append-only persistence.
- Same-direction context selection and rejection of cross-direction memory.
- Binding evaluation feedback to the correct frozen candidate/specification.
- Consumed-holdout markers and retrospective-only reverse results.
- Exclusion of per-draw data and holdout date lists from memory.
- Sanitization of safe same-direction text-only narratives, including reserved validation-window dates before ledger consumption, previously consumed dates, secrets, number sets, Arabic-digit narrative, malformed blocks, and size-limit rejection.
- Fail-closed rejection of incomplete, reversed, malformed, or oversized reserved validation windows.
- Detection of numeric and English/Turkish word-form number sets, explicit single candidate values and partial rankings, plus English/Turkish month-name dates in structured text.
- Sealed-plan binding to the exact archive SHA-256, target date, record count, target-isolation flags, and archive-derived directional partition; claimed cycles are checked against the immutable prior-consumption snapshot.
- Validation of candidate-value and partial-ranking rejection both before persistence and when loading stored entries.
- Same-direction memory data placed in a user-role JSON field, with tests proving that report text never appears in the system message and cross-direction memory is absent.

Offline tests do not make hosted NVIDIA requests and do not claim a formula has a predictive advantage.

## Public repository notice

This repository is public. Persistent memory records, hypotheses, definitions, aggregate results, and failure metadata committed here are visible to repository visitors. The memory format intentionally excludes raw draw rows and predicted number values, but its research content is still public.
