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

The v3 preview:
1. Archives the incorrect `NO_ELIGIBLE_CANDIDATE` event and matching FIFO
   completion record in `recovery_history`.
2. Replaces the active Cycle #55 event with `FAILED / WORKFLOW_FAILED`, using
   the recorded provider-after-response workflow failure timestamp.
3. Restores the exact Generation 51 FIFO item at the queue head.
4. Preserves the existing pending tail, observation cursor, attempt ledger,
   authorization, and unrelated events unchanged.
5. Adds a digest over the complete restored FIFO item to the recovery journal.

PR #103 remains authoritative: `WORKFLOW_FAILED` is not automatically
retryable. The restored target blocks later FIFO targets and itself cannot be
automatically retried. This recovery changes the correctness of the persisted
history; it does not grant permission to repeat Cycle #55.

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
