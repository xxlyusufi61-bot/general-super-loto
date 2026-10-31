# GENERAL Super Loto — Research notation and binding governance

**Status:** Active research reference  
**Purpose:** Explain the notation and rules that GENERAL and its agents must follow.  
**Fresh-start boundary:** This document contains no previously developed formula, implementation, experiment, score, weight, or historical result.

## 1. Research notation

These symbols are research labels, not approved equations or executable formulas.

| Symbol | Meaning for new research | What is deliberately not fixed |
|---|---|---|
| A | The earlier draw/state in a chronological research sequence. | No inherited feature vector, encoding, or formula. |
| B | The draw/state immediately following A when a consecutive sequence is being studied. | No inherited transition calculation or scoring rule. |
| C | The draw/state immediately following B in the same chronological sequence. | No inherited triple-matching or candidate-voting algorithm. |
| T | A neutral name for a transition or derived representation that a new hypothesis explicitly defines. | No fixed mathematical definition, direction, shape, or encoding. |
| MOD5 | A modulo-five view of draw values used as a research concept. | No residue convention, position ordering, aggregation, or transition formula is preselected. |

### Research-family labels

- **A→B→C:** Investigate a relationship across three consecutive chronological draw states A, B, and C.
- **A→T:** Investigate a newly defined relationship between A and a specifically defined T representation.
- **MOD5→T:** Investigate a newly defined relationship between a MOD5 representation and a specifically defined T representation.

The arrow (→) means “relationship to investigate.” It is not an equation, a claim that a relationship exists, or approval of a prediction method. The labels stay OPEN_DEFINITION until a complete new definition is independently proposed, tested, reviewed, and explicitly approved.

Each new hypothesis must define every symbol, input, transformation, output, parameter, tie-break rule, and evaluation window. It must not borrow a formula or interpretation from previous project experiments.

## 2. Binding research rules

1. Keep the target draw hidden during discovery, training, feature construction, model selection, and tuning.
2. Use only records strictly earlier than the isolated target for predictive evaluation.
3. Validate archive structure, values, date uniqueness, chronology, and target partition before analysis. Stop if any required check fails.
4. Keep R54 and R60 separate. The R60 boundary is 2020-08-02. Any cross-regime comparison must be explicitly identified and tested separately.
5. Use leakage-free forward walk-forward evaluation and time-ordered cross-validation.
6. Reverse chronology is permitted only as a separately labelled retrospective stress test; it is never predictive evidence.
7. Use deterministic synthetic tests before real-archive evaluation.
8. Separate discovery/selection data from a frozen chronological evaluation interval. Do not repeatedly tune against the final evaluation interval.
9. Compare methods with an appropriate random/null baseline and report uncertainty, search limits, and unsuccessful tests. Mean hit count alone is not proof.
10. Preserve reproducibility: exact definitions, code versions, inputs, test fixtures, evaluation boundaries, and results must be recorded.
11. Never invent archive data, mathematical definitions, experiments, results, or test status. Clearly distinguish hypotheses, unverified claims, and verified findings.
12. A method is not canonical, executable, or approved merely because an agent proposes it. Promotion requires explicit user approval.
13. Never promise a winning lottery prediction. A valid research outcome may be that no robust signal is found.

## 3. Fresh-start separation

GENERAL and its agents must begin formula discovery without inherited knowledge of the user's earlier formula implementations or experiments.

They must not load, reuse, reconstruct from, or treat as evidence:

- previously developed formula algorithms or code;
- previous formula test runs, candidate rankings, scores, or performance claims;
- tuned weights, thresholds, parameters, or selection decisions from earlier experiments;
- old formula engines, historical candidate catalogues, proposal scorers, or prior formula reports.

The agents may use the notation in Section 1, the binding rules in this document, the current blank method registry, the validated archive under the target-isolation rules, and results produced by their own fresh-start research after the fresh-start reset.

The symbols and research protocol are retained; earlier formulas and experimental conclusions are not.

## 4. Rule-change requests through Telegram

An agent may recommend adding, revising, or removing a binding project rule when it can explain why the current rule blocks sound research, harms test quality, or prevents progress.

A request must include the exact current rule (or NONE for a proposed addition), the proposed wording or the explicit action REMOVE, evidence and its limitations, risks, required tests, and a rollback plan.

Requests are sent to the authorized Telegram approval flow. An approval records permission for a separate implementation review; it does not edit a rule, method registry, or code automatically. A reviewed pull request and explicit user approval are required before a permanent rule changes. Rejected requests leave the rules unchanged.

## 5. Service and execution boundaries

- Use free resources only. Do not make a live hosted-model request unless free-use and no-billing status have been independently verified for that request.
- If free usage cannot be verified, stop before the network request. Rate limits or a trial label alone do not prove that usage is free.
- Do not use services requiring a payment card or risk automatic paid conversion.
- Do not use Pydroid for this project.
- Prefer offline, deterministic tests whenever they can answer the question.
- A successful GitHub Actions workflow proves only the checks actually executed in that run. A Telegram heartbeat is not proof that agents are continuously reasoning or that a formula works.
