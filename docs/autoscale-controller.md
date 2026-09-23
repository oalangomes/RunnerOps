# Governed autoscale controller

`runnerctl autoscale run-once [owner/repo|.] [--json]` is the single mutating
autoscale boundary in RunnerOps. It consumes the deterministic planner and may
apply **at most one governed local mutation per invocation**:

- `START_LOCAL` — activate one exact already-provisioned local runner;
- `PROVISION_LOCAL` — create one exact bounded local runner through the existing
  `runnerctl add` provisioning path.

`BURST_CLOUD` remains planning-only. Automatic scale-in/removal is not implemented.

```text
CapacitySnapshot
      ↓
persist/update queue + pressure evidence
      ↓
deterministic planner
      ↓
persist actionable decision
      ↓
acquire host lock
      ↓
recover pending action if present
      ↓
fresh policy + CapacitySnapshot + audit evidence
      ↓
re-run deterministic planner
      ↓
same policy + same decision + same exact target?
      ↓
persist planned → started
      ↓
START_LOCAL       or       PROVISION_LOCAL
exact lifecycle            exact runnerctl add
      ↓                           ↓
structured verification / reconciliation
      ↓
succeeded / failed / bounded inconclusive recovery
```

## Opt-in

The controller itself remains disabled by default:

```bash
RUNNER_AUTOSCALE_ENABLED=true runnerctl autoscale run-once .
```

Without `RUNNER_AUTOSCALE_ENABLED=true`, `run-once` does not collect a snapshot,
create controller state or mutate runner lifecycle.

Local **creation** has a second, independent opt-in and bounded pool policy:

```properties
RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED=false
RUNNER_AUTOSCALE_MAX_LOCAL_RUNNERS=0
RUNNER_AUTOSCALE_LOCAL_PROVISION_PROFILE=
RUNNER_AUTOSCALE_LOCAL_PROVISION_GROUP=
RUNNER_AUTOSCALE_LOCAL_PROVISION_LABELS=
RUNNER_AUTOSCALE_LOCAL_PROVISION_NAME_PREFIX=
RUNNER_AUTOSCALE_LOCAL_PROVISION_RUNNER_VERSION=latest
RUNNER_AUTOSCALE_LOCAL_PROVISION_RUNNER_ARCH=auto
```

Enabling local provisioning requires a positive pool maximum and an explicit
profile, group, label set and name prefix. RunnerOps does not infer a provisioning
template from workflow text, job names or an LLM.

## Active capacity and pool size are different

Two limits intentionally protect different resources:

```text
RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS
    maximum local runners that may be active now

RUNNER_AUTOSCALE_MAX_LOCAL_RUNNERS
    maximum local registrations that may exist in the bounded pool
```

An on-demand/offline registration occupies a pool slot even when it is not active.
The planner only chooses `PROVISION_LOCAL` when qualified pressure still justifies
local capacity, no healthy matching idle target can satisfy the deficit, host
guards pass, the provisioning template matches a qualified label scope and the
bounded pool has room.

## Exact START_LOCAL

A start action always targets one exact local runner. Before mutation the target
must be local, enabled, healthy `provisioned_idle` capacity and correlated to a
trustworthy GitHub registration id. That registration id is stored as the action
`external_id`.

The lifecycle call is always equivalent to:

```bash
runners.sh start <exact-runner>
```

The controller never substitutes `all`, `group:*` or `runnerctl ensure .`.
Success requires fresh structured evidence for the same registration showing the
runner online; both `available_now` and `busy_capacity` are valid verified outcomes.

## Exact PROVISION_LOCAL

Provisioning deliberately reuses the existing safe path instead of duplicating
GitHub token, package, registry or systemd logic:

```text
deterministic pool slot
      ↓
runnerctl add <repo> --plan
      ↓
exact-name apply guard
      ↓
runnerctl add <repo> --name <exact-slot> ...
      ↓
configure-runner + registry + systemd migration
      ↓
fresh CapacitySnapshot
      ↓
exact healthy provisioned_idle registration
```

Pool names are stable lowest-free slots such as:

```text
project-auto-01
project-auto-02
project-auto-03
```

The action target is immutable. `provision_exact` previews the effective name
before apply and exports the internal `RUNNEROPS_EXACT_NAME` guard for the apply
boundary. Normal human `runnerctl add` retains its useful auto-increment behavior;
autoscale exact mode does not.

If a collision appears between preview and apply, `configure-runner.sh` refuses to
auto-increment and atomically reserves only the exact directory. In that narrow
race a short-lived GitHub registration token may already have been issued by
`runnerctl`, but RunnerOps does **not** register a different identity such as
`project-auto-01-2`. The outcome is treated conservatively and is not blindly
retried.

A successful `PROVISION_LOCAL` stops at verified `provisioned_idle`. It does not
start the new runner in the same action. If pressure remains, a later deterministic
cycle can produce `START_LOCAL` for that exact registration.

## Decision before mutation

For either actionable local decision, RunnerOps persists the projected audit
decision before entering the mutating critical section. It then acquires the
non-blocking host lock:

```text
${RUNNER_STATE_ROOT}/autoscale-controller.lock
```

With the lock held, policy and evidence are collected again and the deterministic
planner is rerun. No action crosses its mutation boundary unless the policy
fingerprint, action kind and exact target still agree with the persisted intent.

The controller does not reimplement queue thresholds, capability-scope
qualification, cooldown or host safety policy.

## Durable action lifecycle

Both local mutation kinds use the existing immutable audit model:

```text
decision
  ↓
action: planned
  ↓
action: started
  ↓
external mutation boundary
  ↓
action: succeeded | failed
```

No registration token, GitHub token, arbitrary command output or environment dump
is persisted.

For `START_LOCAL`, `external_id` is the exact registration id known before start.
For `PROVISION_LOCAL`, the external id is recorded only after the newly-created
exact target is positively observed as the expected healthy registration.

## Recovery and no blind retry

Pending actions are reconciled before a new mutation is considered.

For `START_LOCAL`, a retained `started` action is verified/reconciled without
issuing a second lifecycle start blindly.

For `PROVISION_LOCAL`, the rule is stricter because registration may have crossed a
remote side-effect boundary:

- a `planned` action may still be cancelled when policy or plan changes;
- once the action becomes `started`, `runnerctl add` is never submitted again for
  that action;
- exact target observed healthy `provisioned_idle` → `succeeded`;
- exact target present but structurally uncertain → remain inconclusive;
- exact target absent after an uncertain apply → remain inconclusive and preserve
  the same action for operator-safe reconciliation.

This means controller restart cannot turn a partial registration into an automatic
duplicate registration.

## Continuous scheduling

Issue #102 schedules this same `run-once` boundary through a per-repository
**systemd user timer**. There is no second planner or hidden daemon. See
[autoscale-scheduler.md](autoscale-scheduler.md).

One timer tick may perform at most one local mutation. A high
`requested_capacity_delta` therefore describes justified demand; it does not mean
batch provisioning or batch starts.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | disabled, deterministic no-op, or verified successful local mutation |
| `1` | mutation/recovery outcome failed |
| `2` | invalid CLI/policy/controller configuration |
| `3` | required evidence, lock or recovery state is inconclusive/unavailable |

JSON output uses `kind: "AutoscaleControllerResult"` and exposes bounded decision,
action and diagnostic fields. Raw provisioning/lifecycle output is not embedded.

## Safety boundaries

The controller does **not**:

- start a runner group or `all`;
- call `runnerctl ensure .` as a fallback;
- create more than one local runner per iteration;
- automatically delete or scale in runners;
- execute `BURST_CLOUD`;
- contain a cloud provider integration;
- let an LLM decide whether to mutate or spend money;
- run merely because RunnerOps is installed.

The local provisioning boundary is intentionally explicit, bounded, deterministic,
auditable and disabled by default.
