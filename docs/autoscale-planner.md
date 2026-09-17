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

A planned action is evidence, not execution. `requested_capacity_delta` is the
bounded local capacity deficit justified by the current plan (and can therefore
be greater than one); it does not mean that multiple runners were started or
provisioned.

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
| `RUNNER_AUTOSCALE_LOCAL_SCALE_OUT_COOLDOWN_SECONDS` | `30` | Stabilization interval between consecutive retained `START_LOCAL` actions while the local capacity deficit remains proven |
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

The planner keeps exact job episodes in audit evidence and also derives a bounded
aggregate window for each exact normalized `required_labels` set. Jobs may hand
off within one scope at the same RunnerOps observation without resetting that
window. Different label sets never share it; a real gap, queue disappearance or
stale/non-continuous current episode resets qualification.

Each label scope qualifies independently. A young GPU scope therefore cannot add
jobs, idle targets or requested capacity to a CPU scope that has already met the
threshold. When at least one scope qualifies, only its current pressure jobs are
used for desired local capacity and the next local target; unqualified scopes stay
visible in evidence but do not block qualified work. If none qualifies, the plan
is `WAIT` with `QUEUE_BELOW_THRESHOLD`.

The planner then uses each qualifying aggregate window's:

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
local scale-out stabilization or action cooldown/headroom guards pass?
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
- `SUSTAINED_QUEUE_PRESSURE`
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
  "requested_capacity_delta": 3,
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
headroom, scoped/pressure job IDs, aggregate pressure windows, qualified label
scopes and job IDs, available/matching capacity, bounded desired local capacity,
capacity deficit and audit-read status used by the plan. It does not contain
credentials, workflow bodies or arbitrary command output.

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

### Durable projection limitation

The #69 `Decision` evidence contract is intentionally closed and currently stores
the normalized current queue references, aggregate capacity counts, action result,
reason codes and requested delta—not the richer #104 `scope` object. The durable
queue-observation history retains the raw episodes required to independently
reconstruct pressure windows, but `autoscale explain` alone cannot reproduce the
qualified-scope selection or desired-capacity arithmetic. Extending that projection
requires a versioned storage-contract change; this slice deliberately leaves the
closed schema unchanged.

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
