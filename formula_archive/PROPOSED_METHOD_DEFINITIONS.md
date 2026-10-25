# Proposed mathematical definitions — synthetic testing only

**Version:** 0.1.0  
**Status:** Experimental proposals, not canonical definitions.  
**Registry:** `general_method_registry.json` is intentionally unchanged.

## Shared rules

All three functions receive chronologically ordered rows and a separate `target_date`. Only rows strictly earlier than that date contribute any draw numbers to scoring. Test fixtures include deliberately invalid target/future number fields to detect accidental access. The archive must be validated by Data Guard before these proposals are used. A test passing only establishes implementation behavior on the tests; it does not establish predictive ability.

Insufficient history returns `no_signal` and no candidate numbers. The algorithms do not fill missing results with low numbers or other arbitrary values. All positive-score candidates are sorted by score descending and number ascending.

## 1. A→B→C — transition similarity predicts the following C

Input history: (D_0, D_1, \ldots, D_m), all before the isolated target. The current observed transition is (A^*=D_{m-1}\rightarrow B^*=D_m).

For every historical triple (D_i=A_i, D_{i+1}=B_i, D_{i+2}=C_i), construct the 11-feature V8.6 transition signature:

[
\Phi(A,B) = (\text{posdiff},\text{gapdiff},\text{parityflip},\text{bandmove},
\text{mod3diff},\text{mod5diff},\text{mod7diff},\text{sumdiff},
\text{span\_diff},\text{shape\_diff},\text{signed\_core})
]

The duplicate `rawdiff` feature is omitted because it equals `posdiff`. Transform each component by sign ((-1,0,1)); compute Hamming distance from the current transition. Sort historical triples by distance ascending and, on ties, more recent historical start index first. Select at most (K=5) triples. Each selected triple at rank (r), starting at 1, contributes (1/r) to every number in its (C_i) state.

**Important:** this fixes the legacy direction error for this proposal. The old engine voted on B, but the name A→B→C requires predicting the next C. The proposal remains disabled until explicitly approved.

Minimum history: 3 pre-target draws.

## 2. A→T — empirical source-number/next-draw co-occurrence

Let (D_0,\ldots,D_m) be the pre-target history and (A^*=D_m) the latest observed source draw. For each fully historical transition (D_i\rightarrow D_{i+1}), count:

[
M[x,y] = \sum_{i=0}^{m-1} \mathbf{1}[x\in D_i]\mathbf{1}[y\in D_{i+1}]
]

For every (x\in A^*), choose the up-to-ten **observed** target numbers (y) with the largest (M[x,y]); ties are resolved by ascending (y). Add (M[x,y]) to (y)'s score. Rank positive scores descending, then ascending number.

This preserves the legacy `gen_pair_relation` top-10 behavior. It remains a co-occurrence hypothesis, not a claim that a source number causes a target number.

Minimum history: 20 pre-target draws.

## 3. MOD5→T — residue-profile nearest transitions

Define the modulo-5 state of a draw (D) as a five-count histogram:

[
\operatorname{MOD5}(D)=(c_0,c_1,c_2,c_3,c_4),\quad
c_r=|\{n\in D:n\bmod 5=r\}|
]

For every historical transition (D_i\rightarrow D_{i+1}), compute its source-profile L1 distance from (operatorname{MOD5}(D_m)), where (D_m) is the latest pre-target source. Select up to five nearest transitions; ties are resolved in favor of the more recent source. Each selected next-draw set contributes one vote to every number it contains.

This is a **new explicit proposal**, not a reinterpretation that pretends the old `mod5diff` feature was already MOD5→T.

Minimum history: 6 pre-target draws.

## Synthetic tests included

The test suite verifies deterministic directionality on repeating toy sequences, target/future-number invariance (the hidden number fields contain intentionally invalid sentinels), insufficient-history behavior, draw integrity, stable output ordering, and that the canonical registry remains unchanged.

## What a successful test does not mean

It does not prove an edge over random 6/60 draws. If these definitions are later approved, the next separate stage is leakage-free forward walk-forward, reverse retrospective stress testing, chronological cross-validation, and an appropriate random/null baseline with uncertainty. No real-archive scoring or live NVIDIA calls are performed by this stage.
