# Safe Hypothesis Execution DSL v2

Status: implemented as an opt-in typed execution language. It does not approve a hypothesis, alter a method-registry entry, or change the project's research notation.

## Version compatibility

The existing DSL v1 evaluator and its schema remain unchanged. The execution-specification envelope emitted by the research runner still uses outer `schema_version: 1`; the nested `execution_spec.schema_version` selects DSL v1 or v2. Version dispatch validates and runs the matching engine. A v1 document is never silently upgraded.

## Direction-sealed state slots

The canonical archive is chronological. The sealed evaluator, not the model, selects the two adjacent archive rows exposed to a v2 expression.

- Forward walk-forward scoring of target C: A is the immediately preceding row before B; B is the immediately preceding row; C is the hidden target. The expression may read only A and B.
- Reverse retrospective scoring of target A: B is the first later row and C is the second later row. The expression may read only B and C. A remains hidden until the ranking is complete. This direction is retrospective stress testing only, never predictive evidence.

Every DSL v2 production entry point requires canonical_history; omission or an explicit null value fails closed. The pair-ranking API compares the supplied pair with the two immediately adjacent rows in that canonical context. Forward holdout requires every training row to match a canonical archive row, while the two rows used for scoring must equal the immediately preceding two canonical rows before that target. Older consumed rows may be omitted from the available training subset; no gap may be bridged in the actual A/B or B/C pair. Reverse evaluators bind B/C to the first two later canonical rows, require available history to be an exact archive subset, and skip consumed neighbours without bridging. The sealed evaluator supplies context from its validated immutable archive snapshot. Direct callers remain responsible for passing the authoritative archive, not a hand-picked subset. Calendar-day adjacency is not required; archive-row adjacency is.

Synthetic fixtures are isolated in test-only helpers and run through the explicitly named run_synthetic_contract_tests path. Production entry points do not infer or substitute a synthetic canonical history.

Each input DrawState is the set of six distinct integers in the active 1..60 pool. Derived set operations remain bounded by that 60-number universe. The interpreter has no source-code execution, imports, functions, loops, file access, or network access. AST limits are 64 nodes and depth 12.

## Typed AST

The root `score_expression` must return a number. Supported types are Number, Boolean, and DrawState.

- `state_ref`: reads one permitted slot.
- `set_union`, `set_intersection`, `set_difference`, `set_symmetric_difference`: bounded set operations over DrawState values.
- `contains_candidate`, `cardinality`: query a DrawState using the current candidate or return its size.
- Numeric and boolean operations: `const`, `var(candidate)`, `abs`, `bool_to_number`, arithmetic except modulo, comparisons, `and`, `or`, and `not`.

All operators require exact key sets and statically compatible operand types. Candidate ranking is deterministic: descending score, then ascending number for ties.

## MOD5 remains undefined

DSL v2 provides no built-in MOD5 projection, residue representation, scalar modulo operator, residue ordering, or aggregation. It does not select a remainder convention, position convention, or residue encoding on behalf of a hypothesis.

A MOD5-related proposal must state its exact mathematical definition independently. If that definition cannot be represented faithfully by the existing DSL nodes without adding a default or approximating its meaning, it remains proposal-only and its execution specification must be omitted. Do not work around this restriction by encoding an unstated convention in constants or expression structure.

## T and project notation

T is not a built-in DSL operator and has no fixed formula, direction, shape, or encoding. A proposal must state its exact definition; an executable v2 expression must represent that definition through its explicit AST subtree. A→B→C, A→T, and MOD5→T remain research labels, not approved equations or canonical methods. The DSL adds no default crossover interpretation or tuned weights.

Unsupported or ambiguous definitions remain proposal-only. Agents may not change governance rules or write to the method registry.

## Evaluation contract

Forward holdout and time-ordered cross-validation use the last two already-observed archive rows before each target. Earlier validation rows enter later folds only after their chronological turn. Reverse evaluation uses the first two later archive rows for each historical target and is labelled retrospective-only. The current isolated target and post-target rows are not passed into discovery or score expressions.

Synthetic contract tests use deterministic synthetic 6-of-60 fixtures only. Sealed evaluation still requires one separately frozen forward specification and one separately frozen reverse specification, disjoint fresh holdout windows, the existing minimum history, immutable archive snapshot/hash, and append-only ledger claim. The existing safe-catalog bidirectional diagnostic remains a separate baseline and does not implement or promote any A/B/C or MOD5 hypothesis.
