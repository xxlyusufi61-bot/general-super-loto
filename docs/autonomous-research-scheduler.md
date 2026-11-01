# GENERAL Autonomous Research Scheduler v0.1

## Purpose

The scheduler reuses the existing blank-slate research runner, safe JSON DSL,
sealed evaluation ledger, and Telegram reporting. It does not replace or loosen
their validation rules.

A scheduled check runs every six hours. It may start at most one research cycle
for a newly published latest draw date. If the archive target date has not
advanced, that date has already been attempted, the archive fails Data Guard,
or fresh disjoint holdout windows are unavailable, the cycle stops before any
NVIDIA inference request.

## Authorization and NVIDIA safety

- Automation is disabled by default in general_autonomous_research_state.json.
- Activation requires a distinct exact phrase, a fresh manual free-only/no-billing
  attestation, and an authorization period of 1, 7, or 30 days.
- Activation is rejected while the existing global NVIDIA safety lock is active.
  The separate manual unlock workflow remains the only way to clear that lock.
- A successful activation authorizes at most two NVIDIA requests for one new
  target date: one forward discovery request and one independent reverse request.
- The scheduler does not retry an uncertain request. Existing failure handling
  persists the global lock after an incomplete or invalid provider request.
- No automatic unlocking, paid conversion, card-required service, or billing
  transition is permitted. A 402/429 or uncertain provider response is not retried.
- The provider's account quota is not queried by this controller. Free-use
  verification is an explicit operator attestation, not an inferred quota result.

The manual activation action does not itself call NVIDIA. It only records the
expiring authorization. The next scheduled check may run only after the global
lock is separately clear.

## Target, archive, and holdout rules

- Only validated 6-of-60 archive rows dated 2020-08-02 or later are accepted.
- The latest draw remains isolated. The target numbers are never passed to either
  discovery request or to the evaluator.
- Forward and reverse model requests remain independent and direction-labelled.
- The sealed evaluator reserves disjoint forward and reverse holdouts and records
  consumed dates in its existing append-only ledger. The scheduler never changes
  those dates or silently reduces minimum history requirements.
- A target date is reserved in the scheduler's append-only attempt list before
  the first hosted request. That date is not automatically retried after failure.
- A target already present in the sealed evaluation ledger is not run again by
  the scheduler, including a cycle previously started manually.

## Reporting and limits

On a completed evaluation, Telegram receives separate forward and reverse
messages containing candidate IDs/hashes, test dates, hit counts, and matched
top-six column positions. Target number sets, predicted number values, and full
candidate rankings are omitted. Reverse results are labelled retrospective only.

The scheduler checks GitHub Actions every six hours; GitHub does not guarantee
that scheduled jobs start at an exact minute or that a hosted runner is always
available. This is recurring remote automation, not a permanently running
server process.

A successful scheduler run does not prove lottery predictability. The canonical
method registry remains unchanged; unsupported hypotheses remain untested
rather than being approximated.
