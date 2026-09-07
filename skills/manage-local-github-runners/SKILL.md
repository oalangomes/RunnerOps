---
name: manage-local-github-runners
description: Manage local GitHub Actions self-hosted runners through runnerctl. Use when the user asks to inspect, start, stop, diagnose, register, create, validate, or change boot policy for local runners. Prefer the current GitHub repository when no target is specified.
---

# Manage Local GitHub Runners

Use `runnerctl` as the stable public interface.

Do not discover or call `runners.sh`, `runner-services.sh`, `configure-runner.sh`, `svc.sh` or `systemctl` directly.

## Platform check

```bash
command -v runnerctl
runnerctl platform-doctor
```

If `runnerctl` is missing, report that the platform CLI must be installed from the actions-runners checkout with `./install.sh`.

## Inventory and health

```bash
runnerctl list
runnerctl groups
runnerctl status all
runnerctl health all
```

For one runner:

```bash
runnerctl status <runner>
runnerctl health <runner>
runnerctl doctor <runner>
runnerctl logs <runner>
```

Under on-demand policy:

- active + boot disabled = healthy and available;
- inactive + boot disabled = healthy idle capacity;
- failed = unhealthy.

## Current repository

To ensure only the current project's runners are active:

```bash
runnerctl ensure .
```

Do not use `runnerctl start all` unless the user explicitly asks for the whole fleet.

## Start/stop

```bash
runnerctl start <runner>
runnerctl stop <runner>
runnerctl restart <runner>
```

Groups are supported:

```bash
runnerctl start group:my-team
runnerctl health group:my-team
```

## Boot policy

Prefer on-demand for local development runners:

```bash
runnerctl on-demand <runner>
```

Use autostart only when the user explicitly wants always-on capacity:

```bash
runnerctl autostart <runner>
```

## Register a new runner

For the current repository:

```bash
runnerctl add .
```

Or an explicit repository:

```bash
runnerctl add owner/repo
```

Optional overrides:

```bash
runnerctl add . \
  --profile python \
  --group backend \
  --labels python,backend,local-runner \
  --name backend-runner
```

`runnerctl add` uses authenticated GitHub CLI to request a short-lived registration token and passes it through stdin to the internal registration script. Never ask the user to paste or expose a token when this flow is available.

After registration it installs the systemd service and validates doctor/health. With the default on-demand policy, the service should end idle and boot-disabled after validation.

## Diagnose stale registration

If a runner starts and immediately dies:

```bash
runnerctl logs <runner>
```

If GitHub reports that the registration was deleted, do not repeatedly restart it. Offer re-registration or explain that destructive removal is not yet exposed through the stable runnerctl surface.

Do not bypass runnerctl with internal removal commands merely to satisfy the request.

## Agent Skills

```bash
runnerctl skills list
runnerctl skills install codex
runnerctl skills install copilot
runnerctl skills install claude
```

## Reporting

Keep routine reports compact:

```text
Local runner:
- repo: owner/project
- runner: project
- policy: on-demand
- state: active | idle | failed
- registration: healthy | stale
```

## Prohibitions

- Never expose registration tokens.
- Never start all runners unless explicitly requested.
- Never enable the entire fleet at boot by default.
- Never delete a runner merely because it is idle.
- Never bypass `runnerctl` with internal scripts unless the task explicitly concerns platform development.
