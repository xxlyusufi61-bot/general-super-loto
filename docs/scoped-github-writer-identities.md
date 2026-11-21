# Scoped GitHub writer identities

## Purpose

Keep the default `GITHUB_TOKEN` read-only in runtime workflows. A write operation must receive the dedicated identity for its one data domain. Missing or misrouted writer credentials fail closed; there is no fallback to `GITHUB_TOKEN`.

## Three separate GitHub Apps

User reports that all three identities have been created and installed only on `xxlyusufi61-bot/general-super-loto`, with the six repository variable/secret names configured. This is user-reported setup status; this integration cannot read secret values or independently inspect App settings. Verify each installation in GitHub settings without exposing PEM keys or secret values. Each App must have **Contents: read and write only**. Do not grant Actions, Administration, Issues, Pull requests, or workflow permissions.

| Writer role | Repository variable | Repository secret | Intended branch scope, enforced by code plus Rulesets |
|---|---|---|---|
| Main writer | `GENERAL_MAIN_WRITER_APP_CLIENT_ID` | `GENERAL_MAIN_WRITER_APP_PRIVATE_KEY` | `main` plus strictly named review-memory proposal branches |
| Automation-state writer | `GENERAL_AUTOMATION_STATE_WRITER_APP_CLIENT_ID` | `GENERAL_AUTOMATION_STATE_WRITER_APP_PRIVATE_KEY` | `general-automation-state` |
| NVIDIA-budget writer | `GENERAL_NVIDIA_BUDGET_WRITER_APP_CLIENT_ID` | `GENERAL_NVIDIA_BUDGET_WRITER_APP_PRIVATE_KEY` | `general-nvidia-rate-limit-state` |

Workflow token minting is scoped to the one repository and explicitly requests only `contents: write`. The action is pinned to the immutable v3.3.0 commit. Tokens are created immediately before the write path. The official action revokes its token at job completion; installation tokens expire after one hour. The installation token itself is repository-scoped, not branch-scoped. The writer code enforces its role's destination; the separate Rulesets are required to enforce that branch boundary on GitHub.

## Required branch rulesets, to be configured separately

- `main`: require pull requests and the selected offline CI checks for normal changes; block force pushes and deletion. Add only the Main writer App as an **Always allow** bypass actor because the global NVIDIA safety lock and immutable sealed-evaluation snapshots currently require direct writes. This is a narrow but real trust boundary: that App can bypass the main-branch ruleset, so its token must remain available only to the explicit lock/snapshot steps.
- `general-automation-state`: restrict updates and deletions to the Automation-state writer App. Keep the existing manifest generation, content hashes, domain validation, expected commit SHA, single atomic commit, and fast-forward-only ref update unchanged.
- `general-nvidia-rate-limit-state`: restrict updates and deletions to the NVIDIA-budget writer App. Keep the existing one-reservation-per-commit history and normal fast-forward push; never force-push or rewrite prior reservations.

Do not add the general GitHub Actions bot or repository administrators as broad bypass actors. Apply these Rulesets before merging/enabling the writer integration, after the dedicated Apps and six credential entries have been configured and the offline writer tests/guards have passed. The live installation-token validation workflow in this PR cannot be dispatched until it exists on the default branch; run it after merge and before enabling any writer-dependent live workflow. The validation workflow only mints the three repository-scoped installation tokens and performs GET requests to `/installation/repositories`; it does not write repository data.

## Workflow permissions

- Default `GITHUB_TOKEN`: `contents: read`; use `pull-requests: write` only in the agent/coordinator workflows that open review-only memory PRs. Keep `issues: write` only in workflows that call `general_rule_change.py`, which creates and updates user-approval issues.
- Writer Apps: `contents: write` only, with one App per write domain.
- Checkout credentials are not persisted. Git pushes use an in-memory Git HTTP extra-header derived from the role-specific token; the token is not placed in command arguments or printed to logs.

## Default-off deployment validation gate

The repository Actions variable `GENERAL_SCOPED_WRITERS_VALIDATED` is a required deployment switch for every normal scoped-writer token. It must be absent or anything other than the exact string `true` until all deployment checks are complete. In that state, the shared `create-scoped-writer-token` action fails before it creates an installation token. This also protects calls made indirectly through `publish-automation-state`.

The autonomous schedule has an additional job-level gate: both `archive-refresh` and `scheduled-cycle` are skipped unless the variable is exactly `true`. The read-only status action remains available. Manual activation/pause or other writer-dependent workflows remain blocked by the shared token gate.

A narrowly restricted exception preserves the ability to activate the global NVIDIA safety lock before deployment validation. Only the Main Writer client ID, only the explicit Main Writer lock operation, and only these workflow references may use that exception: `general-nvidia-manual-lock.yml`, plus the failure-handler lock persistence steps in `general-agent.yml`, `general-autonomous-research.yml`, `general-coordinator-execute.yml`, `nvidia-chat-test.yml`, `nvidia-diagnostic.yml`, `nvidia-test.yml`, and `open-research.yml`. The exception cannot be used for unlocks, snapshots, memory publication, automation-state writes, NVIDIA-budget writes, or research execution. CI checks the allowlist and exact call sites.

**Enable sequence after merge:**
1. Verify the three App installations, their installed permissions, and all six variable/secret names without exposing secret values.
2. Apply and verify the three required branch Rulesets.
3. Separately authorize and run `GENERAL GitHub App Read-Only Validation` from `main`; require all three App slug and single-repository scope checks to pass.
4. Only after all three checks above pass, manually create or set the repository Actions variable `GENERAL_SCOPED_WRITERS_VALIDATED` to the exact value `true`.

If an App, installation, permission, credential, or Ruleset changes, remove the variable or change it away from `true` until validation is repeated. The validation workflow itself intentionally does not depend on this variable, so it can run while the default-off gate is closed.

## Deployment gate

This PR adds integration code and workflow references only. It does **not** create GitHub Apps, install Apps, create repository variables/secrets, or change branch protection/rulesets. Write-enabled paths stop if their required dedicated App configuration is missing; read-only paths continue using `GITHUB_TOKEN`.

**Before merge:** verify all six App variable/secret entries exist (without revealing values), verify each App is installed only on this repository with Contents read/write only, pass the offline writer tests, and apply the three branch Rulesets described above. The live token validation workflow is intentionally dispatchable only from `main`; GitHub does not allow a workflow_dispatch definition that exists only on a PR branch to be run as the trusted default-branch workflow. Therefore, after merge, run `GENERAL GitHub App Read-Only Validation` and require all three token identity/repository-scope checks to pass before enabling any writer-dependent live operation. This post-merge validation performs token issuance and GET-only scope checks; it does not write repository contents, refs, issues, pull requests, or Rulesets and does not call NVIDIA or start Super Loto research.

Because installation tokens are repository-scoped, not branch-scoped, the three branch Rulesets are required before the integration is considered enforced.
