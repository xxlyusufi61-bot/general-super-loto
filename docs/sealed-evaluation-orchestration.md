# GENERAL sealed evaluation orchestration v0.1

## Purpose and boundaries

The discovery agent must receive only the discovery partition. The current latest draw is isolated by Data Guard and is excluded from both discovery and evaluation. The sealed evaluator is a separate deterministic program; it never calls NVIDIA or executes model-supplied source code.

The three user-authorized method labels remain open definitions. A DSL-valid score expression is not a canonical method and cannot edit or promote the method registry. Hypotheses that cannot be represented faithfully by the restricted DSL remain proposal-only.

## Fixed split

The archive is validated before splitting. Rows are sorted chronologically and kept in separate R54 and R60 partitions; the regime boundary remains 2020-08-02. The latest row is the isolated target. For each regime, a separate validation interval is taken from the most recent pre-target rows only when enough history remains for the default 40-draw initial training requirement plus at least two validation draws. The maximum reserved interval is 30 draws.

R54 currently has fewer than 42 pre-target rows, so it remains discovery-only and its status is `NOT_EVALUABLE_INSUFFICIENT_HISTORY`. The minimum training requirement is not silently reduced. R60 uses its own discovery rows and a separate validation interval. Regime histories are never pooled by the scoring engine.

The model receives discovery rows, not the validation rows. The public split plan records dates, counts, hashes, and statuses only; it has no draw-number sets.

## Candidate freeze

The model orders at most five textual proposals using discovery data only. For the one-shot real-archive evaluation, it may provide at most one DSL specification and that specification must match the first proposal. If zero or multiple specifications are emitted, or the specification does not match the first proposal, real-archive evaluation is skipped rather than selecting an alternative after the fact.

The fixed candidate is validated again and run through synthetic R54/R60 contract tests before a claim is created. Synthetic tests are not evidence of predictive value.

## One-shot claim, snapshot, and ledger

Before any real-archive score is calculated, the orchestrator:
1. checks that the validation dates have not been consumed by an earlier claim;
2. writes an append-only ledger entry containing the target cutoff, archive and specification hashes, validation dates, protocol settings, and candidate ID;
3. writes an immutable content-addressed copy of the exact archive bytes used by the claim; and
4. requires the workflow to commit the ledger and snapshot to the repository before evaluation can run.

The ledger does not store draw-number sets or per-draw scores. A claimed interval is considered consumed even if a later step fails, which intentionally fails closed. Any later interval that overlaps a consumed date is rejected before the hosted research request is made. A fresh interval is required before another evaluation can run.

The evaluation runner reads the immutable snapshot and verifies the archive hash, target cutoff, validation dates, frozen candidate hash, and persisted claim ID. It refuses a missing, altered, duplicated, or already evaluated claim.

## Reports

A valid claim runs three separately labelled diagnostics for one frozen specification:

- **Forward chronological walk-forward:** each draw in the reserved, recent validation interval is scored with only earlier discovery rows and validation rows that would already have occurred by that date. The interval is capped at 30 draws by the current engine limit.
- **Blocked time-ordered cross-validation:** contiguous blocks of that same validation interval; no shuffling. It reuses the same sealed interval, so fold summaries are correlated and are not independent confirmation.
- **Full available reverse retrospective stress test:** the engine evaluates every R60 pre-target historical row that has at least 40 later rows available, batching at most 30 test rows per call and checking adjacent batches for gaps or overlap. It proceeds newest-to-oldest, using later historical draws as reference history. The result is explicitly retrospective and is never predictive evidence.

The report includes the exact uniform-random expectation (36/N hits per six-number selection), mean overlap and standard error, hashes, date ranges, protocol and search limits. It excludes per-draw target values and candidate rankings. It does not automatically choose a winner, alter registry status, or claim a proven mechanism.

This orchestration does not currently evaluate R54 because the archive does not meet the default 40-draw training minimum plus a separate validation interval; even if R54 grows, this version keeps it discovery-only. The forward confirmatory report covers the reserved recent interval only. It does not claim a full 2020→latest leakage-free forward replay: selecting a hypothesis after seeing later discovery data and then scoring it on earlier rows would leak selection information. A future full historical forward replay must re-run or freeze hypothesis discovery separately at each historical cutoff rather than mislabel such scores as independent evidence. It also does not define or execute every whole-state, A→B→C, A→T, MOD5→T, or crossover hypothesis; unsupported semantics must not be approximated silently.

## Billing and operational safeguards

Automated tests and preflight use no hosted-model requests. The live research workflow remains behind the existing explicit free-only/no-billing confirmations and global safety lock. The sealed scoring step makes no additional hosted request. A failed or overlapping preflight stops before the research request.
