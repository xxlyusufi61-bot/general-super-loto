# GENERAL bounded continuous research loop

## Purpose

The autonomous research workflow now supports a bounded serial chain of complete
research runs. A six-hour scheduled trigger starts a session. After one complete
research/evaluation attempt and its matching Telegram outcome report, a small
read-only controller determines whether another full run is eligible. If so,
a dedicated workflow job dispatches exactly one successor run.

Each successor repeats the existing archive refresh, one-active-target queue
capture, offline discovery, NVIDIA authorization and lock gates, directional
research, sealed evaluation, state publication, and Telegram reporting. The
system does not loop only the model-prompt step and does not create parallel
research attempts.

## Session limits

- The session opens a five-hour continuation window from the first
  scheduled research run. No successor is dispatched at or after that deadline.
- At most 150 whole research iterations may belong to one session.
- Each iteration is a separate GitHub Actions workflow run with the existing
  60-minute scheduled-cycle job timeout. An iteration already in progress is
  allowed to finish and report after the continuation deadline; only a new
  iteration is blocked. The window is not a five-hour runner process.
- The next scheduled six-hour trigger starts a new session. A session never
  extends its original deadline.
- Only the scheduled event can create iteration 1. A successor is accepted only
  on main, through workflow_dispatch, with action continue, and actor
  github-actions[bot]. The child verifies its parent run is this workflow and
  that the parent's dedicated continue-research job actually dispatched it.
  Manually selecting continue does not authorize a run.
- The dispatch job gets only contents: read and actions: write. Existing
  scoped writer Apps and their permissions are unchanged.

## Continuation gates

The controller proceeds only when all conditions pass:

1. The current iteration has reached a terminal state and its matching success,
   no-candidate, or failure Telegram report was confirmed.
2. The expiring free-only authorization remains active.
3. The remote global NVIDIA lock is verified clear.
4. The shared rate-limit ledger is readable and has capacity for both initial
   directional requests in the next turn, using either the ordinary forward and
   reverse categories or the retry reserve selected by the existing scheduler.
5. The ordinary prepare_cycle preflight confirms that the exact active target,
   archive snapshot, retry provenance, pending sealed claim, consumed holdout
   dates, and fresh directional validation windows make another attempt eligible.
6. The five-hour deadline and 150-iteration safety ceiling have not been reached.

The controller reads state through the existing verified state-store hydration
path and checks the NVIDIA lock and rate-limit ledger read-only. It runs
prepare_cycle against a temporary hydrated copy. It does not publish runtime
state, migrate the remote budget ledger, reserve NVIDIA requests, activate or
pause authorization, clear a lock, or update the target queue.

The ordinary request reservation remains the final authority immediately before
each provider call. The existing 30-request normal rate ceiling, immutable
40-request hard ceiling, 1,500-request rolling daily budget, category
allocations, and retry accounting remain enforced by general_nvidia_guard.py.
The continuation preflight requires two available request slots so it does not
start a partial forward/reverse pair.

## Target continuity and research integrity

Every successor begins by refreshing the canonical archive and applying the
existing one-active-target queue rules. An unfinished target remains pinned to
its exact archive snapshot. A newer draw is staged as the latest archive
snapshot and cannot replace unfinished work. Once the existing sealed
evaluation and queue lifecycle resolves the old target, a later iteration can
advance to the newer target.

The loop does not change discovery or evaluation methodology:

- Target and post-target draw values remain hidden from discovery, training,
  tuning, and selection.
- The archive scope remains 6-of-60 draws dated 2020-08-02 and later.
- Forward walk-forward and time-ordered cross-validation remain chronological.
- Reverse research remains a separate retrospective stress test, never
  predictive evidence.
- Consumed sealed holdout and offline development dates remain unavailable for
  later research.
- A→B→C, A→T, and MOD5→T remain open research-family labels only.
- The user's earlier manual/Pydroid formulas and reports remain outside GENERAL
  research context. GENERAL may use only its own validated append-only learning.
- Candidates remain hypotheses. There is no automatic selection, promotion, or
  canonical approval.

A terminal NO_ELIGIBLE_CANDIDATE for the same active target ends the current
session instead of inventing random attempts. If the latest archive has advanced
to a different eligible target, the existing scheduler preflight decides
whether that newer target may begin. Retryable failures can continue only
through existing retry rules; manual-unlock requirements for
WORKFLOW_FAILED and PROVIDER_DELIVERY_UNCERTAIN are unchanged.

## Telegram and dispatch reliability

A successor is dispatched only after the iteration's state/report stages and
Telegram outcome step complete successfully. If an actual research attempt was
made, a confirmed Telegram success, no-candidate, or failure report is required.
If no research attempt was made because the queue has no new target, the session
can stop with WAITING_FOR_NEW_LATEST_DRAW without inventing a missing research
report. If Telegram delivery for an actual attempt is uncertain, the chain
stops to avoid duplicate reporting. The controller sends one workflow-dispatch
request and never retries an ambiguous dispatch; an uncertain dispatch is
reported as blocked rather than risking two children.

A separate stop-status message reports session expiry, budget exhaustion,
authorization or lock failure, missing eligible work, and other safe stop
reasons when the research report itself was successfully delivered. It is not
used to replay or compensate for an uncertain Telegram research report.

## Validation boundary

The new controller has deterministic offline tests for session identity,
expiration, iteration ceilings, Telegram gating, dispatch authorization, and
read-only budget-capacity checks. Workflow syntax and existing continuity,
target-queue, sealed-evaluation, research-memory, and coordinator regressions
run in the existing pull-request CI workflows.

Passing these tests verifies the code paths they exercise only. It does not
activate the production writer-validation variable, migrate the live budget
ledger, dispatch a live research run, make a live NVIDIA request, or prove that
Telegram delivery occurred in production.
