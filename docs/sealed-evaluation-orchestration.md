# GENERAL bidirectional sealed evaluation orchestration v0.3

## Purpose and boundaries

The latest archive draw is the isolated current target. Its number set and any record on or after its date are excluded from discovery, training, candidate selection, validation, and scoring. The archive is validated by Data Guard before any split. The exact archive bytes are hashed and copied to a content-addressed snapshot before a claim is evaluated.

The discovery agent receives two views of the same discovery-only rows: one ordered oldest-to-newest and one newest-to-oldest. These are two independent discovery directions. Candidate hypotheses must explicitly label their discovery direction as forward, reverse, or both; forward and reverse discoveries/results are not silently combined.

A successful one-shot claim requires one safe DSL specification matching the first eligible forward/both proposal and one matching the first eligible reverse/both proposal. If either directional candidate is missing, mismatched, unrepresentable, or fails synthetic preflight, evaluation is skipped. The evaluator may not select an alternative after validation outcomes are seen. A proposal/spec is not a method registry entry or proof of a mechanism.

The labels A→B→C, A→T and MOD5→T remain open method families unless their exact mathematical definitions have been formally approved. The generic safe catalog described below is a separate exploratory baseline and must not be treated as silently defining those methods or any proposal in open PR #59.

## Regimes and disjoint windows

R54 and R60 are validated and reported separately. The boundary remains 2020-08-02; number pools or scores are not pooled across the boundary.

For R60, the pre-target history is split into three chronological, non-overlapping pieces:
- Reverse holdout: up to 30 oldest eligible pre-target rows.
- Discovery: only the middle rows after the reverse holdout and before the forward holdout.
- Forward holdout: up to 30 newest pre-target rows.

The two holdouts are sized symmetrically and do not overlap. A minimum of 40 middle discovery rows plus at least two rows for each holdout is required; the minimum is never silently reduced. With 80 pre-target R60 rows, for example, the split is 20 reverse / 40 discovery / 20 forward. With a longer archive, each directional holdout is capped at 30 rows and the remainder stays in discovery. Dates/counts may be shown in the public plan; draw-number sets may not.

R54 remains discovery-only in this orchestration version, preserving its existing restriction. The independent per-cutoff safe-catalog diagnostic can still report each R54 direction separately for the rows where enough direction-appropriate history exists. That diagnostic does not authorize a R54 AI-selected holdout claim.

## Candidate freeze and safe DSL

The research agent can propose at most five textual hypotheses. The strict proposal parser requires a directional label. The agent can emit at most two execution specs: the first eligible forward/both proposal and the first eligible reverse/both proposal. One both-direction proposal may cover both directions with one spec. There is no post-validation substitution. Every spec is checked by the restricted JSON expression DSL and deterministic synthetic R54/R60 tests before any claim.

The DSL executes no model-provided Python, code, imports, file/network access, or dynamic functions. A DSL-valid expression is still not canonical, does not change the registry, and is not evidence of predictive ability. If a hypothesis cannot be represented faithfully by the DSL, its spec must be omitted; meanings of A→B→C, A→T and MOD5→T are never approximated silently.

## Forward candidate evaluation and two-way chronological testing

The frozen forward candidate is tested on the newest sealed R60 forward holdout. Each row is ranked using only the discovery rows and prior validation rows that had already occurred before that row. The report includes one line per test date with training count, hit count, and matched top-six column positions 1 through 6; it never exposes either the target number set or the actual predicted number values.

The frozen reverse candidate is tested on the oldest, disjoint R60 reverse holdout. For each test date, ranking uses only strictly later historical draws. Rows are evaluated newest-to-oldest. The report again records date, hit count and matched top-six column positions without revealing target numbers or predicted number values. Because later draws are used for this direction, this result and its blocked reverse-time folds are retrospective stress tests only—not evidence of predicting future draws.

Time-ordered cross-validation is reported separately for the forward interval and the reverse interval. The folds reuse their respective sealed interval, so fold results are correlated summaries and are not independent confirmation. No shuffling is performed. Mean overlap must be compared with the uniform-random expectation of 36/N hits per six-number set (0.6 for R60); scores alone cannot establish a mechanism.

A separate, pre-registered GENERAL-SAFE-DSL-CATALOG-v1 baseline replays each eligible historical target independently in both directions across the full available history. For each forward target, selection/scoring uses only earlier draws; for each reverse target, selection/scoring uses only later draws and is marked retrospective-only. The catalog is fixed in source before viewing archive outcomes, is independent of the AI-selected candidates, and contains six explicitly named generic scoring baselines. This full-history baseline is not an implementation/approval of A→B→C, A→T or MOD5→T. Replaying the same historical window on future runs is a reproducibility audit, not a newly untouched independent holdout.

## Per-draw reports and interpretation

The per-draw details record:
- direction, date and frozen/canonical candidate identifier and SHA-256;
- the number of rows in the direction-appropriate reference history;
- hit count from zero to six;
- the one-based position(s) among the six selected columns that matched.

Reports omit target number sets, predicted number values and full candidate rankings. They include separate forward/reverse summaries and per-regime summaries, tested date ranges, hit distributions, uniform-random expectations, hashes, protocol limits, and explicit caveats. No candidate is automatically promoted, no method registry status changes, and no formula is called proven.

The append-only ledger stores both directional holdout dates, candidate/specification hashes, archive snapshot hash, and evaluation status. A date consumed by either holdout in either direction cannot be reused as a fresh validation date in a later claim. The claim and snapshot must be committed before scores are calculated. If evaluation later fails, the claimed dates remain consumed; the system fails closed.

## Billing and operational safeguards

Unit tests and offline preflight do not send hosted inference requests or score the real archive. A live hosted call remains blocked unless the existing exact free-only/no-billing confirmation, independently verified free-use status, and global NVIDIA safety lock all pass. No paid service, credit-card-required setup or automatic billing transition is authorized. A provider rate limit is not proof of free allowance. The evaluator makes no additional hosted request.

## Known limits

- The direction-specific AI one-shot protocol is available only in R60; R54 remains discovery-only for the AI candidate test in this version.
- The generic full-history baseline is a fixed, bounded catalog, not an exhaustive search across every possible formula.
- The restricted DSL cannot faithfully express every conceivable state transition/crossover rule. Unsupported semantics remain untested rather than approximated.
- The reverse direction is inherently retrospective because it uses later data. It must never be combined with forward results as evidence of future prediction.
- Lottery draws are uncertain; no prediction guarantee or advantage claim is made.
