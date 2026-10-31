# GENERAL safe hypothesis execution — DSL v0.1

**Status:** Restricted execution prototype; not a canonical lottery method.  
**Purpose:** Let a new hypothesis be expressed as validated data rather than executable source code.

## 1. Security model

The executor interprets a closed JSON expression tree. It does not accept or run Python, shell commands, imports, plugins, network requests, file paths, dynamic evaluation, or arbitrary functions. Unknown keys and operators fail closed.

A validated expression is not an automatically approved method. It does not modify the method registry, promote A→B→C, A→T, or MOD5→T, or permit an agent to change project rules.

## 2. Specification shape

An execution specification has exactly two top-level fields:

- schema_version: integer 1
- score_expression: one numeric expression tree

Every expression node is a JSON object with an allowlisted op. Extra fields are rejected.

### Literal and variable nodes

- const with a finite numeric value; absolute value at most 100000.
- var with name candidate — the candidate number being scored.
- var with name value — one historical number while the aggregate iterates a prior draw.
- var with name draw_age — zero for the most recent prior draw, increasing toward older draws.

value and draw_age are permitted only inside the history aggregate.

### Numeric operators

Binary operators: add, sub, mul, div, mod, min, max.  
Unary operator: abs.

Division by zero, non-positive or non-integer modulo divisors, non-finite results, and out-of-range scores stop execution.

### Comparison and boolean operators

Comparison operators: eq, ne, lt, lte, gt, gte.  
Boolean operators: and, or, not.

The validator checks expression types before any evaluation.

### Exact JSON node keys

Each node must contain exactly the keys listed here; extra keys are rejected:

- const: op, value
- var: op, name
- abs or not: op, arg
- any binary operator: op, left, right
- aggregate_history_numbers: op, aggregate, expression

The top-level execution spec contains exactly schema_version and score_expression. A research-run item contains exactly hypothesis_id and execution_spec.

### Historical aggregate

Exactly one aggregate_history_numbers node is required:

- aggregate count requires its expression to return a boolean.
- aggregate sum requires its expression to return a number.

The aggregate visits the numbers in the supplied earlier-draw history. Its expression can use candidate, value, and draw_age. Nested aggregates are prohibited.

## 3. Fixed safety limits

- The only supported number pool is 1–60; every input record and cutoff date must be on or after 2020-08-02.
- At most 32 expression nodes and depth 8.
- At most 64 prior draws per candidate-ranking operation.
- At most 30 holdout draws per evaluation call.
- At most 10000 discovery-history rows are accepted as input.
- A score must be finite and have absolute value no greater than 1,000,000,000.
- Ties are broken by ascending number.
- One fixed specification is evaluated; the engine never tunes parameters or selects a winner automatically.

## 4. Chronological holdout interface

evaluate_holdout receives two separate, strictly chronological inputs:

1. discovery_history, which must end before the validation interval begins.
2. validation_draws, which must all be strictly earlier than the isolated current target date.

For each validation draw, candidate scores are calculated only from the earlier discovery rows and validation rows that would already have occurred by that date. The current validation draw is used only after ranking, to count overlap. The current isolated target and all later draws are rejected by the date boundary.

The executor rejects all pre-2020-08-02 dates and all number pools other than 1–60. No earlier-period data is accepted by this engine.

The report contains aggregate hit statistics, a standard error, the uniform-random reference 36/N, and a hash of the exact specification. It omits per-draw target values and candidate rankings.

evaluate_time_ordered_cv splits the same separate validation interval into contiguous time-ordered blocks, without shuffling. Earlier validation draws may enter the training history of later folds only after their dates have passed.

evaluate_reverse_retrospective_stress starts at the newest testable pre-target draw and moves backward toward older draws. Each score uses only later historical rows from the same regime as its reference history. Its output is always marked EVALUATED_RETROSPECTIVE_ONLY with predictive_evidence false. It is a diagnostic only and never predictive evidence.

## 5. Interpretation and limits

This v0.1 DSL can express only score functions based on one aggregate over prior draw numbers. It does not yet express every possible hypothesis involving whole draw states, linked state sequences, crossover, or more complex transformations. A proposal outside this grammar must remain PROPOSAL_ONLY; it must not be approximated silently or translated into arbitrary code.

The engine unit tests use deterministic synthetic fixtures. This PR does not run new candidate specifications against the real archive, does not call a hosted model, does not change the canonical method registry, and does not claim a predictive result. Before real-archive evaluation, a separate orchestration step must withhold a non-overlapping chronological validation interval from the research agent, prevent repeat use of the same holdout, preserve R54/R60 separation, and record reproducible provenance.

A validated DSL specification is not evidence of predictive power. Mean overlap alone is not proof, and no lottery outcome is guaranteed.
