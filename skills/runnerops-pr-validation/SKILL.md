---
name: runnerops-pr-validation
description: Ensure the local GitHub Actions self-hosted runners mapped to the current repository are active before an agent publishes a pull request, then consume GitHub Actions feedback through runnerctl after the push/PR when validation is part of the task. Use runnerctl as the only runner-management and CI-watch interface.
---

# RunnerOps PR Validation

Before publishing a pull request, ensure the current repository's configured local runner capacity is active. After publishing, use the public CI watcher when the task requires waiting for GitHub Actions.

The user's explicit instruction takes precedence. If the user explicitly asks to skip local runner startup, do not block the PR.

## Preconditions

Require the public CLI:

```bash
command -v runnerctl
```

Do not locate the actions-runners checkout yourself and do not call internal scripts such as `runners.sh`, `runner-services.sh`, `svc.sh` or `systemctl` directly.

## Determine whether a local runner is needed

Inspect workflow routing:

```bash
grep -R -n -E 'self-hosted|local-runner' .github/workflows 2>/dev/null || true
```

If no workflow references local/self-hosted routing, runner startup is not required.

## Pre-PR gate

When local routing is present:

```bash
runnerctl ensure .
```

`runnerctl ensure .` resolves the current GitHub repository, finds only enabled runners mapped to that repository, starts them, and validates status/health.

Never replace it with:

```bash
runnerctl start all
```

If `runnerctl ensure .` fails, do not silently publish the PR. Report the real failure.

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

Only after the preflight passes may the agent continue with the requested push / PR creation.

Keep the pre-publish summary compact:

```text
Runner preflight:
- repo: example/project
- status: active
- PR gate: passed
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
- `2`: infrastructure/access failure. Use `runnerctl doctor`, `runnerctl health` or `runnerctl logs` for diagnosis when relevant. Do not remove/recreate runners automatically.
- `3`: timeout, cancellation or inconclusive state. Report that CI is not conclusively green.

The JSON payload may include `pr_number`, `run_id`, `run_attempt`, `workflow`, `job`, `step`, runner metadata and `diagnosis`. Prefer those structured fields over guessing from generic error text.

On a rerun, trust the `run_attempt` returned by `runnerctl`; do not reuse failure details from an older attempt.

A normal post-publish summary can be:

```text
CI feedback:
- repo: example/project
- PR: 123
- status: success | failure | infra | inconclusive
- workflow/job/step: <when available>
```

## Prohibitions

- Do not start the full fleet unless explicitly requested.
- Do not call internal platform scripts directly.
- Do not create/reconfigure/remove runners from this pre-PR skill.
- Do not expose registration tokens.
- Do not bypass a failed local-runner gate.
- Do not claim CI is green without a conclusive watcher result when the task requires CI validation.
- Do not replace `runnerctl ci watch` with aggressive custom polling.
- Do not restart/remove a runner because a test, lint or build step failed.
