# Governed autoscale controller — Slice #71

`runnerctl autoscale run-once [owner/repo|.] [--json]` is the first mutating
autoscale boundary in RunnerOps. It consumes the deterministic planner introduced
in #70 and may apply **one exact `START_LOCAL` action** for an already provisioned
local runner.

This slice deliberately does not register new runners and does not contact a cloud
provider.

```text
GitHub queue + local/systemd capacity
                ↓
        CapacitySnapshot
                ↓
      SQLite queue continuity
                ↓
      deterministic planner
                ↓
        AutoscalePlan
                ↓
       decision == START_LOCAL ?
          no  → no lifecycle mutation
          yes
           ↓
     policy fingerprint recheck
           ↓
   decision/action audit journal
           ↓
      exact runner start
           ↓
 local status + health verification
           ↓
 GitHub online verification
           ↓
     succeeded / failed
```

## Opt-in and usage

Mutation is disabled by default.

```bash
runnerctl autoscale run-once .
```

With no explicit opt-in this returns `status=disabled` and does not collect a
snapshot, create the SQLite store/controller lock, or invoke runner lifecycle.

Enable a single invocation with:

```bash
RUNNER_AUTOSCALE_ENABLED=true runnerctl autoscale run-once .
RUNNER_AUTOSCALE_ENABLED=true runnerctl autoscale run-once owner/repo --json
```

The variable is read through the normal RunnerOps runtime environment, so a machine
configuration may opt in deliberately. Values accepted as true are `1`, `true`,
`yes` and `on`; false accepts `0`, `false`, `no` and `off`.

## One-shot first

This delivery exposes `run-once` only. A continuously scheduled systemd controller
and public `autoscale enable/disable` commands are intentionally deferred until the
one-shot mutable path is proven on a real host.

That keeps the first mutation boundary small enough to inspect and dogfood:

- one selected repository per iteration;
- at most one exact local runner start;
- no background loop hidden from the operator;
- no automatic `runnerctl add`;
- no cloud/provider execution.

## Queue continuity

`run-once` records the current `CapacitySnapshot` through the #69 audit store before
planning. This is what turns repeated observations into RunnerOps-owned continuous
queue evidence.

The first observation of a newly queued job normally has:

```text
first_seen_queued_at == last_seen_queued_at
observed_queued_seconds = 0
```

and therefore normally produces `WAIT` when the queue threshold is positive. A
later invocation can cross the threshold only if the same `repository + run_id +
run_attempt + job_id + labels + GitHub creation evidence` remains continuous within
the configured observation-gap contract.

GitHub `job.created_at` remains provenance; it never becomes the autoscale clock.

The controller uses repository-scoped SQLite reads for open queue continuity,
latest started scaling action and retained active burst evidence. It does not depend
on the bounded global `autoscale history` view used for human inspection.

## Exact START_LOCAL boundary

Only a plan with:

```text
decision = START_LOCAL
action.kind = START_LOCAL
action.target = <exact local runner id>
```

is executable in this slice.

`PROVISION_LOCAL`, `BURST_CLOUD`, `WAIT`, `HOLD` and `BLOCKED` are returned as
`status=noop`. `INCONCLUSIVE` returns inconclusive and performs no lifecycle
mutation.

The target is passed only as an exact runner id to the existing lifecycle boundary:

```bash
runners.sh start <exact-runner>
```

`all` and `group:*` are rejected by the controller itself even if a malformed plan
somehow reached the mutation layer.

## Concurrency and policy currentness

Before reading mutable autoscale state, an enabled invocation acquires a
non-blocking host lock:

```text
${RUNNER_STATE_ROOT}/autoscale-controller.lock
```

The state root must remain private (`0700`) and the lock file is created with mode
`0600`. A second mutating controller cannot proceed concurrently.

The planner policy is fingerprinted by #70. Immediately before a new action is
persisted/applied, the controller loads policy again and refuses to execute when the
fingerprint changed. A stale decision is never silently applied under different
limits.

## Durable action lifecycle

For a new applicable plan, SQLite records:

```text
decision
  ↓
action: planned
  ↓
action: started
  ↓
exact lifecycle mutation
  ↓
verification
  ↓
action: succeeded | failed
```

The action id is deterministically derived from `decision_id + START_LOCAL + target`.
The controller never records registration tokens, GitHub tokens, environment dumps
or arbitrary command output.

A command exit code of zero is not sufficient for success. After `start`, the
controller requires:

1. exact local `status` success;
2. exact local `health` success;
3. a fresh CapacitySnapshot where the same local runner is active and the GitHub
   registration is `online` (`available_now` or already `busy_capacity`).

Remote verification is bounded by:

```text
RUNNER_AUTOSCALE_VERIFY_TIMEOUT_SECONDS   default 20
RUNNER_AUTOSCALE_VERIFY_INTERVAL_SECONDS  default 1
```

A runner that does not become verifiably online is recorded as failed or
inconclusive rather than reported as successful.

## Restart and replay safety

Before creating a new action, the controller inspects retained pending
`START_LOCAL` actions for the repository.

If a prior invocation died after recording `started` and the target is already
online, the next invocation marks that action succeeded without starting it again.
If evidence is inconclusive, it fails closed. Pending plans are cancelled when they
are no longer valid before execution; actions that had already started are
reconciled against observed state rather than treated as a fresh action.

Completed actions plus planner cooldown prevent immediate duplicate starts over the
same queue pressure. Deterministic decision/action identifiers also provide a
stable audit correlation key.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | disabled, deterministic no-op, or verified successful `START_LOCAL` |
| `1` | lifecycle command or post-start verification failed |
| `2` | invalid CLI argument, planner policy, enable value, or controller setting |
| `3` | required evidence/store/lock state is inconclusive or unavailable |

JSON output uses `kind: "AutoscaleControllerResult"` and includes repository,
decision/reason codes, action id/state/target and a bounded diagnostic code. It does
not embed raw lifecycle output.

## Safety boundaries

The controller does **not**:

- execute `runnerctl add` or `configure-runner.sh`;
- start a whole runner group or `all`;
- provision local pool capacity;
- execute `BURST_CLOUD`;
- contain provider credentials/configuration;
- let an LLM choose whether to mutate or spend cloud money;
- run automatically merely because the code is installed.

The next autoscale capability may consume `PROVISION_LOCAL`, but it must preserve
this controller contract: current evidence, deterministic policy, bounded action,
explicit audit, exact target and verified outcome.
