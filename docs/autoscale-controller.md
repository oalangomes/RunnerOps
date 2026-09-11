# Governed autoscale controller — Slice #71

`runnerctl autoscale run-once [owner/repo|.] [--json]` is the first mutating
autoscale boundary in RunnerOps. It consumes the deterministic planner introduced
in #70 and may apply **one exact `START_LOCAL` action** for an already provisioned
local runner.

This slice deliberately does not register new runners, provision local capacity or
contact a cloud provider.

```text
CapacitySnapshot
      ↓
persist/update queue observation
      ↓
deterministic planner
      ↓
validate exact START_LOCAL target
      ↓
persist decision
      ↓
acquire host lock
      ↓
recover pending action if present
      ↓
fresh policy + CapacitySnapshot + queue observation
      ↓
re-run deterministic planner
      ↓
same policy + same START_LOCAL + same target/registration?
      ↓
persist planned -> started
      ↓
existing exact runner lifecycle start
      ↓
fresh structured CapacitySnapshot verification
      ↓
succeeded / failed
```

## Opt-in and usage

Mutation is disabled by default.

```bash
runnerctl autoscale run-once .
```

Without explicit opt-in this returns `status=disabled` and does not collect a
snapshot, create SQLite/controller-lock state or invoke runner lifecycle.

Enable one invocation with:

```bash
RUNNER_AUTOSCALE_ENABLED=true runnerctl autoscale run-once .
RUNNER_AUTOSCALE_ENABLED=true runnerctl autoscale run-once owner/repo --json
```

Accepted true values are `1`, `true`, `yes` and `on`; false accepts `0`, `false`,
`no` and `off`.

## One-shot first

This delivery exposes `run-once` only. Continuous polling, a systemd autoscaler
service and public `autoscale enable/disable` commands are intentionally deferred.
The first mutating slice proves the safety boundary before operationalizing it.

The controller is therefore limited to:

- one repository per invocation;
- at most one exact local runner start;
- no hidden background loop;
- no automatic `runnerctl add`;
- no provider/cloud execution.

## Queue continuity

An enabled `run-once` writes each controller `CapacitySnapshot` through the #69
audit store. Repeated observations build RunnerOps-owned continuous queue evidence.

The first observation of a newly queued job has:

```text
first_seen_queued_at == last_seen_queued_at
observed_queued_seconds = 0
```

and therefore normally produces `WAIT` for a positive queue threshold. A later
invocation can cross the threshold only when the same queue identity remains
continuous within the configured observation-gap contract.

GitHub `job.created_at` remains provenance. It is never the autoscale threshold
clock.

Controller planning uses repository-scoped SQLite reads for open queue continuity,
latest started scaling action and retained active-burst evidence instead of the
bounded global history view intended for human inspection.

## Exact START_LOCAL boundary

Only a plan with:

```text
decision = START_LOCAL
action.kind = START_LOCAL
action.target = <exact local runner id>
```

can enter the mutating path.

Before the decision is persisted, the target must exist exactly once in structured
capacity evidence as:

- local;
- enabled;
- `provisioned_idle`;
- healthy on-demand systemd capacity;
- registered in GitHub with a trustworthy registration id.

That registration id is stored as the bounded action `external_id`. Revalidation
and post-start verification require the same identity, so a runner name cannot be
silently rebound to another registration between decision and apply.

`PROVISION_LOCAL`, `BURST_CLOUD`, `WAIT`, `HOLD` and `BLOCKED` remain no-op outcomes.
`INCONCLUSIVE` performs no lifecycle mutation.

The lifecycle call is always:

```bash
runners.sh start <exact-runner>
```

`all` and `group:*` are rejected by the controller boundary.

## Decision before mutation, action after revalidation

For an actionable first plan, RunnerOps persists the projected #69 decision before
trying to enter the mutating critical section. It does **not** create a `planned`
action yet.

The controller then acquires the non-blocking host lock:

```text
${RUNNER_STATE_ROOT}/autoscale-controller.lock
```

The state root must remain private (`0700`) and the lock file is created as `0600`.
Lock contention returns inconclusive, performs no lifecycle mutation and leaves no
new `planned` action. The already-persisted decision remains valid audit evidence of
what that invocation observed and intended.

With the lock held, RunnerOps reloads policy, collects a fresh `CapacitySnapshot`,
updates queue continuity and re-runs the **same deterministic planner**. A new action
is created only when all of these are still true:

- policy fingerprint is unchanged;
- planner still returns `START_LOCAL`;
- exact target is unchanged;
- target registration identity is unchanged;
- target is still trustworthy `provisioned_idle` capacity;
- evidence is conclusive.

The controller does not reimplement cooldown or scaling policy.

## Durable action lifecycle

After successful revalidation, SQLite records:

```text
decision (already durable)
  ↓
action: planned
  ↓
action: started
  ↓
exact lifecycle mutation
  ↓
structured verification
  ↓
action: succeeded | failed
```

The action id is deterministically derived from `decision_id + START_LOCAL + target`.
No registration token, GitHub token, environment dump or arbitrary subprocess output
is written to the audit store.

## Structured post-start verification

The `runners.sh start` exit code is evidence, not proof of success. Verification is
performed from fresh `CapacitySnapshot` evidence rather than parsing human
`status`/`health` output.

Success requires the exact target to remain:

- local and enabled;
- the same GitHub registration id authorized before mutation;
- systemd active/running according to structured local evidence;
- GitHub `online`.

Both `available_now` and `busy_capacity` are valid successful outcomes. The queued
job may be assigned immediately after the runner comes online.

Verification is bounded by:

```text
RUNNER_AUTOSCALE_VERIFY_TIMEOUT_SECONDS   default 20
RUNNER_AUTOSCALE_VERIFY_INTERVAL_SECONDS  default 1
```

A zero start exit with GitHub still offline is not success. Conversely, a non-zero
start exit may still finish as `succeeded` only when the exact structured
postcondition is conclusively satisfied; the original exit code remains in the
action diagnostic.

Verification snapshots are also written through the queue observation API so queue
episodes can end when work leaves the queue.

## Restart and replay safety

Before creating a new action under the lock, the controller checks retained
`planned` / `started` `START_LOCAL` actions for the repository.

- A `planned` action is revalidated against current policy, planner output, exact
  target and registration before it may transition to `started`.
- If a `planned` target became online externally, the action is cancelled rather
  than claiming credit for a start RunnerOps did not perform.
- A previously `started` action is reconciliation work. If the target is already
  online, the existing action is completed as succeeded without another start.
- If a `started` action is not yet online, the controller verifies/reconciles it and
  does not create a second action or blindly issue a duplicate lifecycle mutation.
- Inconclusive recovery evidence fails closed and preserves the non-terminal state
  when safe recovery cannot be proven.

Planner cooldown plus deterministic action identity prevents immediate duplicate
starts over the same queue pressure.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | disabled, deterministic no-op, or verified successful `START_LOCAL` |
| `1` | post-start/recovery outcome failed |
| `2` | invalid CLI argument, planner policy, enable value or controller setting |
| `3` | required evidence/store/lock/recovery state is inconclusive or unavailable |

JSON output uses `kind: "AutoscaleControllerResult"` and contains repository,
decision/reason codes, action id/state/target and a bounded diagnostic code. It does
not embed raw command output.

## Safety boundaries

The controller does **not**:

- execute `runnerctl add` or `configure-runner.sh`;
- start a whole runner group or `all`;
- provision local pool capacity;
- execute `BURST_CLOUD`;
- contain provider credentials/configuration;
- let an LLM choose whether to mutate or spend cloud money;
- run automatically merely because the code is installed.

The acceptance target for #71 is intentionally narrower: prove one exact,
audit-correlated, revalidated local activation end to end before adding continuous
operation or another mutation kind.
