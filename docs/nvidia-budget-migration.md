# NVIDIA Budget V2 to V3 Migration

## Why the Ruleset check is split

GitHub intentionally omits `bypass_actors` from the Rulesets API response unless
the caller has write access to the Ruleset configuration. The dedicated NVIDIA
Budget Writer App is deliberately limited to `Contents: write` and
`Metadata: read`; granting repository Administration write just to read this
field would violate least privilege.

The migration preflight therefore validates the Ruleset ID, repository source,
protected branch, active enforcement, and required protection rules through
the scoped App token. It independently validates the fixed NVIDIA Budget
Writer App identity, exact permissions, and single-repository installation
scope.

The bypass actor itself is an explicit operator confirmation, not an
API-verified assertion. Before dispatching either mode, inspect:

1. Repository **Settings**.
2. **Rules** → **Rulesets**.
3. Open `nvidia-budget-state-protection` (Ruleset ID `27814116`).
4. Confirm exactly one bypass actor exists:
   - Actor type: **Integration**
   - Actor/App ID: **5861647** (`general-nvidia-budget-writer`)
   - Bypass mode: **Always**
5. Confirm there are no additional bypass actors.

Then set the workflow input `ruleset_bypass_confirmed` to `YES`. Its default
is `NO`, and the workflow fails closed otherwise.

## Migration boundary

- `mode=preflight` validates the scoped App, visible Ruleset configuration,
  operator-confirmed bypass actor, and V2-to-V3 conversion preview. It makes
  no writes and sends no NVIDIA API request.
- `mode=migrate` additionally requires the exact approval phrase
  `I_APPROVE_PERSIST_NVIDIA_BUDGET_V2_TO_V3`.
- The migration writes only `general_nvidia_rate_limit.json` to
  `general-nvidia-rate-limit-state`.
- No research, NVIDIA inference, runtime-state, authorization, lock, or global
  writer-gate operation is performed by this migration workflow.

The human confirmation is intentionally limited to this one-time, manually
dispatched migration. Do not broaden the Budget Writer App permissions to
Administration write merely to expose the hidden Ruleset field.
