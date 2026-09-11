# Deterministic autoscale planner — Slice #70

`runnerctl autoscale plan [owner/repo|.] [--json]` converts current queue/capacity
evidence plus optional durable audit evidence into a deterministic plan. It is a
**read-only** decision surface: this slice does not start a runner, call
`runnerctl add`, contact a cloud provider, enable a controller, or write SQLite.

```text
CapacitySnapshot
+ RunnerOps-observed queue continuity
+ host headroom
+ autoscale policy
        ↓
AutoscalePlan v1
```

The command is intended to make a future controller inspectable before mutation is
introduced. `runnerctl autoscale status` remains the CapacitySnapshot command;
`runnerctl autoscale history` and `explain` remain audit-store readers.

## Usage

```bash
runnerctl autoscale plan .
runnerctl autoscale plan owner/repo --json
```

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | A deterministic `WAIT`, `START_LOCAL`, `PROVISION_LOCAL`, `BURST_CLOUD`, `HOLD` or `BLOCKED` plan was produced |
| `2` | Invalid CLI arguments or autoscale policy |
| `3` | Required evidence was missing, contradictory or unavailable; decision is `INCONCLUSIVE` |

A planned action is evidence, not execution. `requested_capacity_delta: 1` means
"one additional capacity unit would be requested by this plan"; it does not mean
that capacity was started or provisioned.

## Policy

Policy is loaded from the normal RunnerOps runtime environment/config path. There
are no planner-specific CLI policy flags.

| Variable | Default | Meaning |
| --- | ---: | --- |
| `RUNNER_AUTOSCALE_QUEUE_THRESHOLD_SECONDS` | `300` | Minimum RunnerOps-observed continuous queued duration before a scaling action can be planned |
| `RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS` | `1` | Host-wide active local runner ceiling used before `PROVISION_LOCAL` |
| `RUNNER_AUTOSCALE_MIN_MEMORY_AVAILABLE_MIB` | `1024` | Minimum `/proc/meminfo` `MemAvailable` required for a scaling action |
| `RUNNER_AUTOSCALE_MAX_CPU_PERCENT` | unset | Optional host CPU guard; unset disables CPU sampling/guard |
| `RUNNER_AUTOSCALE_MAX_BURST_RUNNERS` | `0` | Maximum retained active/planned burst actions considered allowable |
| `RUNNER_AUTOSCALE_COOLDOWN_SECONDS` | `300` | Minimum elapsed time after the latest retained started scaling action |
| `RUNNER_AUTOSCALE_BURST_ENABLED` | `false` | Explicitly permits planning `BURST_CLOUD` after local options are exhausted |
| `RUNNER_AUTOSCALE_LABEL_SCOPE` | unset | Optional comma-separated labels; unset selects queued self-hosted jobs for the repository |

Invalid booleans, percentages, integer ranges or labels fail with exit `2`. Policy
is normalized before its SHA-256 fingerprint is calculated.

## Queue threshold semantics

GitHub job creation age is **not** the scaling clock.

`CapacitySnapshot.queue.jobs[].queue_age_seconds` is derived from
`job.created_at`. That timestamp may include dependency/approval waiting and is
kept as source evidence only. Threshold-dependent planner decisions join a current
queued job to an exact retained audit episode by:

```text
repository
+ run_id
+ run_attempt
+ job_id
+ required labels
+ continuous_queued=true
```

The planner then uses:

```text
last_seen_queued_at - first_seen_queued_at
```

as `oldest_observed_queued_seconds`. If required continuity cannot be established,
the result is `INCONCLUSIVE`; an old `job.created_at` never substitutes for it.

A current job that already has matching `available_now` capacity is not considered
pressure. If all scoped jobs have available capacity, the plan is `WAIT`. If one
job has available capacity but another scoped job with different labels does not,
the second job remains pressure and is evaluated independently instead of being
hidden by the available runner.

## Decision order

The planner follows a bounded local-first order:

```text
scoped queued work?
  no  -> WAIT / LABEL_SCOPE_BLOCKED
  yes
   ↓
required evidence complete?
  no  -> INCONCLUSIVE
  yes
   ↓
all scoped jobs already have available capacity?
  yes -> WAIT
  no
   ↓
continuous observed queue threshold met?
  no  -> WAIT
  yes
   ↓
cooldown/headroom guards pass?
  no  -> HOLD
  yes
   ↓
matching provisioned-idle local runner exists?
  yes -> START_LOCAL
  no
   ↓
active local pool below max?
  yes -> PROVISION_LOCAL
  no
   ↓
burst disabled?
  yes -> BLOCKED
  no
   ↓
burst limit reached?
  yes -> HOLD
  no  -> BURST_CLOUD
```

`START_LOCAL`, `PROVISION_LOCAL` and `BURST_CLOUD` only describe a future action.
Slice #70 never applies them.

## Reason codes

Public reason codes are uppercase and stable within the v1 contract:

- `NO_SCOPED_QUEUED_WORK`
- `QUEUE_BELOW_THRESHOLD`
- `MATCHING_LOCAL_RUNNER_AVAILABLE`
- `MATCHING_LOCAL_RUNNER_IDLE`
- `OBSERVED_QUEUE_THRESHOLD_MET`
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

Multiple reasons are emitted in sorted order so equivalent plans are byte-stable
under canonical JSON serialization.

## AutoscalePlan JSON v1

The JSON surface contains:

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
  "reason_codes": [
    "MATCHING_LOCAL_RUNNER_IDLE",
    "OBSERVED_QUEUE_THRESHOLD_MET"
  ],
  "requested_capacity_delta": 1,
  "action": {
    "kind": "START_LOCAL",
    "target": "runner-name"
  },
  "evidence": {}
}
```

`status` is `inconclusive` only with decision `INCONCLUSIVE`; other deterministic
decisions use `ok`. `action` is null for decisions that request no capacity.
Evidence contains the normalized observation timestamp, queue/capacity facts, host
headroom, scoped/pressure job IDs and audit-read status used by the plan. It does
not contain credentials, workflow bodies or arbitrary command output.

The plan ID is a SHA-256-derived identifier over schema version, repository,
normalized policy and normalized evidence. The public `timestamp` is the source
CapacitySnapshot observation timestamp rather than a second wall-clock read. As a
result, identical evidence + policy produces identical decision, reason ordering,
plan ID and JSON.

## Audit-store relationship

The planner opens the optional audit store in read-only/query-only mode. It may use
continuous queue episodes, the latest retained started scaling action for cooldown,
and retained `planned`/`started` `BURST_CLOUD` actions as conservative active-burst
evidence.

`plan` does **not** insert its decision into SQLite. Therefore:

```bash
runnerctl autoscale plan . --json
runnerctl autoscale explain --decision <returned-plan-id>
```

does not imply that `explain` will find the plan. `explain` only addresses decisions
actually persisted through the internal audit-store writer API. A future controller
must perform that explicit persistence step before it applies an action.

## Safety boundaries

- no lifecycle mutation;
- no automatic runner registration;
- no cloud/provider calls or credentials;
- no SQLite writes, migrations, pruning or database creation;
- missing/contradictory required evidence fails closed as `INCONCLUSIVE`;
- burst spending cannot be decided by an LLM;
- current label/capacity evidence does not claim GitHub scheduler authority.

Focused contracts live in `tests/test-autoscale-planner-contracts.py`. Capacity and
audit-store contracts remain independent so the planner can be tested as a pure
function with frozen evidence.