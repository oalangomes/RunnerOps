# Autoscale Slice 05 real-host qualification

Qualified PR HEAD: `02195c1e56b39361e6b409335adb7280bf06497c` (runtime and
contract commit exercised in both cycles below).

On 2026-10-05, the governed local ephemeral controller ran from this checkout
on a Linux systemd host. [Hosted Validation](https://github.com/oalangomes/RunnerOps/actions/runs/37322252681)
passed on the same SHA. Two controlled workflow dispatches, also on that SHA,
queued jobs requiring `runnerops-autoscale-ephemeral-qualification`, a label
offered by no persistent runner. The scheduler was enabled with a 30-second
interval and observed-queue threshold, `max_active_local_runners=0`, local
provisioning disabled, and explicit ephemeral policy with `max_active=1` and
profile `generic`. Both jobs succeeded.

| Evidence | Cycle A | Cycle B after A was `CLEANED` |
| --- | --- | --- |
| Workflow run / job | [37322322602](https://github.com/oalangomes/RunnerOps/actions/runs/37322322602) / `111804550552` | [37323000533](https://github.com/oalangomes/RunnerOps/actions/runs/37323000533) / `111806882816` |
| Proven queue pressure | First observed `14:09:50Z`; 39 seconds observed | First observed `14:14:22Z`; 40 seconds observed |
| Decision | `plan-9f29e09a53c1d21d9bcf3b5d2b69f121` | `plan-a9afd02eddd9ba97c26ed42704fbe7ff` |
| Autoscale action | `action-467c02c07997a106a11f1bfd9976a8de` | `action-e73e87a1fd56672eec0c5bde9b47ef9b` |
| Exact lifecycle ID | `ec42fbde1b6745b2e6b9524d02969cb0` | `38520664802e3de8d27ff06810bca089` |
| Runner identity | `runnerops-ephemeral-609b3c77af73fed2` | `runnerops-ephemeral-8814142f7998fbcd` |
| Lifecycle | `REQUESTED → REGISTERING → REGISTERED → BUSY → TERMINAL → CLEANUP_PENDING → CLEANED` | `REQUESTED → REGISTERING → REGISTERED → BUSY → INCONCLUSIVE_TERMINAL → TERMINAL → CLEANUP_PENDING → CLEANED` |
| Registration | One attempt; exact remote runner ID `33` | One attempt; exact remote runner ID `34` |
| Cleanup | `SUCCEEDED`; local stop, remote removal, root removal true | `SUCCEEDED`; local stop, remote removal, root removal true |
| Final autoscale action | `succeeded`, `EPHEMERAL_CLEANED` at `14:12:35Z` | `succeeded`, `EPHEMERAL_CLEANED` at `14:17:17Z` |

The scheduler observed the same exact action on repeated ticks while each job
was running. The B lifecycle briefly reported `INCONCLUSIVE_TERMINAL` when the
remote registration disappeared before local terminality was proven; a later
tick proved terminality from the observed workload, local exit, and remote
absence. Neither cycle created a duplicate action or runner. B received new
decision, autoscale action, lifecycle, and runner identities while A remained
`succeeded` in the audit history and `CLEANED` in lifecycle history.

For both lifecycles, `workload_evidence.observed=true` and
`terminal_evidence.proven=true`; lifecycle job conclusion remains `unknown` by
contract. An additional public `ephemeral cleanup` call after each completion
returned `CLEANED` with the cleanup attempt count still at one. The scheduler
was disabled after B; the final status reported `enabled=false`, `active=false`,
an empty queue, and zero active local runners.

Persistent inventory before and after contained the same five healthy
on-demand idle registrations, with IDs `23` through `27`. Two pre-existing
remote-only registrations, IDs `28` and `31`, remained inconclusive in the
broad `CapacitySnapshot`; neither offered the qualification label. The scoped
planner evidence was complete and selected each `CREATE_EPHEMERAL` action.

This file is an evidence-only follow-up to the qualified runtime commit. Since
a committed file cannot contain its own commit SHA, the subsequent PR HEAD is
qualified separately after publication and identified in the PR update; the
SHA above identifies the exact checkout used for the two-cycle proof here.
