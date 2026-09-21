# RunnerOps v0.3 autoscale dogfood

This document records a real-world dogfood run of RunnerOps v0.3 against a live GitHub Actions workload.

It is intended as engineering evidence, not as a benchmark or a claim of production-scale validation. Repository and runner names are anonymized in this public record; timings, counts, decisions, and outcomes are preserved.

## Goal

Validate the first governed autoscale mutation path introduced in v0.3:

```text
queued work
→ durable queue evidence
→ deterministic planning
→ START_LOCAL
→ exact runner activation
→ structured verification
→ auditable outcome
```

The test specifically targeted already provisioned local capacity. It did not exercise local provisioning or cloud burst.

## Environment

- RunnerOps: `v0.3.0`
- Repository under load: real application repository (public name anonymized)
- Host: WSL2 with systemd
- Runner lifecycle: on-demand
- Matching runner labels: `self-hosted`, `linux`, `local-runner`
- Local pool size: 5 already provisioned runners
- Queue threshold: 300 seconds
- Autoscale mutation gate: `RUNNER_AUTOSCALE_ENABLED=true`

## Preconditions

The controlled run started with:

```text
active local runners: 0
available_now:         0
busy_capacity:         0
provisioned_idle:      5
inconclusive:          0
```

A real GitHub Actions workflow run was then left queued while all matching local runners remained inactive.

During the controlled portion of the test, no `runnerctl ensure .`, `runnerctl start ...`, or equivalent manual lifecycle command was used. This was important because `ensure .` is an explicit operator action that starts enabled runners mapped to the repository and would bypass the autoscale experiment.

## Procedure

The controller was invoked periodically while the workflow remained queued:

```bash
RUNNER_AUTOSCALE_ENABLED=true runnerctl autoscale run-once . --json
```

Observations were kept closer together than the configured `max_gap_seconds=300`, allowing RunnerOps to establish continuous queue evidence instead of ending the observation with `observation_gap`.

## Observed timeline

The controller remained read-safe until the queue threshold was proven:

```text
10:38:58 -03  WAIT  QUEUE_BELOW_THRESHOLD
10:40:08 -03  WAIT  QUEUE_BELOW_THRESHOLD
10:41:17 -03  WAIT  QUEUE_BELOW_THRESHOLD
10:42:25 -03  WAIT  QUEUE_BELOW_THRESHOLD
10:43:34 -03  WAIT  QUEUE_BELOW_THRESHOLD
10:44:43 -03  START_LOCAL
```

The successful controller result was:

```json
{
  "action_id": "action-c27a8a011a293b37512a814ca465bdd6",
  "action_state": "succeeded",
  "decision": "START_LOCAL",
  "decision_id": "plan-5721af56973b794a81744a90ffba6133",
  "diagnostic": "VERIFIED_ONLINE",
  "reason_codes": [
    "MATCHING_LOCAL_RUNNER_IDLE",
    "OBSERVED_QUEUE_THRESHOLD_MET"
  ],
  "repository": "example/workload-repo",
  "status": "ok",
  "target": "workload-runner-01"
}
```

## Durable evidence

The audit store recorded the `START_LOCAL` decision with:

- `decision_id`: `plan-5721af56973b794a81744a90ffba6133`
- `requested_capacity_delta`: `1`
- `policy_fingerprint`: `sha256:30867717ecdcb8a55bc5ebd8b2a2d4ae270d20a297f1e3dfb179fff2f708ff3f`
- queue evidence status: `complete`
- initial matching capacity: 5 `provisioned_idle`, 0 active local runners
- `first_seen_queued_at`: `2026-09-17T13:38:59.045455+00:00`
- planner observation timestamp: `2026-09-17T13:44:43.893270+00:00`

The queued job used to justify the action had remained continuously observed for longer than the 300 second threshold.

After activation, the queue observation for that job was later closed with:

```text
end_reason=left_queue
observation_count=7
observed_queued_seconds=348
```

This is useful because the evidence does not depend on GitHub's original `job.created_at` timestamp to prove queue continuity. RunnerOps proves continuity from its own persisted observations.

## Post-action verification

After the action completed, `runnerctl overview .` reported:

```text
available_now=0
busy_capacity=1
provisioned_idle=4
inconclusive=0
```

The selected runner was:

```text
workload-runner-01:
  local=active
  github=online
  busy=True
  reason=active_online_busy
```

The remaining four matching runners stayed inactive and provisioned.

This is the expected governed-capacity behavior: RunnerOps requested and activated one unit of local capacity rather than waking the whole repository pool.

## What this proves

This dogfood provides real-host evidence that v0.3 can:

1. observe a real GitHub Actions queue;
2. persist queue continuity in the local audit store;
3. remain in `WAIT` before the configured threshold;
4. transition deterministically to `START_LOCAL` after the threshold is proven;
5. select one exact pre-provisioned runner;
6. execute the local systemd lifecycle boundary;
7. verify that the selected runner became online;
8. observe the runner consuming real queued work;
9. retain an auditable decision and action identity;
10. leave the rest of the local pool inactive.

## What this does not prove

This run does not validate:

- `PROVISION_LOCAL` execution;
- `BURST_CLOUD` execution;
- continuous daemon-style autoscaling;
- scale-to-zero after work completes;
- multi-host coordination;
- high-volume or long-duration autoscale behavior;
- production SLOs or performance characteristics.

Those remain separate product boundaries and should not be inferred from this result.

## Operational findings

### Queue continuity requires recurring observation

Queue age used for scaling is based on RunnerOps-observed continuity.

If controller observations are separated by more than `max_gap_seconds`, the previous sequence is closed with `observation_gap` and a new sequence starts. A supported recurring execution mechanism is therefore important for unattended operation.

For this dogfood, invocations were kept below the 300 second gap limit.

### `ensure .` is a manual capacity override

`runnerctl ensure .` intentionally starts enabled runners mapped to the current repository. During an autoscale test, invoking it changes the capacity state outside the planner and can wake the full mapped pool.

That is not an autoscale failure, but the distinction should remain explicit:

```text
runnerctl ensure .
→ operator-requested repository capacity

runnerctl autoscale run-once .
→ policy-driven exact capacity activation
```

## Result

**PASS for the v0.3 `START_LOCAL` boundary.**

The controlled run demonstrated the intended safety model:

```text
evidence
→ deterministic decision
→ one exact mutation
→ fresh verification
→ durable audit trail
```

The strongest result is not that RunnerOps can issue `systemctl start`.

It is that the action was delayed until the queue condition was proven, scoped to one exact runner, verified against fresh state, and reconstructable afterward from stored evidence.
