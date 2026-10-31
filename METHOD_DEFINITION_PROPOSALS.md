# GENERAL Super Loto — Method Definition Proposals

PROPOSAL ONLY — NOT CANONICAL

Purpose: Put three precise, testable working interpretations on the table without changing the method registry, starting formula discovery, or evaluating the real archive.

## Important semantic assumption

There is no approved mathematical meaning for the symbols in the repository yet. This proposal adopts one coherent interpretation solely to make the next discussion concrete:

- **A** is a number in a source draw.
- **B** is a number in the immediately following draw.
- **C** is a candidate number in the draw after B.
- **T** is a candidate number in the immediate next target draw.
- **MOD5** is the source number's remainder modulo five; multiples of five belong to residue class zero.

If A, B, and C were intended to mean hidden states rather than individual number identities, or T means something other than the next-draw target number, these proposals are the wrong semantics and must be revised before approval. No historical implementation or old formula result has been imported.

## Shared rules

Use draw sets D_i, each containing six distinct numbers. Work in separate regimes:

- **R54:** draw dates before 2020-08-02; universe U={1,...,54}.
- **R60:** draw dates on or after 2020-08-02; universe U={1,...,60}.

Never build a transition across the regime boundary. Transition methods also require complete consecutive-draw coverage; a list of sorted dates alone cannot prove that no draw is missing.

For every forecast target, the input consists only of validated draws strictly earlier than that target. The target and future rows are absent. Estimate each Bernoulli conditional with a fixed Beta(1,1) prior:

\[
P(Y=1\mid X)=\frac{\text{successes}+1}{\text{exposures}+2}.
\]

This prior is fixed for the proposal and is not tuned against validation or target outcomes. Rank the full active universe by score, descending, breaking exact ties by ascending number. The top six form an evaluation set, not a promise.

## 1. A→T — one-step number transition

For each adjacent historical draw pair D_i,D_(i+1), every source number a in D_i receives one exposure. If target number t appears in D_(i+1), count one success for (a,t).

\[
P(T=t\mid A=a)=\frac{N_{a,t}+1}{N_a+2}.
\]

For the latest known pre-target draw A, score each candidate target number by:

\[
S_{A\to T}(t)=\frac{1}{6}\sum_{a\in A}P(T=t\mid A=a).
\]

This gives each of the six antecedent numbers equal weight. Report exposure counts for the six current antecedents and identify zero-support cases.

## 2. MOD5→T — one-step residue-class transition

For every adjacent pair D_i,D_(i+1), each distinct residue class r represented in D_i receives one exposure. If t appears in D_(i+1), count one success for (r,t). Multiple antecedent numbers from the same residue class do not multiply that exposure.

\[
r(a)=a\bmod 5,\qquad r\in\{0,1,2,3,4\}.
\]

Residue zero contains 5, 10, 15, …, and 60 in R60. For the set R of distinct residues in the latest known pre-target draw:

\[
P(T=t\mid MOD5=r)=\frac{N_{r,t}+1}{N_r+2},
\qquad
S_{MOD5\to T}(t)=\frac{1}{|R|}\sum_{r\in R}P(T=t\mid MOD5=r).
\]

Report exposure counts for all five classes and identify the currently active classes.

## 3. A→B→C — two-step pair-conditioned transition

Use historical triples D_i,D_(i+1),D_(i+2). Each pair (a,b) in D_i x D_(i+1) receives one exposure. For each c in D_(i+2), count one success for (a,b,c).

\[
P(C=c\mid A=a,B=b)=\frac{N_{a,b,c}+1}{N_{a,b}+2}.
\]

For the two latest known pre-target draws A and B, there are 36 ordered number-pair contexts. Score each candidate c by the equal-weight average:

\[
S_{A\to B\to C}(c)=\frac{1}{36}\sum_{a\in A}\sum_{b\in B}P(C=c\mid A=a,B=b).
\]

Report how many of the 36 current contexts have historical support. Unseen contexts remain at the fixed smoothed prior and must not be hidden in the report. This method has a substantial sparsity risk, and the 36 contexts from a draw triple are dependent.

## Evaluation and rejection criteria

These are separate model candidates, not a combined prediction system. Do not blend their scores or tune weights at this stage. Compare each with:

1. Uniform random selection of six numbers from the active regime universe. Its theoretical expected overlap is 36/54 in R54 and 36/60 in R60.
2. The existing frequency-top-six, frequency-bottom-six, and adjacent-draw-overlap baselines.

A method should be rejected or left inconclusive if its advantage disappears across chronological folds, depends on sparse/unseen contexts, is unstable between R54 and R60, or does not survive an appropriate uncertainty and multiple-testing assessment. Reverse chronology remains retrospective stress testing only.

## Current status

- All three proposals are PROPOSAL_ONLY.
- The canonical registry remains unchanged: OPEN_DEFINITION, null canonical definitions, and execution_ready=false.
- No real archive rows or target numbers were supplied to the prototype.
- No real-archive discovery, tuning, scoring, or prediction was run.
- The next gate is your approval or correction of the assumed meanings and equations. After that, deterministic synthetic tests can be used to validate the implementation before any real-archive evaluation.
