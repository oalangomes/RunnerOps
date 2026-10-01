---
name: runnerops-pr-validation
description: Validate pull requests without preemptively waking repository-wide self-hosted capacity. Inspect RunnerOps capacity/autoscale state before publishing, then consume GitHub Actions feedback through runnerctl and use only governed or exact-runner recovery when CI evidence proves capacity is needed.
---

# RunnerOps PR Validation

Before publishing a pull request, inspect the current repository's RunnerOps capacity without waking the whole repository. After publishing, use the public CI watcher when the task requires waiting for GitHub Actions.

The default is capacity-first and autoscale-aware. Do not turn idle on-demand capacity into always-active capacity merely because a PR is about to be published. The user's explicit instruction still takes precedence.

## Preconditions

Require the public CLI:

```bash
command -v runnerctl
```

Do not locate the actions-runners checkout yourself or discover and call RunnerOps internal scripts under `scripts/` directly. Use `runnerctl` as the public interface; do not call `svc.sh` or `systemctl` directly.

## Determine whether a local runner is needed

Inspect workflow routing:

```bash
grep -R -n -E 'self-hosted|local-runner' .github/workflows 2>/dev/null || true
```

If no workflow references local/self-hosted routing, runner startup is not required.

## Capacity-first pre-PR gate

When local routing is present, inspect the public control-plane state without mutating runners:

```bash
runnerctl overview .
runnerctl capacity . --json
runnerctl autoscale status . --json
```

Do not call `runnerctl ensure .` by default. It is a repository-wide manual activation override and can wake every enabled runner mapped to the repository, bypassing the normal capacity-first/autoscale path.

If continuous autoscale is enabled, leave idle runners idle before publication. The scheduler/controller will react to observed queue pressure through the existing governed `START_LOCAL` / `PROVISION_LOCAL` boundaries.

If autoscale is disabled, do not compensate by starting the whole repository. Publish the requested change when the repository itself is ready, then use queue/capacity evidence from the actual CI workload to decide whether one exact runner needs manual activation.

Never replace governed or exact-runner behavior with:

```bash
runnerctl start all
```

`runnerctl ensure .` is allowed only when the user explicitly asks to activate repository-wide capacity.

## Failure diagnosis

Use the public CLI only:

```bash
runnerctl status <runner>
runnerctl health <runner>
runnerctl doctor <runner>
runnerctl logs <runner>
```

If logs indicate that the runner registration was deleted from GitHub, report the stale registration. Do not recreate or remove it automatically.

## Continue the PR

A repository-wide runner start is not a publication prerequisite. Continue with the requested push / PR creation when repository checks are ready and the capacity inspection is not itself inconclusive.

Keep the pre-publish summary compact:

```text
Runner preflight:
- repo: example/project
- capacity: available-now | provisioned-idle | autoscale-managed | inconclusive
- broad activation: not requested
```

## Post-publish CI feedback

When the task includes validating the published push/PR, do not invent CI state or poll GitHub with ad-hoc loops. Use `runnerctl`.

If a PR number is known, prefer the server-authoritative PR head:

```bash
runnerctl ci watch . --pr <number> --json
```

Otherwise, for the current local HEAD:

```bash
runnerctl ci watch . --json
```

Interpret the exit code as part of the contract:

- `0`: CI completed successfully. It is safe to report the CI gate as green.
- `1`: CI/workflow failed. Report the returned workflow/job/step context. Do not blame or restart the runner merely because tests, lint or build failed.
- `2`: infrastructure/access failure. Inspect current `runnerctl capacity . --json` and `runnerctl autoscale status . --json` before deciding whether runner recovery is justified. Do not remove/recreate runners automatically.
- `3`: timeout, cancellation or inconclusive state. Report that CI is not conclusively green.

The JSON payload may include `pr_number`, `run_id`, `run_attempt`, `workflow`, `job`, `step`, runner metadata and `diagnosis`. Prefer those structured fields over guessing from generic error text.

On a rerun, trust the `run_attempt` returned by `runnerctl`; do not reuse failure details from an older attempt.

When CI reports runner/infrastructure unavailability:

1. inspect `runnerctl capacity . --json` and `runnerctl autoscale status . --json`;
2. if autoscale is enabled, prefer the governed controller instead of broad activation; when the task explicitly requires immediate reevaluation, `RUNNER_AUTOSCALE_ENABLED=true runnerctl autoscale run-once . --json` is the existing governed mutation boundary;
3. if autoscale is disabled and current queue evidence identifies exactly one healthy matching `provisioned_idle` runner, start only that exact runner, then verify `status` + `health` and rerun the watcher;
4. if the target is ambiguous or evidence is inconclusive, do not choose or start multiple runners by guess.

Do not call `runnerctl ephemeral create` merely because a job is queued. The one-job ephemeral lifecycle is currently an explicit primitive, not an autoscale planner decision.

A normal post-publish summary can be:

```text
CI feedback:
- repo: example/project
- PR: 123
- status: success | failure | infra | inconclusive
- workflow/job/step: <when available>
```

## Prohibitions

- Do not start the full fleet or repository-wide capacity unless explicitly requested.
- Do not use `runnerctl ensure .` as the normal PR preflight.
- Do not call `runnerctl ephemeral create` as an LLM-selected substitute for autoscale.
- Do not call internal platform scripts directly.
- Do not create/reconfigure/remove runners from this pre-PR skill.
- Do not expose registration tokens.
- Do not bypass a failed local-runner gate.
- Do not claim CI is green without a conclusive watcher result when the task requires CI validation.
- Do not replace `runnerctl ci watch` with aggressive custom polling.
- Do not restart/remove a runner because a test, lint or build step failed.
