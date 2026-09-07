---
name: start-project-runners-before-pr
description: Ensure the local GitHub Actions self-hosted runners mapped to the current repository are active before an agent creates or opens a pull request. Use immediately before git push / gh pr create when the repository uses self-hosted or local-runner workflows. Use runnerctl as the only runner-management interface.
---

# Start Project Runners Before PR

Before publishing a pull request, ensure the current repository's configured local runner capacity is active.

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

Keep the summary compact:

```text
Runner preflight:
- repo: example/project
- status: active
- PR gate: passed
```

## Prohibitions

- Do not start the full fleet unless explicitly requested.
- Do not call internal platform scripts directly.
- Do not create/reconfigure/remove runners from this pre-PR skill.
- Do not expose registration tokens.
- Do not bypass a failed local-runner gate.
