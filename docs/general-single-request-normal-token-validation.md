# GENERAL single-request normal-mode token validation

## Purpose

This manual-only workflow validates the ordinary `general_open_research.call_model`
path with `DEFAULT_MAX_COMPLETION_TOKENS = 8192`. It is deliberately separate from
the locked diagnostic path (which selects the diagnostic token constant) and from
the ordinary two-direction research workflow.

## One-shot boundaries

- Only `workflow_dispatch` from `main` is eligible for the live job.
- The exact approval phrase is `I_APPROVE_ONE_NORMAL_RESEARCH_REQUEST_8192_TOKENS`.
- The run must be the first attempt. Reruns are rejected by both the workflow and
  the GitHub Actions run-identity validator.
- The operator must explicitly attest that the already-verified free-only/no-billing
  status remains valid. A false or omitted attestation prevents the live job.
- Workflow-level offline tests run before any live step.
- The live job shares `general-nvidia-api` concurrency with the scheduler and all
  other NVIDIA request workflows.

## Safety sequence

1. Hydrate and audit the authoritative state-store objects.
2. Verify the exact workflow run using GitHub's Actions API and confirm that the
   global NVIDIA lock is active.
3. Refresh the archive locally, without publishing the archive or changing the
   state-store generation.
4. Validate the archive, isolate the latest target, and preflight fresh disjoint
   forward and reverse sealed windows. If any check fails, the lock is not opened.
5. Temporarily clear the lock only through the exact verified workflow run.
6. Recheck the clear lock and make exactly one forward inference request through
   the ordinary normal-mode SDK branch. The shared 40-per-60-second limiter is
   reserved before the provider call. SDK retries remain zero; `stream=false`;
   the response must have final `message.content` and `finish_reason=stop`.
7. Always attempt to restore the fail-closed global lock after the temporary unlock,
   whether the request succeeds, fails, times out, or returns an incomplete response.

## Data boundaries

- Discovery inputs contain only validated rows dated 2020-08-02 or later and strictly
  earlier than the isolated target.
- The target numbers and rows on/after the target are not sent to the model.
- Both fresh sealed holdout intervals are checked and withheld from the prompt.
  The reverse interval is preflighted only; no reverse model call is sent.
- The prompt uses only same-direction sanitized GENERAL memory. No historical
  user/Pydroid formulas or their results are included.
- The test does not parse or promote hypotheses, score formulas, claim a sealed
  interval, evaluate a formula, submit rule changes, or modify the method registry,
  evaluation ledger, research memory, or authoritative archive.
- The final model text is not persisted or uploaded. Only sanitized usage/result
  metadata is retained.

## Outcome interpretation

A successful run proves that one live normal-mode request completed with the
8,192-token request parameter and a validated complete response. It does not prove
that the provider will always return complete 8,192-token responses, that a quota
is unlimited, that the account is charge-proof beyond the operator's attestation,
or that any lottery hypothesis is predictive.

A failed or interrupted run is consumed. Do not rerun it automatically or use its
partial response. Inspect sanitized metadata and preserve the global lock.
