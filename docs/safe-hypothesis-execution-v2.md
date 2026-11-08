# Safe Hypothesis Execution DSL v2

Status: implemented as an opt-in typed execution language. It does not approve a hypothesis, alter a method-registry entry, or change the project's research notation.

## Version compatibility

The existing DSL v1 evaluator and its schema remain unchanged. The execution-specification envelope emitted by the research runner still uses outer `schema_version: 1`; the nested `execution_spec.schema_version` selects DSL v1 or v2. Version dispatch validates and runs the matching engine. A v1 document is never silently upgraded.

## Direction-sealed state slots

The canonical archive is chronological. The sealed evaluator, not the model, selects the two adjacent archive rows exposed to a v2 expression.

- Forward walk-forward scoring of target C: A is the immediately preceding row before B; B is the immediately preceding row; C is the hidden target. The expression may read only A and B.
- Reverse retrospective scoring of target A: B is the first later row and C is the second later row. The expression may read only B and C. A remains hidden until the ranking is complete. This direction is retrospective stress testing only, never predictive evidence.

The state-pair API accepts exactly two rows in chronological order and documents that its caller must bind adjacent rows from the canonical archive. The sealed forward and reverse evaluators construct those pairs from the validated, ordered archive. Calendar-day adjacency is not required; archive-row adjacency is.

Each input DrawState is the set of six distinct integers in the active 1..60 pool. Derived set operations remain bounded by that 60-number universe. The interpreter has no source-code execution, imports, functions, loops, file access, or network access. AST limits are 64 nodes and depth 12.

## Typed AST

The root `score_expression` must return a number. Supported types are Number, Boolean, DrawState, and ResidueView.

- `state_ref`: reads one permitted slot.
- `set_union`, `set_intersection`, `set_difference`, `set_symmetric_difference`: bounded set operations over DrawState values.
- `contains_candidate`, `cardinality`: query a DrawState using the current candidate or return its size.
- Numeric and boolean operations: `const`, `var(candidate)`, `abs`, `bool_to_number`, arithmetic, comparisons, `and`, `or`, and `not`.

All operators require exact key sets and statically compatible operand types. Candidate ranking is deterministic: descending score, then ascending number for ties.

## Explicit MOD5 views

`mod5_project` requires an explicit representation. The integer remainder convention is 0 through 4.

- `residue_set`: unique residues, sorted ascending.
- `residue_counts`: a fixed five-element vector; index 0 through 4 stores the multiplicity of that residue.
- `sorted_residues`: a fixed six-element sequence obtained by mapping the source draw's ascending numbers to residues. The mapped residues preserve source-number order and are not re-sorted.

`residue_contains` can inspect any ResidueView. `residue_frequency` is accepted only for `residue_counts`. `residue_at` is accepted only for `sorted_residues`, with index 0 through 5. No representation is inferred or silently selected.

## T and project notation

T is not a built-in DSL operator and has no fixed formula, direction, shape, or encoding. A proposal must state its exact definition; an executable v2 expression must represent that definition through its explicit AST subtree. A→B→C, A→T, and MOD5→T remain research labels, not approved equations or canonical methods. The DSL adds no default crossover interpretation or tuned weights.

Unsupported or ambiguous definitions remain proposal-only. Agents may not change governance rules or write to the method registry.

## Evaluation contract

Forward holdout and time-ordered cross-validation use the last two already-observed archive rows before each target. Earlier validation rows enter later folds only after their chronological turn. Reverse evaluation uses the first two later archive rows for each historical target and is labelled retrospective-only. The current isolated target and post-target rows are not passed into discovery or score expressions.

Synthetic contract tests use deterministic synthetic 6-of-60 fixtures only. Sealed evaluation still requires one separately frozen forward specification and one separately frozen reverse specification, disjoint fresh holdout windows, the existing minimum history, immutable archive snapshot/hash, and append-only ledger claim. The existing safe-catalog bidirectional diagnostic remains a separate baseline and does not implement or promote any A/B/C or MOD5 hypothesis.
