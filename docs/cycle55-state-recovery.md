# Cycle #55 state-store recovery

## Scope

This procedure prepares a local state preview for the Cycle #55 false terminal
completion. It does not write to the production state branch, send a provider
request, run research, send Telegram messages, or change the NVIDIA lock.

## Pinned evidence

- Current faulty scheduler state: state-store generation 52, commit
  `0c9e5bbbe0cbafea188f941b0408e068080c1e68`.
- Generation 51 pre-completion state: commit
  `6197bfadb8af22633dcb22c5490d32c9ffbd52c7`.
- Target: `2026-11-10`, archive record count 983, archive SHA-256
  `ae7b79e4922d4178a86c7d7880af3e4758d1a5e7f78d6cc3d2595fbb4fd66596`.
- Cycle #55 run: `43288555182`.

The utility verifies both Git blob SHA-1 values and the content SHA-256 values
recorded in the state-store manifest before parsing either state document.
Generation 51 must contain the original 10 November FIFO head, while its
remaining pending items must exactly equal the pending tail in generation 52.
The utility validates the compressed snapshot and its embedded archive identity
without printing or copying draw values into the report.

## Migration result

The original v3 preview:
1. Archives the incorrect `NO_ELIGIBLE_CANDIDATE` event and matching FIFO
   completion record in `recovery_history`.
2. Replaces the active Cycle #55 event with `FAILED / WORKFLOW_FAILED`, using
   the recorded provider-after-response workflow failure timestamp.
3. Restores the exact Generation 51 FIFO item at the queue head.
4. Preserves the existing pending tail, observation cursor, attempt ledger,
   authorization, and unrelated events unchanged.
5. Adds a digest over the complete restored FIFO item to the recovery journal.

That migration was a history correction only. PR #103's three-attempt cap and
`WORKFLOW_FAILED` block remained in force when Generation 53 was published.
A later, separately approved one-time exception is documented below; it does
not alter the original recovery's provenance or rewrite the global retry rule.


## Subsequent one-time Cycle #55 retry exception

On 2026-11-13, a separate explicit approval authorized one additional attempt
only for the exact 2026-11-10 FIFO target (archive SHA-256
`ae7b79e4922d4178a86c7d7880af3e4758d1a5e7f78d6cc3d2595fbb4fd66596`, 983
records), after its three existing pinned attempts and final
`WORKFLOW_FAILED` event. The one-time exception is implemented in scheduler
state schema v4 in `manual_retry_history`; it does not make any other
`WORKFLOW_FAILED` target retryable or raise the normal three-attempt limit.

The guarded `cycle55-manual-retry` workflow_dispatch path requires the pinned
target and FIFO snapshot, the exact one-time confirmation, a first-attempt
dispatch on `main`, and the pinned authorized GitHub actor/workflow identity.
It rechecks existing authorization, the clear global NVIDIA lock, fresh
disjoint forward/reverse sealed windows, chronology, minimum discovery history,
and target isolation before reserving attempt four. It cannot unlock the
provider, modify free-only authorization, consume or reuse prior holdouts, or
skip the FIFO head. The one-time journal is append-only and cannot be replayed.

A terminal result from that exact fourth attempt may release only the matching
2026-11-10 FIFO item, after the existing memory/evaluation/state-store
finalization gates succeed. The subsequent 2026-11-12 FIFO target remains
next. The manual exception is not itself a provider request; normal shared
NVIDIA rate limits, lock checks, evaluation, memory, and Telegram reporting
remain in force.

## Scheduled-work pause and controlled migration workflow

The dedicated .github/workflows/general-cycle55-migration.yml workflow is
manual-only; it has no schedule trigger and has separate permission scopes for
status, pause, preview, and publication.

The pause action disables the existing
.github/workflows/general-autonomous-research.yml workflow through GitHub's
workflow-disable API. This prevents future schedule triggers and also makes
that research workflow's manual dispatch unavailable while disabled.
The pause action disables workflow triggers only; it does not change the scheduler's
authorization record. The pinned recovery intentionally preserves the
Generation 52 authorization exactly, so the authorization flag remains enabled
in the Generation 53 recovery state. Keep the research workflow disabled after
recovery; re-enabling it requires a separate, explicit authorization decision. It does
not cancel queued or in-progress runs. The controller lists every run for that workflow, regardless of whether it
began from schedule or manual dispatch, and the migration gate refuses to
proceed until every run is completed. The preview and publication gates require two zero-active-run
observations separated by a 60-second quiet window. It never cancels a run and
never treats an uncertain disable result as success.

The scheduler's existing pause input is not a substitute for disabling the
workflow: its authorization flag is checked after scheduled archive refresh,
FIFO capture, and the bounded offline discovery stage. The dedicated workflow
pause therefore has to be completed and drained before recovery preview or
publication.

The status action is read-only. Preview requires the research workflow to be
disabled_manually and every run of the research workflow to be complete. It fetches the exact
raw Generation 51 and Generation 52 files from their pinned Git commits,
verifies each manifest, Git blob SHA-1, content SHA-256, byte length, and
schema-v2 source identity, then runs the existing local preview utility. It
does not call the normal state hydrator before preview: domain validation
upgrades v2 to v3 in memory, and hydration writes the upgraded representation
locally, which would no longer be the raw bytes pinned by the historical
manifest. Preview files are not uploaded as artifacts.

Publish is a separate manually dispatched action. It requires the exact
confirmation phrase CYCLE55_CONFIRM_V3_RECOVERY_WRITE_2026_11_10, repeats the
schedule-drain and historical provenance checks, rechecks the drain immediately
before writing, and invokes the existing generation-guarded state writer with
--expected-generation 52 --objects autonomous_research_state. The writer
therefore publishes only the recovered scheduler-state object, in one
generation-guarded state-store commit. The other three runtime objects and the
global NVIDIA lock are outside this operation's write set. A successful
publication is read back and must validate as Generation 53, schema v3, one
recovery journal entry, FAILED / WORKFLOW_FAILED, and the restored Cycle #55
FIFO head.

The workflow has no automatic retry, no state-store rollback, no automatic
workflow re-enable, and no NVIDIA, research, or Telegram step. If the state
writer reports an uncertain outcome, inspect the authoritative state-store
branch and workflow status before taking any further action. Do not rerun the
publication blindly. Re-enabling scheduled research is a separate operational
decision and is deliberately not part of this recovery.

## Local preview command

Supply the exact raw UTF-8 contents of the current state blob and Generation 51
state blob as separate files. The source file must be fetched from the pinned
commit, not reconstructed from the current archive. Run:

```bash
python3 general_cycle55_recovery.py \
  --current-state /tmp/cycle55-generation-52.json \
  --snapshot-source /tmp/cycle55-generation-51.json \
  --output /tmp/cycle55-recovery-preview.json \
  --recorded-at-utc 2026-11-13T12:00:00+00:00
```

The command writes a new local preview file only. It refuses existing output
files and refuses to overwrite either source. The production state-store writer
is not invoked. The preview must be independently reviewed and tested before
a separately authorized publication step is considered.
