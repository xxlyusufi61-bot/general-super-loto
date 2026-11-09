# GENERAL offline mathematical hypothesis discovery v0.1

## Purpose and execution boundary

general_offline_hypothesis_discovery.py is a bounded, deterministic symbolic-discovery and offline-evaluation runner. It makes no hosted-model, NVIDIA, Telegram, or other network request. It does not use Pydroid and does not import earlier manual/Pydroid formula work.

The scheduled autonomous workflow runs this step against only the oldest FIFO target snapshot, before the independent authorization/global-lock gate for hosted research. Materializing the snapshot changes only the job workspace. The offline runner reads the sealed evaluation ledger and does not update it, the canonical archive, the FIFO state, the NVIDIA lock, or autonomous authorization.

This first implementation is not an unrestricted AI inventing arbitrary mathematics. It enumerates a bounded, versioned symbolic search grammar of up to sixteen explicit safe-DSL candidates. Broader model-assisted proposal generation remains a separate provider-authorized activity.

## Research families and candidate definitions

The global method registry remains untouched. A→B→C, A→T, and MOD5→T remain OPEN_DEFINITION; these candidates are research proposals only.

- A→B→C: four Boolean membership transitions over three consecutive archive draws: present/present/present; present/present/absent; absent/present/present; present/absent/present. A candidate is scored by the count of matching consecutive triples.
- A→T: exact historical frequency; exact frequency within the ten most recent available earlier draws; distance at most one; same parity; and same modulo-ten remainder.
- MOD5→T: same positive-integer modulo-five remainder across all prior available draws, and the same residue class within the ten most recent available earlier draws.

Each candidate is a fully specified JSON AST in the safe DSL. The bounded grammar includes three recent-window lengths, two neighbourhood radii, and explicit transition/residue patterns. Its proposal text is generated locally from that AST and must match the separately validated execution specification exactly. The DSL evaluator never executes Python, dynamic code, shell commands, file access, or network operations.

Modulo semantics are local to the corresponding candidate: positive values from 1 through 60 use the ordinary non-negative remainder operation. This does not install a global MOD5 convention or close the registry's open definition.

## Chronology, isolation, and evaluation

1. Validate the FIFO-materialized archive using the existing sealed archive/Data Guard path. The latest draw remains isolated as the target; only the sealed pre-target discovery partition is available.
2. Exclude every validation date already consumed by the sealed evaluation ledger and every development date recorded in the offline consumption ledger.
3. Retain each source archive row's original ordinal as sequence_index. A→B→C transitions count only when the three source rows have consecutive indices. Missing/removed draws are never bridged.
4. Enumerate and formally freeze the candidate ASTs using only the earlier training prefix. At least forty training draws are required. A training-only score-vector screen removes expressions that assign an identical score to every number in that prefix; only candidates with at least two distinct score values and a positive score range proceed. The screen records aggregate variation metadata, never number rankings or values. The resulting candidate set is therefore conditioned on the actual training archive, while validation and target data remain unavailable.
5. Evaluate the fixed candidates on the next ten fresh chronological development draws using forward walk-forward and five contiguous time-ordered CV folds. No shuffling or target/future row is permitted.
6. Run the reverse-time diagnostic separately on pre-target discovery rows. It is labelled retrospective only and never counts as forward predictive evidence.
7. Compare means with the uniform-random 6-of-60 expectation of 0.6 hits out of 6 and retain uncertainty metadata where the evaluator supplies it. This is a reference baseline, not a proof of statistical significance.
8. Do not select a candidate, tune it on the same development interval, promote it, or modify the method registry. Walk-forward and CV reuse the same frozen development interval and are not independent confirmation.

Every evaluated development interval is consumed. Its exact dates are retained only in the dedicated offline consumption ledger so they cannot be reused; the candidate memory entry stores only the window count and a SHA-256 digest. These dates are not passed into later hypothesis-generation context. Future runs need a strictly later fresh interval. Existing sealed holdout consumption remains separately authoritative in general_evaluation_ledger.json.

## Offline GENERAL memory

Offline results live in a dedicated offline_research namespace inside the existing general_research_memory.json research_memory state object. This preserves the four-object state-store contract without adding a fifth object or branch. The namespace is created only after a successful offline evaluation and stores GENERAL-origin candidate definitions, specification hashes, safe aggregate walk-forward/CV/reverse summaries, archive/target provenance, and a digest of the consumed development window.

It does not store raw archive rows, draw-number sets, target numbers, per-draw hit records, candidate rankings, or exact development-date lists inside the hypothesis entries. A separate consumption sub-ledger retains exact development dates solely to enforce non-reuse. Duplicate JSON keys, invalid hashes, non-monotonic dates, duplicated windows, modified prefixes, and candidate/spec mismatches fail closed. Existing entries cannot be edited or deleted.

The existing sealed directional entries remain governed by their sealed-claim/ledger writer and are immutable to the offline writer. The combined root uses a schema-v3 envelope; schema-v2 files are upgraded in memory to an empty offline namespace and are only persisted through the existing generation-guarded research_memory state-store writer. Offline appends cannot change sealed entries.

The offline namespace is published only through the existing dedicated general-automation-state branch and the four-object transactional state writer as part of the existing research_memory object. It is not committed directly to main and does not add a fifth state object. Candidate definitions and aggregate results remain visible to readers of that protected state object; raw draw values and rows are excluded.

After a successful append, the scheduled workflow sends a separate number-safe Telegram summary. It includes the bounded DSL expressions, candidate IDs, training-only score-variation metadata, forward walk-forward/CV means, and reverse retrospective means. It never sends target draw values, predicted-number values, per-draw hit positions, or candidate rankings. The offline-specific formatter validates the formal DSL/proposal hash and research flags before sending; each family is a separate message and messages are rate-spaced.

## Interpretation and status

A successful cycle reports COMPLETED_EXPLORATORY_ONLY. It is not a verified formula, winning-number prediction, or evidence of a repeatable advantage. The only accepted outcome is a reproducible, bounded exploratory measurement, including a null/unsuccessful result. Canonical approval still requires separate review and explicit user approval.
