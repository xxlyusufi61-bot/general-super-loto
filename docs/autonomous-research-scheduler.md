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
previous target has unfinished work or a retryable failed cycle. The
scheduler does not expose target values to discovery.

## Authorization and NVIDIA safety

- Automation remains disabled by default in general_autonomous_research_state.json.
- Activation requires the exact authorization phrase, a fresh manual free-only/no-billing
  attestation, and an authorization period of 1, 7, or 30 days.
- Activation is rejected while the existing global NVIDIA safety lock is active.
  The separate manual unlock workflow remains the only way to clear that lock.
- The former target-wide two-request cap is removed. All requests from all GENERAL
  agents now share the same provider reservation ledger.
- Shared rolling budget: at most 200 reservations in any 24-hour window.
- Normal operating ceiling: 30 reservations in any 60-second window. Immutable
  hard ceiling: 40 reservations in any 60-second window.
- Every workflow run package may reserve at most four requests.
- Daily category allocations are forward discovery 100, forward critique 30,
  reverse retrospective diagnostics 20, between-draw research 30, and controlled
  retry reserve 20. The allocations total exactly 200 and are enforced separately.
- Each call's requested completion limit is at most 8,192 tokens. This is a
  response-token ceiling, not a promise that the model will use that many tokens.
- The rolling daily budget is an internal safety ceiling, not a provider free-tier
  entitlement. The provider's account quota is not queried by this controller.
  Free-use and no-billing verification remains an explicit operator attestation.
- A provider request is reserved before network access. Failed and uncertain
  submissions consume the reservation. SDK automatic retries remain disabled.
- The NVIDIA-compatible Chat Completions call uses the official OpenAI SDK's
  managed SSE streaming mode. GENERAL accumulates only `delta.content`, ignores
  reasoning-only fields, and accepts a response only after the stream terminates
  with `finish_reason=stop`. An interrupted stream or a stream without that
  terminal marker is incomplete and cannot be used as a research report.
  Streaming does not authorize another request: uncertain delivery remains
  subject to the same global lock and manual-recovery rules.
- A failed target remains at the FIFO head. Only NO_PROVIDER_REQUEST_SENT
  (proof that no provider request started) permits an automatic same-target
  retry, within the existing limit of three total attempts per target. Each
  retry is recorded and uses the controlled retry reserve.
- WORKFLOW_FAILED means a provider request was sent and returned, but a later
  workflow stage failed; it is not automatically retried.
- PROVIDER_DELIVERY_UNCERTAIN remains blocked until a manual unlock is recorded
  strictly after that failure. Only a later scheduled cycle may then retry the
  same target. The workflow never unlocks itself.
- HTTP 402/429 responses are recorded as PROVIDER_HTTP_REJECTION_NO_RETRY.
  Manual unlock does not override this non-retryable classification.
- No automatic unlocking, paid conversion, card-required service, or billing
  transition is permitted.

The manual activation action does not itself call NVIDIA. It only records the
expiring authorization. The next scheduled check may run only after the global
lock is separately clear.

## Global NVIDIA request-rate protection

Every live NVIDIA call from GENERAL Core, individual agents, coordinator execution,
blank-slate research, and the two direct API diagnostic workflows reserves a slot
through the same shared ledger before the HTTP request is sent. The ledger is stored
on the separate `general-nvidia-rate-limit-state` branch so rate accounting does not
change the canonical archive, evaluation ledger, or `main` branch.

The guard enforces a rolling limit of at most 200 reservations in any 24-hour
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
  the first hosted request. Only a NO_PROVIDER_REQUEST_SENT outcome allows an
  automatic retry. WORKFLOW_FAILED is not automatically replayed after a
  provider request has been sent. A PROVIDER_DELIVERY_UNCERTAIN failure allows
  retry only after a later manual unlock is recorded; no automatic unlock or
  same-run retry is performed.
- A previously claimed target may be researched again after a retryable failure
  when fresh sealed windows remain available. The sealed evaluator excludes all
  previously consumed dates and must create new, disjoint holdouts; it never
  reuses an earlier claim's validation dates.

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


## FIFO target snapshots and between-draw work

The between-draw allocation is a 30-request rolling-budget category shared by
GENERAL and its agents. It is a ceiling, not a requirement to spend the allocation.
The bounded offline symbolic task runner is now wired to the scheduled FIFO cycle.
It runs before the hosted-research authorization/global-lock gate, makes no provider
request, and consumes no NVIDIA reservation. See
docs/general-offline-hypothesis-discovery.md for its candidate grammar, data split,
evaluation contract, and separate append-only memory.

The scheduler state schema v2 contains a bounded durable FIFO. On each scheduled
archive refresh, the capture step appends every newly observed canonical draw
after the persisted observation cursor, in archive chronology. Each queue item
contains a compressed, content-addressed archive prefix ending exactly at that
target. The snapshot records its target date, row count, SHA-256, source-archive
SHA-256, and enqueue timestamp. Snapshot payloads are decompressed with strict
size limits, checked for duplicate JSON keys, validated as sorted 6-of-60 rows,
and hash-verified before use.

The queue is stored inside the existing `autonomous_research_state` object on
the dedicated state-store branch. It does not add a new state object or merge
runtime state into `main`. The scheduler materializes only the oldest snapshot
into the job workspace, then the existing archive validator, target isolation,
sealed preflight, and research workflow run against that exact snapshot. Target
numbers are not written to queue summaries or GitHub outputs. Newer targets
remain queued and cannot replace the current FIFO head.

The queue is bounded to 128 pending targets, 1.5 MB per uncompressed snapshot,
and 5 MB of total compressed pending snapshots. If a limit is reached, the
capture step fails closed without advancing the observation cursor or dropping
any target. A changed or missing historical archive prefix, corrupt snapshot,
hash mismatch, missing cursor, or unprovable legacy active-target snapshot also
blocks processing rather than silently repairing or skipping a draw.

A target leaves the queue only after the scheduler records SUCCEEDED or
NO_ELIGIBLE_CANDIDATE for the matching target date, snapshot hash, and row count.
A NO_PROVIDER_REQUEST_SENT outcome retains the same FIFO head for retry on a
later scheduled cycle, up to three total attempts for that target. WORKFLOW_FAILED
does not trigger an automatic retry after a provider request has been sent.
Retries are recorded. A PROVIDER_DELIVERY_UNCERTAIN outcome retains the head
until the global lock is manually unlocked after the failure timestamp. A later
scheduled cycle may then retry the same target. Any retry must pass sealed
preflight with fresh, disjoint, previously unconsumed holdouts. A target already
being processed is never replaced by the latest archive head.

When the queue is first introduced to a scheduler state with no prior attempts,
the bootstrap cursor starts immediately before the latest canonical draw, so
the current draw is queued rather than replaying the entire historical archive.
When an older state has a terminal attempt, capture starts after its last
terminal target. An unfinished legacy target is queued only when the current
canonical archive is still exactly the raw archive recorded by that attempt:
the source archive SHA-256, final target date, and record count must all match.
This intentionally distinguishes the legacy attempt's raw-file digest (which
includes metadata and JSON formatting) from the FIFO snapshot's newly computed
canonical digest. If the original source archive cannot be proven byte-for-byte,
the scheduler stops for manual recovery rather than guessing at an old snapshot.

FIFO retention does not relax the existing research contract. Every target still
requires its own leakage-free pre-target partition, canonical adjacency, fresh
disjoint sealed holdouts, unchanged minimum training history, and time-ordered
evaluation. Consumed sealed holdout dates and offline development dates remain unavailable to later discovery and validation windows. Offline consumption is read from the validated research-memory namespace; each sealed claim pins its digest and count so a memory change after claim creation fails closed. A queued
target may therefore remain pending when its own frozen snapshot cannot satisfy
fresh sealed-window requirements; the scheduler must wait or require recovery,
not use later draws, lower minimums, reuse consumed dates, or skip ahead.

The implemented target lifecycle remains: RESERVED, directional discovery,
proposal/DSL validation, applicable frozen evaluation, Telegram/reporting, and
a terminal SUCCEEDED, NO_ELIGIBLE_CANDIDATE, or FAILED outcome. A valid empty
hypothesis response is a terminal scientific NO_ELIGIBLE_CANDIDATE. A sent or
uncertain provider request remains blocked for manual recovery.

## Atomic terminal finalization

A claimed target is not marked SUCCEEDED or NO_ELIGIBLE_CANDIDATE until its
sanitized directional research-memory append has passed validation. The
successful terminal event is then staged locally and published in the same
generation-guarded state-store commit as the research-memory object. This keeps
memory persistence and FIFO completion atomic: the queue head is released only
when both records are accepted by the authoritative state store.

If research-memory validation fails after a provider request was sent and
returned, the claimed attempt is recorded as FAILED with WORKFLOW_FAILED. The
FIFO head remains pending and is not automatically retried, preserving PR #103.
A failed or ambiguous final state-store publication leaves the authoritative
attempt in progress, which also blocks the FIFO; it is not treated as a
successful completion and is never retried automatically. Telegram success
messages are sent only after the final state-store publication succeeds.
Failure notifications cover memory-append, terminal-outcome, and final
state-store publication errors.

Before checking hosted-research authorization, the scheduled cycle
materializes only the current FIFO head in the job workspace and runs the
bounded offline symbolic discovery/evaluation task (up to sixteen safe-DSL candidates screened using only the earlier training prefix). A successful exploratory
cycle appends only to the offline_research namespace in the existing general_research_memory.json object via the generation-guarded state writer, validates its number-safe report, attempts Telegram delivery, and uploads a short-lived report artifact. Report parsing and safety validation remain fail-closed: missing safety metadata, target exposure, or invalid candidate structure stops the cycle before the hosted-research gate. Once the report passes validation, a Telegram delivery problem (missing credentials, network error, or API rejection) is recorded as a warning and does not block the later authorization-, lock-, and target-gated NVIDIA stage. Delivery is not automatically retried, avoiding duplicate messages after uncertain submission. The existing sealed directional entries and evaluation ledger remain unchanged by this runner; the offline namespace is isolated inside the same research_memory object. A waiting or failed offline cycle does not consume the FIFO target, modify protected state, clear a lock, or create a provider request. No NVIDIA calls are made merely to fill the between-draw allocation.


## CLI delivery-mode guard

The command-line `--non-blocking-delivery` option is accepted only when
`--kind offline` and `--status success` are supplied together. Every other
combination is rejected during argument validation, before the report is loaded
or a Telegram request is attempted. Other report kinds and non-success
notifications therefore retain their fail-closed delivery behavior.
