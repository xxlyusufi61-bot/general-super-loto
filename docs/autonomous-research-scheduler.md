# GENERAL Autonomous Research Scheduler v0.2

## Purpose

The scheduler reuses the existing blank-slate research runner, safe JSON DSL,
sealed evaluation ledger, and Telegram reporting. It does not replace or loosen
their validation rules. A target work package is finite: directional discovery,
proposal validation, applicable chronological evaluation, memory/reporting, and
an auditable terminal outcome. Empty but valid research results are scientific
outcomes, not technical failures.

A scheduled check refreshes and validates the canonical 6-of-60 archive
independently of AI authorization and NVIDIA lock state. The research job checks
explicit authorization and the global lock before any hosted call. It activates
one target in chronological order; a newer archive target is held while the
previous target has unfinished work or a recoverable no-send failure. The
scheduler does not expose target values to discovery.

## Authorization and NVIDIA safety

- Automation remains disabled by default in general_autonomous_research_state.json.
- Activation requires the exact authorization phrase, a fresh manual free-only/no-billing
  attestation, and an authorization period of 1, 7, or 30 days.
- Activation is rejected while the existing global NVIDIA safety lock is active.
  The separate manual unlock workflow remains the only way to clear that lock.
- The former target-wide two-request cap is removed. All requests from all GENERAL
  agents now share the same provider reservation ledger.
- Shared rolling budget: at most 72 reservations in any 24-hour window.
- Normal operating ceiling: 30 reservations in any 60-second window. Immutable
  hard ceiling: 40 reservations in any 60-second window.
- Every workflow run package may reserve at most four requests.
- Daily category allocations are forward discovery 40, forward critique 12,
  reverse retrospective diagnostics 8, between-draw research 8, and controlled
  retry reserve 4. The allocations total exactly 72 and are enforced separately.
- Each call's requested completion limit is at most 8,192 tokens. This is a
  response-token ceiling, not a promise that the model will use that many tokens.
- The rolling daily budget is an internal safety ceiling, not a provider free-tier
  entitlement. The provider's account quota is not queried by this controller.
  Free-use and no-billing verification remains an explicit operator attestation.
- A provider request is reserved before network access. Failed and uncertain
  submissions consume the reservation. SDK automatic retries remain disabled.
- A target may be retried only when the workflow proves that no provider request
  started. Such retries are bounded to three attempts total per target (initial
  attempt plus at most two no-send retries), and use the dedicated four-call
  daily retry reserve. Any sent, partial, or uncertain request blocks automatic
  retry and keeps the global fail-closed lock behavior.
- No automatic unlocking, paid conversion, card-required service, or billing
  transition is permitted. A 402/429 or uncertain provider response is not retried.

The manual activation action does not itself call NVIDIA. It only records the
expiring authorization. The next scheduled check may run only after the global
lock is separately clear.

## Global NVIDIA request-rate protection

Every live NVIDIA call from GENERAL Core, individual agents, coordinator execution,
blank-slate research, and the two direct API diagnostic workflows reserves a slot
through the same shared ledger before the HTTP request is sent. The ledger is stored
on the separate `general-nvidia-rate-limit-state` branch so rate accounting does not
change the canonical archive, evaluation ledger, or `main` branch.

The guard enforces a rolling limit of at most 72 reservations in any 24-hour
window, a 30-per-minute normal operating ceiling, and the immutable 40-per-minute
hard limit across the repository's shared NVIDIA key. It reads the current safety lock
and initializes a dedicated Git worktree for the ledger, then records each request
reservation by a normal fast-forward Git push before the provider call. This avoids
a per-request GitHub REST Contents API read/write pair and its hourly/content-write
quotas. The existing `general-nvidia-api` concurrency group serializes all six
provider workflows; a non-fast-forward push or other Git failure blocks the
provider request instead of overwriting a reservation. A 30-second reservation
timestamp safety margin accounts for the bounded state-operation time. When 30 minute slots are occupied, GENERAL waits for an expired slot; the hard
40-per-minute validation remains a separate guardrail. When the rolling daily or
category budget is exhausted, the request is blocked until an eligible reservation
expires. A missing, malformed, unreadable, or unwriteable ledger blocks the
provider call. Failed and uncertain provider attempts still consume their
reserved slot; no automatic provider retry is performed.

The global lock has one narrowly scoped exception: the dedicated
`.github/workflows/nvidia-diagnostic.yml` may reserve exactly one forward-only
diagnostic request while the lock remains active. Its preflight and provider
reservation both query the GitHub Actions run API and require the canonical
repository, the fixed diagnostic workflow ID and path, `workflow_dispatch`,
the `main` branch, the exact one-request approval phrase, matching run/commit
identity, an in-progress run, `run_attempt=1`, and the GitHub API `actor` must
match the fixed authorized account login and immutable numeric user ID. The
runner-supplied actor environment values are not trusted. The authorized account
is the repository-connected administrator verified during PR review. The diagnostic job grants
`actions: read` for this metadata check and retains `contents: write` only for
the existing shared rate-limit and failure-lock state operations. A runner
environment flag alone never authorizes the exception.

The diagnostic run ID is permanently recorded in
`diagnostic_run_ids` in the same shared rate-limit ledger commit that reserves
its 40-per-60-second hard-limit slot. That one-shot history survives expiry of the rolling
rate window, so a second reservation or workflow rerun using the same run ID is
rejected. Normal GENERAL and agent requests continue to use the ordinary
`check` path and remain blocked by an active global lock. The diagnostic
exception does not edit or clear `general_nvidia_lock.json`; every reservation
still requires the lock to be active and the shared rate-limit push to succeed.

The state branch is operational safety data and must not be deleted or merged into
`main`. Its legacy v1 history is reconstructed from append-only reservation commits
before the first v2 request; if the full rolling 24-hour history cannot be proved,
NVIDIA requests remain blocked rather than assuming a zero daily count. The 40-per-60-second accounting covers requests routed through this
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
  the first hosted request. Only a recorded NO_PROVIDER_REQUEST_SENT outcome
  permits a bounded retry. Any delivery-uncertain or already-sent request is not
  repeated automatically.
- A target already present in the sealed evaluation ledger is not run again by
  the scheduler, including a cycle previously started manually.

## Reporting and limits

On a completed evaluation, Telegram receives separate forward and reverse
messages containing candidate IDs/hashes, test dates, hit counts, and matched
top-six column positions. Target number sets, predicted number values, and full
candidate rankings are omitted. Reverse results are labelled retrospective only.

The scheduled workflow uses GitHub Actions schedule events; GitHub does not guarantee
that scheduled jobs start at an exact minute or that a hosted runner is always
available. This is recurring remote automation, not a permanently running
server process.

A successful scheduler run does not prove lottery predictability. The canonical
method registry remains unchanged; unsupported hypotheses remain untested
rather than being approximated.


## Between-draw work and queue completion

The 8-call between-draw allocation is a shared category ceiling for independent
GENERAL work that does not consume a new draw target. It is not an instruction to
spend eight calls every day. Offline candidate tests do not consume NVIDIA calls.
A background task is eligible only when it has a bounded, explicit input snapshot
and a finite stopping rule. It must not reuse consumed sealed holdouts, inspect
target values, or mark an unfinished task complete merely to release the next
target. If no executable GENERAL-origin hypothesis is available, the correct
result is NO_APPLICABLE_OFFLINE_WORK; the workflow must not substitute a legacy
Pydroid formula or a fixed catalog candidate.

The implemented target lifecycle is: RESERVED, directional discovery,
proposal/DSL validation, applicable frozen evaluation, Telegram/reporting, and
a terminal SUCCEEDED, NO_ELIGIBLE_CANDIDATE, or FAILED outcome. The scheduler
serializes targets and blocks a newer target while the current target is
unfinished or requires recovery.

**Known scope boundary:** this version does not yet persist a FIFO of every
intermediate draw that arrives while an earlier target is blocked. It retains
the active attempt history and later sees the then-current canonical archive
target; a durable multi-target snapshot queue is a separate implementation
requirement before claiming lossless processing of every intervening draw.
Do not interpret target blocking as proof that intermediate targets are queued.

A technical failure before any provider request is sent may be retried within
the bounded policy above. A valid empty hypothesis response is terminal
scientific NO_ELIGIBLE_CANDIDATE. A sent or uncertain provider request remains
blocked for manual recovery.

The between-draw 8-call allocation is implemented as a quota category, not as
an autonomous offline task runner. This version does not yet schedule offline
hypothesis evaluation while waiting for a new draw. Offline evaluation remains
available only through the existing explicitly invoked, leakage-free test and
sealed-evaluation paths. No NVIDIA calls are made merely to fill the allocation.
