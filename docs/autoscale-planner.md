# Deterministic autoscale planner

`runnerctl autoscale plan [owner/repo|.] [--json]` converts normalized capacity, queue, durable pressure, host, and policy evidence into an `AutoscalePlan`.

It is a **read-only** decision surface. It does not start runners, register capacity, call a cloud provider, or write SQLite.

```text
CapacitySnapshot
+ exact current queue evidence
+ durable aggregate pressure evidence
+ host headroom
+ autoscale policy
        ↓
AutoscalePlan v1
```

The governed controller consumes the same planner function after persisting its observation.

## Usage

```bash
runnerctl autoscale plan .
runnerctl autoscale plan owner/repo --json
```

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | A deterministic `WAIT`, `START_LOCAL`, `PROVISION_LOCAL`, `BURST_CLOUD`, `HOLD`, or `BLOCKED` plan was produced |
| `2` | Invalid CLI arguments or policy |
| `3` | Required evidence was unavailable, contradictory, stale, or incomplete; decision is `INCONCLUSIVE` |

A planned action is evidence, not execution.

## Policy

Policy comes from the normal RunnerOps runtime environment/configuration.

| Variable | Default | Meaning |
| --- | ---: | --- |
| `RUNNER_AUTOSCALE_QUEUE_THRESHOLD_SECONDS` | `300` | Minimum **proved** aggregate queued duration before scale-out can qualify |
| `RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS` | `1` | Host-wide active local capacity ceiling |
| `RUNNER_AUTOSCALE_MIN_MEMORY_AVAILABLE_MIB` | `1024` | Minimum host `MemAvailable` for scale-out |
| `RUNNER_AUTOSCALE_MAX_CPU_PERCENT` | unset | Optional CPU headroom guard |
| `RUNNER_AUTOSCALE_MAX_BURST_RUNNERS` | `0` | Maximum retained active/planned burst actions |
| `RUNNER_AUTOSCALE_COOLDOWN_SECONDS` | `300` | Generic stabilization interval after a scaling action |
| `RUNNER_AUTOSCALE_LOCAL_SCALE_OUT_COOLDOWN_SECONDS` | `30` | Short stabilization interval between justified local `START_LOCAL` actions |
| `RUNNER_AUTOSCALE_BURST_ENABLED` | `false` | Explicitly permits planning cloud burst after local capacity is exhausted |
| `RUNNER_AUTOSCALE_LABEL_SCOPE` | unset | Optional required label subset for queued self-hosted work |

Policy is normalized before its SHA-256 fingerprint is computed.

## Queue age is not the scaling clock

GitHub `job.created_at` remains source provenance only.

RunnerOps does **not** use:

```text
now - job.created_at
```

as the autoscaling qualification clock.

Instead it uses time that RunnerOps itself observed and persisted.

## Two queue evidence layers

The planner intentionally separates exact current-job truth from aggregate capability pressure.

### Exact current-job evidence

Exact queue episodes are keyed by:

```text
repository
+ run_id
+ run_attempt
+ job_id
```

They answer whether an exact GitHub Actions job was observed as queued.

An inconclusive observation closes an exact episode. A later appearance of the same or another job starts a fresh exact episode.

### Aggregate capability pressure

Autoscaling pressure is qualified by exact normalized `required_labels` scope.

Schema v2 persists a durable qualification made from explicitly observed segments:

```text
segment A
→ unknown interval
→ segment B
```

The planner uses:

```text
proven_queued_seconds = Σ observed_segment_seconds
```

Unknown time does not contribute.

A transient inconclusive observation can therefore suspend aggregate pressure and later resume the same capability scope without pretending that the unknown interval was observed.

Example:

```text
955s proven
+ 64s unknown
+ scope resumes
= 955s proven
```

A later 60-second observed segment produces `1015s`, not `1079s`.

See [autoscale-pressure-evidence.md](autoscale-pressure-evidence.md) for the state machine.

## Capability-scope isolation

Each exact normalized label scope qualifies independently.

For example:

```text
[self-hosted, linux, cpu]
[self-hosted, linux, gpu]
```

are distinct evidence boundaries.

An old CPU qualification cannot make a new GPU queue eligible. A young GPU backlog also cannot inflate the capacity delta justified by qualified CPU pressure.

Only current pressure jobs belonging to qualified scopes participate in:

- desired local capacity;
- capacity deficit;
- idle target selection;
- requested capacity delta.

## Read-only temporal projection

The governed controller performs:

```text
observe → persist → plan
```

so its snapshot and durable evidence share a coherent observation timestamp.

Standalone `runnerctl autoscale plan` deliberately does not persist its fresh snapshot. Its T1 snapshot may therefore be newer than durable evidence from T0.

RunnerOps allows a bounded read-only projection only when:

- the durable qualification remains active;
- the same normalized capability scope is present now;
- an exact current job identity anchors the persisted evidence;
- the T0→T1 lag is within the retained gap contract.

The lag is exposed as evidence but never added to `proven_queued_seconds`.

Example:

```text
11:50:00 first proved observation
11:59:30 latest persisted observation
12:00:00 fresh read-only snapshot

proved duration = 570s
projection lag = 30s
```

The planner does not claim `600s`.

If the current anchor disappeared or the lag is unsafe, the plan fails closed with `QUEUE_EVIDENCE_NOT_CURRENT`.

This #108 projection boundary is separate from #107 durable scope resumption. A capability scope can resume **after a complete persisted observation** even when job identities churn; speculative read-only projection still requires an exact current anchor.

## Pressure qualification and capacity

Once a scope has enough `proven_queued_seconds`, the planner considers current capacity.

A current job with `available_now` capacity does not represent pressure.

For qualified pressure jobs RunnerOps computes a bounded desired local capacity and deficit. `requested_capacity_delta` can be greater than one when evidence justifies multiple missing slots, but the v0.3 controller still applies at most one exact `START_LOCAL` action per iteration.

## Decision order

The planner follows a bounded local-first order:

```text
scoped queued work?
  no  → WAIT / LABEL_SCOPE_BLOCKED
  yes
   ↓
required evidence complete/current?
  no  → INCONCLUSIVE
  yes
   ↓
all scoped jobs have available capacity?
  yes → WAIT
  no
   ↓
aggregate proved threshold met?
  no  → WAIT / QUEUE_BELOW_THRESHOLD
  yes
   ↓
cooldown + host guards pass?
  no  → HOLD
  yes
   ↓
matching provisioned-idle runner exists?
  yes → START_LOCAL
  no
   ↓
local capacity target below configured max?
  yes → PROVISION_LOCAL
  no
   ↓
burst disabled?
  yes → BLOCKED
  no
   ↓
burst limit reached?
  yes → HOLD
  no  → BURST_CLOUD
```

`PROVISION_LOCAL` and `BURST_CLOUD` remain planning outcomes until their execution slices are implemented.

## Reason codes

Public reason codes include:

- `NO_SCOPED_QUEUED_WORK`
- `QUEUE_BELOW_THRESHOLD`
- `QUEUE_EVIDENCE_NOT_CURRENT`
- `MATCHING_LOCAL_RUNNER_AVAILABLE`
- `MATCHING_LOCAL_RUNNER_IDLE`
- `OBSERVED_QUEUE_THRESHOLD_MET`
- `SUSTAINED_QUEUE_PRESSURE`
- `PRESSURE_QUALIFICATION_RESUMED`
- `LOCAL_CAPACITY_DEFICIT`
- `LOCAL_CAPACITY_TARGET_REACHED`
- `LOCAL_SCALE_OUT_STABILIZING`
- `LOCAL_POOL_BELOW_MAX`
- `LOCAL_POOL_AT_MAX`
- `HOST_MEMORY_HEADROOM_LOW`
- `HOST_CPU_THRESHOLD_EXCEEDED`
- `COOLDOWN_ACTIVE`
- `BURST_DISABLED`
- `BURST_LIMIT_REACHED`
- `LOCAL_CAPACITY_SATURATED`
- `LABEL_SCOPE_BLOCKED`
- `EVIDENCE_INCONCLUSIVE`

`PRESSURE_QUALIFICATION_RESUMED` appears when a resumed durable qualification contributes to a threshold-qualified scaling decision. It explains provenance; it does not change the policy result by itself.

Reason codes are sorted before serialization.

## AutoscalePlan v1

A plan contains:

```json
{
  "schema_version": 1,
  "kind": "AutoscalePlan",
  "status": "ok",
  "decision_id": "plan-...",
  "timestamp": "...",
  "repository": "owner/repo",
  "policy_fingerprint": "sha256:...",
  "decision": "START_LOCAL",
  "reason_codes": [],
  "requested_capacity_delta": 1,
  "action": {
    "kind": "START_LOCAL",
    "target": "runner-name"
  },
  "evidence": {}
}
```

The evidence includes current queue/capacity facts, host facts, capability scope, aggregate pressure qualification, desired capacity, deficit, and audit-read timing.

Aggregate pressure entries expose enough information to explain resumed proof, including:

- qualification ID/state;
- normalized labels;
- first/latest proved timestamps;
- proved queued seconds;
- current job IDs;
- resume count;
- last resume time;
- last unknown interval length;
- observed segments;
- read-only projection lag when applicable.

The plan ID is derived from schema version, repository, normalized policy, and normalized evidence. Identical inputs therefore produce an identical deterministic plan ID and output ordering.

## Audit-store relationship

The planner reads SQLite but standalone planning does not write it.

The audit store keeps:

- strict exact-job queue episodes;
- schema-v2 aggregate pressure qualifications and observed segments;
- stored decisions/actions from the governed controller.

The existing stored Decision evidence contract remains deliberately closed. The rich planner scope object is not copied wholesale into a Decision record. Aggregate pressure is durable through its own dedicated schema instead.

Therefore:

```bash
runnerctl autoscale plan . --json
runnerctl autoscale explain --decision <plan-id>
```

is not automatically valid. `explain` addresses decisions that a writer explicitly persisted.

## Safety boundaries

- standalone planning never mutates runner lifecycle;
- standalone planning never writes or migrates SQLite;
- GitHub job creation age never qualifies scale-out by itself;
- unknown time never increases proved pressure;
- exact job evidence remains strict;
- capability scopes remain isolated;
- missing/contradictory evidence fails closed;
- burst spending remains deterministic policy, not an LLM decision;
- planner output does not claim GitHub scheduler authority.

## Validation

Relevant contracts include:

```text
tests/test-autoscale-planner-contracts.py
tests/test-autoscale-readonly-plan-timing.py
tests/test-autoscale-pressure-contracts.py
tests/test-autoscale-pressure-planner-contracts.py
```

Together they cover deterministic planning, scoped qualification, bounded read-only projection, resumable durable pressure, reset conditions, restart durability, and a resumed qualification driving a real `START_LOCAL` plan.
