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

The coordinated runtime cutover is already active. Consuming workflows
hydrate the four registered runtime objects from
`general-automation-state` before application modules read them, and
approved updates are published through the transactional writer using the
manifest generation captured during hydration. The state branch is
authoritative; copies of those four objects on `main` are historical
comparison snapshots only and are not runtime inputs.

This PR aligns reader and runtime-audit reporting with that active authority
model. It does not perform the initial migration or publish runtime-state
changes. Writer regression tests use fake transport; state-branch integrity
verification is read-only. This PR does not dispatch workflows or make NVIDIA
requests.


## Workflow integration map

The canonical runtime read path is the composite hydrate
action followed by the read-only active-store audit. Every consuming workflow hydrates the exact four-object state snapshot before
any application module reads an archive, evaluation ledger, autonomous
scheduler state, or research memory:

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

## Runtime operation

Each consuming workflow hydrates one verified state-branch snapshot before
reading runtime data. Writers publish only explicitly allowlisted objects and
must use the generation returned by hydration; stale generations and branch
races fail closed. The four runtime objects are not written to `main`.
The global NVIDIA lock remains on `main`, the shared 40-requests-per-minute
ledger remains on `general-nvidia-rate-limit-state`, and immutable sealed
evaluation snapshots remain on `main`.

Documentation changes and offline regression tests do not publish runtime
state or contact NVIDIA.
