# Continuous autoscale scheduler — Issue #102

`runnerctl autoscale enable [owner/repo|.]` creates a deterministic pair of
**systemd user** units for the canonical GitHub repository. The timer contains no
autoscale policy or runner mutation logic. It only schedules the governed boundary:

```text
systemd --user timer
        -> runnerctl autoscale run-once owner/repo --json
        -> deterministic planner + governed controller
```

The service receives `RUNNER_AUTOSCALE_ENABLED=true`, the operator's HOME/XDG
paths, RunnerOps platform home, runtime configuration and GitHub CLI context. It
is never a root service.

Depending on explicit policy and fresh evidence, one scheduled invocation may:

- activate one exact existing runner with `START_LOCAL`;
- provision one exact bounded local runner with `PROVISION_LOCAL`;
- make no mutation for `WAIT`, `HOLD`, `BLOCKED` or `INCONCLUSIVE`;
- plan `BURST_CLOUD` without executing it.

## Operator flow

```bash
runnerctl autoscale enable .
runnerctl autoscale status .
runnerctl autoscale disable .
```

`enable` resolves `nameWithOwner`, updates the per-repository units in
`${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user`, reloads the user manager and
enables/starts the timer. It rejects root execution so scheduled work stays with
the normal RunnerOps operator user. Repeating it is safe. It schedules future
controller invocations; it does not itself start or register a runner.

`disable` stops and disables only that timer. It leaves runner registrations,
runner directories and `RUNNER_BOOT_POLICY` unchanged, and does not stop active
runner jobs. Repeating it is safe. Unit files are retained so a later `enable` can
reactivate the same deterministic scheduler identity.

`status` retains the CapacitySnapshot shape and adds scheduler state:

```json
{
  "scheduler": {
    "enabled": true,
    "active": true,
    "interval_seconds": 60,
    "service": "runnerops-autoscale-…service",
    "timer": "runnerops-autoscale-…timer",
    "next_run": "…"
  }
}
```

## Cadence and safety

The default `RUNNER_AUTOSCALE_INTERVAL_SECONDS` is 60 seconds. It must be a
positive integer strictly below `RUNNER_AUTOSCALE_QUEUE_GAP_SECONDS`, whose
default is 300 seconds. This keeps scheduled observations frequent enough to
maintain RunnerOps-owned queue/pressure continuity. GitHub `job.created_at`
remains provenance, not the autoscaling clock.

The scheduler does not duplicate:

- queue qualification;
- planner policy;
- host/pool limits;
- capability-scope matching;
- controller lock;
- fresh revalidation;
- exact target selection;
- provisioning/lifecycle recovery;
- verification or audit receipts.

Those remain controller responsibilities. One tick can perform at most one local
mutation, even when the planner reports `requested_capacity_delta > 1`.

## Provisioning through the timer

Local pool growth requires its own explicit policy in addition to the scheduler:

```properties
RUNNER_AUTOSCALE_LOCAL_PROVISION_ENABLED=true
RUNNER_AUTOSCALE_MAX_LOCAL_RUNNERS=4
RUNNER_AUTOSCALE_LOCAL_PROVISION_PROFILE=python
RUNNER_AUTOSCALE_LOCAL_PROVISION_GROUP=my-project
RUNNER_AUTOSCALE_LOCAL_PROVISION_LABELS=self-hosted,Linux,X64,python,local-runner
RUNNER_AUTOSCALE_LOCAL_PROVISION_NAME_PREFIX=my-project-auto
```

Without that policy, the timer cannot create local runner registrations.

When provisioning is justified, the controller creates at most one deterministic
pool slot through the existing safe `runnerctl add` boundary. The created runner
remains on-demand/idle. If pressure persists, a later tick may independently
produce `START_LOCAL` for that exact registration.

No automatic scale-in is implemented. `runnerctl ensure .` remains a separate
manual repository-wide activation override; continuous autoscale neither calls nor
requires it.

## Diagnostics

Use the service name reported by status:

```bash
runnerctl autoscale status .
runnerctl autoscale status . --json
journalctl --user -u runnerops-autoscale-<repository>-<id>.timer --since 30m
journalctl --user -u runnerops-autoscale-<repository>-<id>.service --since 30m
```

The scheduled controller's JSON result is written to the user journal. Durable
audit records remain the source of queue evidence, decisions and action outcomes.
