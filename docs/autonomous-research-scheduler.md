# GENERAL Autonomous Research Scheduler v0.1

## Purpose

The scheduler reuses the existing blank-slate research runner, safe JSON DSL,
sealed evaluation ledger, and Telegram reporting. It does not replace or loosen
their validation rules.

A scheduled check runs every six hours. Its first job refreshes and validates
the canonical 6-of-60 archive independently of AI authorization and NVIDIA
lock state; this archive-only job makes no model request. The research job then
checks the explicit authorization and global lock and may start at most one
research cycle for a newly published latest draw date. If the archive target
date has not advanced, that date has already been attempted, the archive fails
Data Guard, or fresh disjoint holdout windows are unavailable, the cycle stops
before any NVIDIA inference request.

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

## Global NVIDIA request-rate protection

Every live NVIDIA call from GENERAL Core, individual agents, coordinator execution,
blank-slate research, and the two direct API diagnostic workflows reserves a slot
through the same shared ledger before the HTTP request is sent. The ledger is stored
on the separate `general-nvidia-rate-limit-state` branch so rate accounting does not
change the canonical archive, evaluation ledger, or `main` branch.

The guard enforces a rolling limit of at most 40 reservations in any 60-second
window across the repository's shared NVIDIA key. It reads the current safety lock
and initializes a dedicated Git worktree for the ledger, then records each request
reservation by a normal fast-forward Git push before the provider call. This avoids
a per-request GitHub REST Contents API read/write pair and its hourly/content-write
quotas. The existing `general-nvidia-api` concurrency group serializes all six
provider workflows; a non-fast-forward push or other Git failure blocks the
provider request instead of overwriting a reservation. A 30-second reservation
timestamp safety margin accounts for the bounded state-operation time. When all
40 slots are occupied, GENERAL waits for an expired slot rather than sending a
41st request. A missing, malformed, unreadable, or unwriteable ledger blocks the
provider call. Failed and uncertain provider attempts still consume their
reserved slot; no automatic provider retry is performed.

The global lock has one narrowly scoped exception: the dedicated
`.github/workflows/nvidia-diagnostic.yml` may reserve exactly one forward-only
diagnostic request while the lock remains active. Its preflight and provider
reservation both query the GitHub Actions run API and require the canonical
repository, the fixed diagnostic workflow ID and path, `workflow_dispatch`,
the `main` branch, the exact one-request approval phrase, matching run/commit
identity, an in-progress run, and `run_attempt=1`. The job token has only the
`actions: read` permission needed to verify that run. A runner environment
flag alone never authorizes the exception.

The diagnostic run ID is permanently recorded in
`diagnostic_run_ids` in the same shared rate-limit ledger commit that reserves
its 40-per-60-second slot. That one-shot history survives expiry of the rolling
rate window, so a second reservation or workflow rerun using the same run ID is
rejected. Normal GENERAL and agent requests continue to use the ordinary
`check` path and remain blocked by an active global lock. The diagnostic
exception does not edit or clear `general_nvidia_lock.json`; every reservation
still requires the lock to be active and the shared rate-limit push to succeed.

The state branch is operational safety data and must not be deleted or merged into
`main`. The 40-per-60-second accounting covers requests routed through this
repository's shared GENERAL NVIDIA key; it cannot observe manual calls or other
projects that use the same NVIDIA account/key outside this repository. Any such
external consumer must use the same central limiter to be included in this count.
Git transport remains subject to GitHub's normal repository/abuse controls; any
fetch or push failure blocks the corresponding NVIDIA request.

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
