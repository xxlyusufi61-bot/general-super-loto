# GENERAL one-time recovery: 2026-11-05 target

## Scope

This recovery path exists only for the original 2026-11-05 target whose forward
NVIDIA request timed out during GENERAL Autonomous Research Cycle #27
(run `42144488585`). The original failed scheduler attempt remains unchanged
in `general_autonomous_research_state.json`. The recovery workflow does not edit
the ordinary scheduler, its six-hour schedule, its authorization record, or
its target-attempt list.

## Historical archive pin

The recovery archive is read from state-store commit
`8be67c7ba561862aa81af988f3544ef5fbb7be71`, generation 11. The pinned
archive is fixed at 981 rows, from 2020-08-02 through 2026-11-05, with SHA-256:

`5319d759df2ab34bf15e8eed66fc85826b9628f7297daa39b6fd34803a40d452`

Before research, the helper checks the exact source commit, byte length,
content hash, archive row count, target date, Data Guard validation, and equality
of the historical prefix against the current canonical archive. Later rows in
the current archive are allowed, but they are never passed to the recovery
research runner. If the historical prefix has changed, the workflow stops.

The research runner receives the pinned archive through
`GENERAL_ARCHIVE_FILE=general_recovery_archive.json`. The 2026-11-05
target and all later records remain excluded from discovery. The sealed
preflight must provide fresh, non-overlapping forward and reverse holdouts
before any hosted request is allowed.

## One-shot and NVIDIA limits

The recovery workflow is manual-dispatch only and shares the
`general-nvidia-api` concurrency group. The helper queries GitHub Actions run
history and authorizes only one original `workflow_dispatch` run from `main`,
with the fixed repository actor, matching run/commit identity, the exact
confirmation phrase, and `run_attempt=1`. A second dispatch or rerun is
rejected. A failed or interrupted one-shot dispatch is not automatically
restarted.

The original run #27 reserved one forward provider request before timing out.
Recovery may make at most two new requests: one forward and one reverse, in
that order. Thus the target-wide ceiling is three requests including the
original uncertain attempt. The existing research runner retains
`max_retries=0`, a 600-second request timeout, `stream=false`, and its
two-request per-run cap. The shared NVIDIA limiter continues to enforce
40 requests per rolling 60-second window. Any incomplete recovery request
activates the existing global safety lock.

The workflow rechecks the existing expiring authorization and global lock
immediately before model inference. It never unlocks the provider guard.

## Completion and return to normal scheduling

If both directional discovery requests return valid responses, the existing
safe-DSL and sealed-evaluation flow is used. The immutable archive snapshot
and sealed holdout claim are persisted through the existing mechanisms, and
the evaluation ledger prevents those holdout dates from being reused.

The recovery does not rewrite the scheduler's 2026-11-05 failed attempt.
Once the canonical archive advances to a later draw date, the unchanged
scheduler can treat that newer date as its next target. No target override is
added to ordinary scheduled cycles.

## Offline verification

`general-recovery-20261105-test.yml` runs only offline tests and YAML/
Python syntax checks. It does not install an NVIDIA credential, call NVIDIA,
change authorization, clear the global lock, or execute the recovery workflow.
