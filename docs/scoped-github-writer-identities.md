# Scoped GitHub writer identities

## Purpose

Keep the default `GITHUB_TOKEN` read-only in runtime workflows. A write operation must receive the dedicated identity for its one data domain. Missing or misrouted writer credentials fail closed; there is no fallback to `GITHUB_TOKEN`.

## Three separate GitHub Apps

Create and install these identities in a later, separately approved setup step. Each App must be installed only on `xxlyusufi61-bot/general-super-loto` and granted **Contents: read and write only**. Do not grant Actions, Administration, Issues, Pull requests, or workflow permissions.

| Writer role | Repository variable | Repository secret | Intended branch scope, enforced by code plus Rulesets |
|---|---|---|---|
| Main writer | `GENERAL_MAIN_WRITER_APP_CLIENT_ID` | `GENERAL_MAIN_WRITER_APP_PRIVATE_KEY` | `main` |
| Automation-state writer | `GENERAL_AUTOMATION_STATE_WRITER_APP_CLIENT_ID` | `GENERAL_AUTOMATION_STATE_WRITER_APP_PRIVATE_KEY` | `general-automation-state` |
| NVIDIA-budget writer | `GENERAL_NVIDIA_BUDGET_WRITER_APP_CLIENT_ID` | `GENERAL_NVIDIA_BUDGET_WRITER_APP_PRIVATE_KEY` | `general-nvidia-rate-limit-state` |

Workflow token minting is scoped to the one repository and explicitly requests only `contents: write`. The action is pinned to the immutable v3.3.0 commit. Tokens are created immediately before the write path. The official action revokes its token at job completion; installation tokens expire after one hour. The installation token itself is repository-scoped, not branch-scoped. The writer code enforces its role's destination; the separate Rulesets are required to enforce that branch boundary on GitHub.

## Required branch rulesets, to be configured separately

- `main`: require pull requests and the selected offline CI checks for normal changes; block force pushes and deletion. Add only the Main writer App as an **Always allow** bypass actor because the global NVIDIA safety lock and immutable sealed-evaluation snapshots currently require direct writes. This is a narrow but real trust boundary: that App can bypass the main-branch ruleset, so its token must remain available only to the explicit lock/snapshot steps.
- `general-automation-state`: restrict updates and deletions to the Automation-state writer App. Keep the existing manifest generation, content hashes, domain validation, expected commit SHA, single atomic commit, and fast-forward-only ref update unchanged.
- `general-nvidia-rate-limit-state`: restrict updates and deletions to the NVIDIA-budget writer App. Keep the existing one-reservation-per-commit history and normal fast-forward push; never force-push or rewrite prior reservations.

Do not add the general GitHub Actions bot or repository administrators as broad bypass actors. Do not apply these rulesets until the dedicated Apps are installed and their workflows have been tested.

## Workflow permissions

- Default `GITHUB_TOKEN`: `contents: read`; use `pull-requests: write` only in the agent workflow that opens review-only memory PRs.
- Writer Apps: `contents: write` only, with one App per write domain.
- Checkout credentials are not persisted. Git pushes use an in-memory Git HTTP extra-header derived from the role-specific token; the token is not placed in command arguments or printed to logs.

## Deployment gate

This PR adds integration code and workflow references only. It does **not** create GitHub Apps, install Apps, create repository variables/secrets, or change branch protection/rulesets. Write-enabled paths stop if their required dedicated App configuration is missing; read-only paths continue using `GITHUB_TOKEN`. Do not merge this PR or enable branch rulesets until all six App variables/secrets are configured, each App's repository permission is verified, and each writer has passed dry-run checks. Because installation tokens are not branch-scoped, the three branch Rulesets are required before the integration is considered enforced.
