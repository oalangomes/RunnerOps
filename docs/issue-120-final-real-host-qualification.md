# Issue #120 final real-host qualification

## Result

PASS

## Qualified revision

- PR #133
- branch: feat/issue-120-ephemeral-lifecycle
- exact SHA: 417bfb3bb5261a48f1cb0108c931e5e3e0398626
- workflow run: https://github.com/oalangomes/RunnerOps/actions/runs/36912469392

## Preconditions

- Revision gate satisfied: the checked-out HEAD was exactly 417bfb3bb5261a48f1cb0108c931e5e3e0398626.
- PR #133 remained clean and pointed at feat/issue-120-ephemeral-lifecycle.
- The required `Validate runner platform` status was green before the real-host mutation.
- Persistent pool baseline was captured via `runnerctl list`, `runnerctl health all`, and `runnerctl capacity . --json` without altering the existing persistent registrations.

## Exact identity

- action_id: 01779d911f444762b3ff7208b2fe642e
- runner_identity: runnerops-ephemeral-89eb52df32c4f0f9
- deterministic proof: `runnerops-ephemeral-$(printf '%s' "$action_id" | sha256sum | cut -c1-16)` equals `runnerops-ephemeral-89eb52df32c4f0f9`.

## Lifecycle timeline

| Time (UTC) | State | Evidence |
| --- | --- | --- |
| 2026-10-01T19:10:07Z | REQUESTED | `runnerctl ephemeral create` accepted exact action id and persisted action record. |
| 2026-10-01T19:10:46Z | REGISTERING | registration started, local config materialized, one exact registration attempt recorded. |
| 2026-10-01T19:10:58Z | REGISTERED | local configuration succeeded and exact runner identity was configured. |
| 2026-10-01T19:11:01Z | ONLINE | exact runner observed online and locally active as `actions.runner.runnerops-ephemeral-alangomes@01779d911f444762b3ff7208b2fe642e.service`. |
| 2026-10-01T19:13:14Z | BUSY | GitHub observation reported `status=BUSY` and `workload_evidence.observed=true`. |
| 2026-10-01T19:19:17Z | TERMINAL | after the permitted reconcile, the exact runner was absent remotely and the local unit had exited; `terminal_evidence.proven=true` with reason `workload_observed_then_local_exit_and_remote_absence`. |
| 2026-10-01T19:19:42Z | CLEANUP_PENDING | cleanup started against exact action and exact remote/local root state. |
| 2026-10-01T19:19:43Z | CLEANED | `runnerctl ephemeral cleanup` returned `result=SUCCEEDED` and `root_removed=true`. |

## Controlled workload proof

- RunnerOps BUSY evidence: observed at 2026-10-01T19:13:14.370767+00:00; `github_observation.status=BUSY` and `workload_evidence.observed=true`.
- GitHub exact job result: `One controlled ephemeral job` executed with runner name `runnerops-ephemeral-89eb52df32c4f0f9` and conclusion `success`.
- Job labels included the exact ephemeral identity: `self-hosted`, `Linux`, `X64`, `runnerops-ephemeral-89eb52df32c4f0f9`.

## Terminal proof

Terminal proof was established by the permitted reconcile after the workflow completed:

- exact remote identity was absent (`github_observation.status=ABSENT`)
- exact local systemd unit observed `status=EXITED`
- `workload_evidence.observed=true` was already present from the BUSY window
- `terminal_evidence.proven=true` and reason was `workload_observed_then_local_exit_and_remote_absence`

This is the required V1 terminal condition for the action even though GitHub job conclusion is recorded as `success` and the lifecycle-level `job_conclusion` remains `unknown` by design.

## Cleanup proof

The cleanup step only proceeded after fresh terminal evidence was present:

- `runnerctl ephemeral cleanup 01779d911f444762b3ff7208b2fe642e --json` returned `result=SUCCEEDED`
- `cleanup.remote_removed=true`
- `cleanup.local_stopped=true`
- `cleanup.root_removed=true`
- `action_state=CLEANED`

## Idempotency

After cleanup, the action converged without further mutation:

- `CLEANED -> cleanup -> CLEANED`
- `CLEANED -> status -> CLEANED`
- `CLEANED -> reconcile -> CLEANED`

This confirms the lifecycle converged to a stable end state without retrying create or requiring any remote registration mutation.

## Persistent pool before/after

The persistent pool was not regressed by the qualification. The same persistent runners remained active, and the only differences in the capacity JSON were collection timestamps and wall-time metrics, not runner inventory or state.

- before: 25 persistent items, health OK, no ephemeral identity present
- after: 25 persistent items, health OK, no ephemeral identity present
- regression_observed: false

## Safety invariants

- create_calls: 1
- blind_registration_retry: false
- registration_material_persisted: false
- persistent_registry_mutated: false
- no second `create` was used after the initial action was created

## Known limitation

This is a V1 lifecycle qualification. The `job_conclusion` at lifecycle level remains `unknown` because the contract is designed around workload evidence plus local exit + remote absence rather than a full GitHub job-summary mapping. The observed BUSY sample was still short and valid, but this remains a known V1 limitation.

## Final gate

BLOCKERS: 0
HIGH: 0
MEDIUM: 0
LOW: 0

REAL-HOST QUALIFICATION: PASS
PR #133 MERGE GATE: SATISFIED

The implementation was not changed during this real-host qualification; only durable evidence was recorded in this documentation set.
