# Autoscale Slice 05 real-host qualification

On 2026-10-02, the governed local ephemeral slice was exercised on a Linux
systemd host against one controlled GitHub Actions workflow dispatch. The
workflow used a dedicated capability label that no persistent runner offered.
The scheduler was enabled with a 30-second interval, 30-second proven queue
threshold, `max_active_local_runners=0`, local provisioning disabled, and
explicit ephemeral policy with `max_active=1`, profile `generic`, and labels
covering the controlled job. The scheduler was disabled after cleanup.

| Evidence | Observed result |
| --- | --- |
| Workflow run / job | `37029122281` / `110911467683` |
| Proven queue episode | `15:45:20Z` first observed; 36 seconds proven before the job left the queue |
| Decision | `plan-f1bf1957ce3085bde6739fbf08f6de20` selected `CREATE_EPHEMERAL` |
| Autoscale action | `action-51e68c6f7d1cce3c14ca47aab9c958cb`, exact target `6ec2aff58666ad2c524a7c2ed4ffe2a9` |
| Exact runner | `runnerops-ephemeral-99904295837d1aa6` |
| Lifecycle | `REQUESTED → REGISTERING → REGISTERED → BUSY → TERMINAL → CLEANUP_PENDING → CLEANED` |
| Workload and terminality | `workload_observed=true`, `terminal_proven=true`; lifecycle job conclusion remains `unknown` by contract |
| Cleanup | `SUCCEEDED`, local stop and root removal true, exact remote identity absent |
| Autoscale completion | Action `succeeded` with `EPHEMERAL_CLEANED` at `15:47:47Z` |

`runnerctl ci watch` reported success for the workflow SHA and for the PR head.
The lifecycle recorded one registration attempt. Autoscale history contained
one `CREATE_EPHEMERAL` decision and action. A repeated public cleanup call
returned `CLEANED` with the original cleanup attempt count still equal to one.
The final scheduler state was disabled and inactive; the queue was empty.

Persistent inventory remained at five healthy on-demand idle registrations
before and after the run. Two pre-existing remote-only registrations remain
`inconclusive` in the broad `CapacitySnapshot`; they do not offer the dedicated
qualification label. Consequently `runnerctl report` included the new decision
and action counts but returned `collection_status=failed` for
`current_capacity_inconclusive`. This existing broad-inventory limitation did
not affect the scoped planner decision or the one-job lifecycle proof.
