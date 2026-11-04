# GENERAL Automation State Writer

## Scope

The writer publishes only these migrated runtime objects to the dedicated
`general-automation-state` branch:

- `super_loto_archive.json`
- `general_evaluation_ledger.json`
- `general_autonomous_research_state.json`
- `general_research_memory.json`

The NVIDIA global lock remains on `main` until every lock reader and writer is
migrated together. The shared 40-requests-per-minute NVIDIA rate-limit ledger
remains on its separate branch. Legacy `general_memory.json`, the method
registry, reports, and sealed snapshots are not writable through this module.

## Transaction protocol

1. Read and validate one state-branch snapshot, including the manifest, allowlist,
   Git blob SHA-1, SHA-256, byte lengths, and branch-head recheck.
2. Require the caller's expected manifest generation to match that snapshot.
3. Validate the merged four-object document set with the existing archive,
   sealed-ledger, autonomous-state, and research-memory validators.
4. Create one Git tree containing only changed objects plus the updated manifest.
5. Create one commit whose sole parent is the exact snapshot commit.
6. Recheck the branch head, then update the ref with `force=false`. A concurrent
   writer makes this commit non-fast-forward and GitHub rejects the update.
7. Never retry a failed or ambiguous ref update automatically. Verify the remote
   head before any retry; a corrective write is a new generation, not a reset.

A batch changing several objects increments the manifest generation once and
publishes all changed objects and the manifest in the same Git commit. A stale
generation, unexpected object, invalid payload, or branch race fails closed.

## Hydration and publication

`hydrate` reads one verified snapshot and writes the four local workspace files
through per-file temporary files and atomic replacement. It does not write GitHub.

`publish` requires an explicit object list and expected generation. It cannot
write `nvidia_lock`, change the NVIDIA rate-limit branch, or update `main`.
It prints only object keys, generation, and commit identifiers; it does not emit
draw rows or number sets.

## Current deployment status

This module is a writer primitive, not an operational cutover. Until separate
workflow integration and parity verification are completed, `main` remains the
runtime source of truth. The global NVIDIA lock stays locked and no NVIDIA
requests are made by this module.


## Workflow integration map

The canonical runtime read path is now the composite hydrate action followed by
the read-only active-store audit. All consuming workflows hydrate the exact
four-object state snapshot before any application module reads an archive,
evaluation ledger, autonomous scheduler state, or research memory:

- autonomous research control, scheduled archive refresh, and scheduled cycle;
- blank-slate open research;
- GENERAL agent and both coordinator workflows;
- walk-forward evaluation and its committed-archive baseline test;
- canonical archive target-isolation audit and live cross-source audit;
- the explicitly gated NVIDIA diagnostic workflow.

Writers use the composite publish action with the generation captured during
hydration. Each publication is one Git commit containing all changed objects
and the manifest. Multiple objects in one publication share one generation.
Every later publication uses the generation returned by the preceding
publication. A stale writer is rejected rather than rebased or retried.

## Deliberate storage boundaries

The first cutover covers exactly four runtime objects. The following remain
outside it:

- `general_nvidia_lock.json` remains on `main`; its REST Contents API reader
  and writer are intentionally not redirected to the state branch.
- The shared 40-requests-per-minute ledger remains on
  `general-nvidia-rate-limit-state`.
- `general_evaluation_snapshots/` remains on `main` as immutable sealed
  evaluation evidence. The research workflows commit only this snapshot
  directory there; the evaluation ledger itself is persisted to the state
  branch.
- Legacy `general_memory.json`, the method registry, candidate/report files,
  and temporary artifacts remain outside the first state-store contract.

Consequently, direct main-branch data writes are not entirely eliminated:
global-lock updates and immutable snapshot creation still have their existing
main-branch write paths. Main branch protection must remain disabled until
those two exception paths are separately reviewed and coordinated. This
change does not enable branch protection.

## Operational transition

After this PR is merged, the updated workflows will begin hydrating and
publishing state-store data when they are next invoked. The state branch is
not changed by this PR, no live workflow is dispatched here, and no data is
written to GitHub by CI. The NVIDIA lock remains locked and autonomous
research remains paused. Existing main copies of the four migrated files are
historical shadows and are not consumed by the updated workflows.
