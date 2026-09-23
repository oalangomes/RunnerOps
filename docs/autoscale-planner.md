# Deterministic autoscale planner

`runnerctl autoscale plan [owner/repo|.] [--json]` converts normalized capacity,
queue, durable pressure, host and policy evidence into an `AutoscalePlan`.

It is strictly **read-only**. Planning does not start runners, register capacity,
call a cloud provider or write SQLite.

```text
CapacitySnapshot
+ exact current queue evidence
+ durable aggregate pressure evidence
+ host headroom
+ autoscale policy
        ↓
AutoscalePlan v1
```

The governed controller consumes the same planner after persisting its observation.

## Usage

```bash
runnerctl autoscale plan .
runnerctl autoscale plan owner/repo --json
```

| Exit | Meaning |
| --- | --- |
| `0` | deterministic `WAIT`, `START_LOCAL`, `PROVISION_LOCAL`, `BURST_CLOUD`, `HOLD` or `BLOCKED` |
| `2` | invalid CLI/policy |
| `3` | required evidence unavailable, stale, contradictory or incomplete (`INCONCLUSIVE`) |

A planned action is evidence, not execution.

## Policy

Core policy:

| Variable | Default | Meaning |
| --- | ---: | --- |
| `RUNNER_AUTOSCALE_QUEUE_THRESHOLD_SECONDS` | `300` | proved queued duration required for scale-out |
| `RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS` | `1` | host-wide active local capacity ceiling |
| `RUNNER_AUTOSCALE_MIN_MEMORY_AVAILABLE_MIB` | `1024` | minimum host memory headroom |
| `RUNNER_AUTOSCALE_MAX_CPU_PERCENT` | unset | optional CPU guard |
| `RUNNER_AUTOSCALE_MAX_BURST_RUNNERS` | `0` | bounded burst capacity |
| `RUNNER_AUTOSCALE_COOLDOWN_SECONDS` | `300` | generic stabilization interval |
| `RUNNER_AUTOSCALE_LOCAL_SCALE_OUT_COOLDOWN_SECONDS` | `30` | local scale-out stabilization interval |
| `RUNNER_AUTOSCALE_BURST_ENABLED` | `false` | allows planning cloud burst after local capacity is exhausted |
| `RUNNER_AUTOSCALE_LABEL_SCOPE` | unset | optional required label subset |

Local provisioning is a separate explicit policy fragment:

| Variable | Default | Meaning |
| --- | ---: | --- |
| `RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED` | `false` | permits planning/execution of bounded local pool growth |
| `RUNNER_AUTOSCALE_MAX_LOCAL_RUNNERS` | `0` | total local registration ceiling, independent of active limit |
| `RUNNER_AUTOSCALE_LOCAL_PROVISION_PROFILE` | unset | explicit technical profile |
| `RUNNER_AUTOSCALE_LOCAL_PROVISION_GROUP` | unset | explicit operational group |
| `RUNNER_AUTOSCALE_LOCAL_PROVISION_LABELS` | unset | labels the new runner must advertise |
| `RUNNER_AUTOSCALE_LOCAL_PROVISION_NAME_PREFIX` | unset | deterministic pool-slot prefix |
| `RUNNER_AUTOSCALE_LOCAL_PROVISION_RUNNER_VERSION` | `latest` | actions/runner version |
| `RUNNER_AUTOSCALE_LOCAL_PROVISION_RUNNER_ARCH` | `auto` | `auto`, `x64` or `arm64` |

When local provisioning is enabled, max pool/profile/group/labels/prefix must all be
valid and explicit. The normalized fragment participates in the SHA-256 policy
fingerprint.

## Queue time is observed, not inferred

GitHub `job.created_at` is provenance only. RunnerOps never qualifies scale-out
from `now - job.created_at`.

Exact queue episodes are keyed by:

```text
repository + run_id + run_attempt + job_id
```

Aggregate pressure is independently qualified by exact normalized
`required_labels` scope. Schema v2 can suspend a capability qualification across a
bounded unknown interval and resume it later without counting unknown time:

```text
segment A → unknown → segment B
proved_queued_seconds = observed(A) + observed(B)
```

See [autoscale-pressure-evidence.md](autoscale-pressure-evidence.md).

## Capability-scope isolation

Exact scopes qualify independently. For example:

```text
[self-hosted, linux, cpu]
[self-hosted, linux, gpu]
```

An old CPU qualification cannot qualify new GPU work, and a young GPU backlog
cannot inflate the local deficit justified by CPU pressure.

Only current jobs belonging to threshold-qualified scopes contribute to desired
local capacity, deficit and target selection.

## Read-only temporal projection

The controller can use coherent `observe → persist → plan` evidence. Standalone
`autoscale plan` does not write its fresh snapshot, so it may project a recent
durable qualification only when the same scope and an exact current job anchor are
present within the retained gap contract.

Projection lag is exposed but is never added to `proved_queued_seconds`. Unsafe or
unanchored projection fails closed with `QUEUE_EVIDENCE_NOT_CURRENT`.

## Local capacity model

RunnerOps distinguishes:

```text
active local capacity
    runners currently active

provisioned local pool
    all local runner registrations, including on-demand/offline/disabled records
```

`RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS` bounds the former.
`RUNNER_AUTOSCALE_MAX_LOCAL_RUNNERS` bounds the latter.

For threshold-qualified pressure the planner computes a bounded desired active
capacity and `requested_capacity_delta`. The delta may be greater than one, while
the controller still applies at most one local mutation per invocation.

## Decision order

```text
scoped queued work?
  no  → WAIT / LABEL_SCOPE_BLOCKED
  yes
   ↓
required evidence complete/current?
  no  → INCONCLUSIVE
  yes
   ↓
all scoped jobs already have available capacity?
  yes → WAIT
  no
   ↓
proved threshold met?
  no  → WAIT / QUEUE_BELOW_THRESHOLD
  yes
   ↓
cooldown + host guards pass?
  no  → HOLD
  yes
   ↓
matching healthy provisioned-idle runner exists?
  yes → START_LOCAL exact target
  no
   ↓
active capacity deficit > 0?
  no  → local active target reached → burst policy
  yes
   ↓
local provisioning explicitly enabled?
  no  → local path blocked → burst policy
  yes
   ↓
actual local pool below max + template matches qualified scope?
  yes → PROVISION_LOCAL exact deterministic slot
  no  → local path blocked → burst policy
   ↓
burst disabled? → BLOCKED
burst limit reached? → HOLD
otherwise → BURST_CLOUD (planning only)
```

`START_LOCAL` always takes precedence over creating another registration when a
healthy matching idle runner can satisfy the deficit.

## Deterministic provisioning target

When `PROVISION_LOCAL` qualifies, the planner selects the lowest free pool slot:

```text
<prefix>-01
<prefix>-02
<prefix>-03
```

The decision exposes:

- `current_local_pool_size`;
- `max_local_pool_size`;
- `selected_provisioning_scope`;
- `provisioning_template_labels`;
- `provisioning_target`.

The exact target is part of deterministic plan evidence. A fresh replan over the
same inventory therefore resolves the same slot, which is essential for replay-safe
controller recovery.

## Important reason codes

Alongside existing queue/host/cooldown reasons, local provisioning adds or gives
precise semantics to:

- `LOCAL_CAPACITY_DEFICIT`
- `LOCAL_POOL_BELOW_MAX`
- `LOCAL_POOL_AT_MAX`
- `LOCAL_PROVISION_DISABLED`
- `LOCAL_PROVISION_TEMPLATE_INCOMPATIBLE`
- `LOCAL_POOL_EVIDENCE_INCONCLUSIVE`
- `LOCAL_POOL_SLOT_INCONCLUSIVE`
- `SUSTAINED_QUEUE_PRESSURE`
- `OBSERVED_QUEUE_THRESHOLD_MET`

`LOCAL_POOL_AT_MAX` refers to the actual bounded registration pool, not merely the
active-runner ceiling.

## AutoscalePlan v1

Example start:

```json
{
  "decision": "START_LOCAL",
  "requested_capacity_delta": 1,
  "action": {"kind": "START_LOCAL", "target": "runner-name"}
}
```

Example provisioning decision:

```json
{
  "decision": "PROVISION_LOCAL",
  "requested_capacity_delta": 3,
  "action": {"kind": "PROVISION_LOCAL", "target": "project-auto-02"},
  "evidence": {
    "scope": {
      "current_local_pool_size": 2,
      "max_local_pool_size": 4,
      "provisioning_target": "project-auto-02"
    }
  }
}
```

A delta of three does **not** mean three runners are created in one controller
iteration.

The plan ID is derived from schema version, repository, normalized policy and
normalized evidence, so identical normalized inputs produce the same plan ID and
output ordering.

## Audit relationship

Standalone planning reads durable evidence but does not write it. The audit store
keeps exact queue episodes, aggregate pressure qualifications and decisions/actions
explicitly persisted by the controller.

The rich planner scope is not copied wholesale into the closed stored Decision
contract; aggregate pressure remains durable through its own schema.

## Safety boundaries

- standalone planning never mutates runners or SQLite;
- missing/contradictory evidence fails closed;
- unknown time never increases proved pressure;
- exact scopes remain isolated;
- provisioning requires explicit deterministic policy;
- burst remains a policy decision, not an LLM decision;
- planner output does not claim GitHub scheduler authority.

## Validation

Relevant permanent contracts include:

```text
tests/test-autoscale-planner-contracts.py
tests/test-autoscale-readonly-plan-timing.py
tests/test-autoscale-pressure-contracts.py
tests/test-autoscale-pressure-planner-contracts.py
tests/test-autoscale-provision-contracts.py
tests/test-autoscale-provision-planner-contracts.py
tests/test-autoscale-provision-run-once-contracts.py
```
