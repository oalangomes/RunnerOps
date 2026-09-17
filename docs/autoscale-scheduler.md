# Continuous autoscale scheduler — Issue #102

`runnerctl autoscale enable [owner/repo|.]` creates a deterministic pair of
**systemd user** units for the canonical GitHub repository. The timer contains no
autoscale policy or runner lifecycle logic. It only schedules the proven boundary:

```text
systemd --user timer
        -> runnerctl autoscale run-once owner/repo --json
        -> existing governed controller
```

The service receives `RUNNER_AUTOSCALE_ENABLED=true`, the operator's HOME/XDG
paths, RunnerOps platform home, runtime configuration and GitHub CLI context. It
is never a root service. The existing restricted lifecycle helper remains the only
place that can use its separately authorized administrative boundary when an exact
`START_LOCAL` action is eventually applied.

## Operator flow

```bash
runnerctl autoscale enable .
runnerctl autoscale status .
runnerctl autoscale disable .
```

`enable` resolves `nameWithOwner`, updates the per-repository units in
`${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user`, reloads the user manager and
enables/starts the timer. It rejects root execution so the scheduled service stays
with the normal RunnerOps operator user. Repeating it is safe. It schedules future
controller invocations; it does not start a runner itself.

`disable` stops and disables only that timer. It leaves runner registrations,
runner directories and `RUNNER_BOOT_POLICY` unchanged, and does not stop active
runner jobs. Repeating it is safe. Unit files are intentionally retained so a
later `enable` can update and reactivate the same deterministic identity.
For an explicit `owner/repo`, disable can still use that normalized identity when
GitHub authentication is temporarily unavailable.

`status` retains the existing CapacitySnapshot shape and adds:

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

If user systemd is unavailable, capacity evidence remains visible and the
scheduler field reports an explicit error rather than claiming a running timer.

## Cadence and safety

The default `RUNNER_AUTOSCALE_INTERVAL_SECONDS` is 60 seconds. It must be a
positive integer strictly below `RUNNER_AUTOSCALE_QUEUE_GAP_SECONDS`, whose
default is 300 seconds. This makes scheduled observations frequent enough to
maintain RunnerOps-owned queue continuity. GitHub `job.created_at` is still
provenance, not the autoscaling clock.

The scheduler does not duplicate the planner, controller lock, fresh
revalidation, exact target selection, verification or audit trail. Overlap safety
therefore remains owned by the controller's existing host lock. No automatic
scale-in is implemented. `PROVISION_LOCAL`, `BURST_CLOUD`, `WAIT`, `HOLD`,
`BLOCKED` and `INCONCLUSIVE` remain non-mutating.

`runnerctl ensure .` remains a separate manual repository-wide activation
override; continuous autoscale neither calls nor requires it.

## Diagnostics

Use the service name reported by status:

```bash
runnerctl autoscale status .
runnerctl autoscale status . --json
journalctl --user -u runnerops-autoscale-<repository>-<id>.timer --since 30m
journalctl --user -u runnerops-autoscale-<repository>-<id>.service --since 30m
```

The scheduled controller's JSON result is written to the user journal. The
controller's normal durable audit records remain the source of decisions and
action outcomes.
