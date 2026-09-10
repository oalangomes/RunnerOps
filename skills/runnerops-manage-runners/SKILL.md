---
name: runnerops-manage-runners
description: Manage local GitHub Actions self-hosted runners through runnerctl. Use when the user asks to inspect, start, stop, diagnose, register, create, remove, validate, add immediate CI capacity, or change boot policy for local runners. Prefer repository-scoped or exact-runner operations over shared groups.
---

# RunnerOps Manage Runners

Use `runnerctl` as the stable public interface for lifecycle and provisioning.

Do not discover or call `runners.sh`, `runner-services.sh`, `configure-runner.sh`, `svc.sh` or `systemctl` directly.

Read-only GitHub CLI/API calls are allowed only when remote runner registration/status must be verified and RunnerOps reports the remote state as inconclusive. Never use them to bypass `runnerctl add` or manually obtain a registration token.

## Platform check

```bash
command -v runnerctl
runnerctl platform-doctor
```

If `runnerctl` is missing, report that the platform CLI must be installed from the RunnerOps checkout with `./install.sh`.

For runtime lifecycle, RunnerOps is intentionally non-interactive. If `platform-doctor` reports:

```text
runtime_privileges=not-authorized
```

do **not** invoke `sudo runnerctl ...`, `sudo systemctl ...`, or attempt to obtain credentials. Report that a human must run the one-time setup:

```bash
runnerctl platform-authorize
```

Once authorized, `ensure/start/stop/restart` may run without password prompts.

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

- `active + boot disabled` = healthy and available now;
- `inactive + boot disabled` = healthy provisioned capacity, currently idle;
- `failed` = unhealthy;
- `state=unknown`, `boot=unknown`, `observation=query-error` or missing lifecycle evidence = inconclusive, never healthy by assumption.

`runnerctl doctor` proves the checks it actually reports: local runner structure, lifecycle observability, cache configuration and required commands in PATH. Do not paraphrase that as full functional integrity of Git, Python, package managers or the remote runner.

## Repository scope

For the current repository, prefer:

```bash
runnerctl ensure .
```

This is repository-scoped.

For a known instance, prefer the exact runner:

```bash
runnerctl start <runner>
```

Groups are operational groupings and are **not guaranteed to be repository-scoped**. A shared group may contain runners mapped to different repositories.

Do not use this by default:

```bash
runnerctl start group:my-team
```

Use a group only when the user explicitly intends to operate that whole group after its membership has been inspected.

Never use `runnerctl start all` unless the user explicitly asks for the whole fleet.

## Start and restart verification

After every explicit start or restart, verify the result:

```bash
runnerctl start <runner>
runnerctl status <runner>
runnerctl health <runner>
```

or:

```bash
runnerctl restart <runner>
runnerctl status <runner>
runnerctl health <runner>
```

Do not declare an activation successful from the start command alone.

For `runnerctl ensure .`, the command already performs repository-scoped start plus status/health validation for the matched runners.

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

`runnerctl add` resolves the canonical GitHub `nameWithOwner`, preflights systemd/admin access before requesting a registration token, and passes the short-lived token internally through stdin. Never ask the user to paste or expose a token when this flow is available.

With the default on-demand policy, a successful registration normally finishes as healthy idle capacity:

```text
backend=systemd
state=inactive
boot=disabled
policy=on-demand
```

That means **provisioned**, not necessarily **available now**.

### If add reports PARTIAL

If the command reports a state such as:

```text
[PARTIAL] ... phase=systemd-migrate
```

do **not** repeat `runnerctl add`.

Follow the recovery commands emitted by RunnerOps. The normal recovery path is:

```bash
runnerctl doctor <runner>
runnerctl migrate <runner>
runnerctl status <runner>
runnerctl health <runner>
```

### If add reports INCONCLUSIVE

If the command reports:

```text
[INCONCLUSIVE] ... remote-registration=unknown
```

do **not** blindly repeat `runnerctl add`.

Inspect the local runner with `runnerctl list` / `runnerctl doctor <runner>`. If the remote registration still must be determined, use a read-only GitHub query and verify whether the reported GitHub runner name already exists before attempting any new registration.

Never interpret an inconclusive provisioning phase as either success or absence.

## Provisioned versus available capacity

Infer the requested completion criterion from the user's intent.

If the user asked only to:

- create a runner;
- register a runner;
- configure a runner;

then healthy `inactive + boot disabled + on-demand` is a valid completion state.

If the user asked to:

- add capacity now;
- support the current queue;
- reduce/descongest CI backlog;
- make another runner available;
- help currently queued jobs;

then idle provisioning is **not** enough.

After registration/recovery, start the exact new runner and verify it:

```bash
runnerctl start <runner>
runnerctl status <runner>
runnerctl health <runner>
```

When immediate remote availability is part of the request, also confirm that GitHub reports the runner online before declaring completion. A busy runner is still available capacity that is currently executing work.

## Diagnose stale registration

If a runner starts and immediately dies:

```bash
runnerctl status <runner>
runnerctl health <runner>
runnerctl logs <runner>
```

If GitHub reports that the registration was deleted, do not repeatedly restart or recreate it without first establishing the intended recovery path.

## Remove a runner

Removal is intentionally single-runner and confirmation-gated.

Always preview first:

```bash
runnerctl remove <runner> --plan
```

Verify that the plan names the exact runner, repository, path and systemd unit the user intends to remove.

If the user explicitly requested removal and the target is unambiguous:

```bash
runnerctl remove <runner> --yes
```

Default removal:

- stops the exact runner;
- uninstalls its local systemd unit;
- validates the remote GitHub runner id/name before deletion;
- removes the remote registration when it still exists;
- removes only that registry entry;
- preserves the local runner directory.

Delete the local directory only when the user explicitly requests that too:

```bash
runnerctl remove <runner> --yes --delete-dir
```

For a local detach that intentionally preserves the GitHub registration:

```bash
runnerctl remove <runner> --yes --keep-remote
```

Never use `all` or `group:<group>` with removal. Never infer a destructive target from a repository name when multiple runner instances exist.

## CI feedback

Use the public watcher when the user asks to wait for or diagnose GitHub Actions associated with a published commit or PR:

```bash
runnerctl ci watch . --json
runnerctl ci watch . --pr <number> --json
runnerctl ci watch owner/repo --sha <sha> --json
```

Interpret exit codes strictly:

- `0` — CI success;
- `1` — CI/workflow failure;
- `2` — GitHub access or runner/infrastructure failure;
- `3` — timeout, cancellation or inconclusive result.

A CI failure does not authorize restarting, removing or recreating a runner. Use the returned `workflow`, `job`, `step`, `run_attempt`, runner metadata and `diagnosis` to decide whether the problem is code, workflow or infrastructure.

For a known PR, prefer `--pr <number>` so the watcher resolves the current server-side head SHA. On reruns, trust the reported `run_attempt` and do not reuse stale details from an older attempt.

## Agent Skills

```bash
runnerctl skills list
runnerctl skills install codex
runnerctl skills install copilot
runnerctl skills install claude
```

## Reporting

Keep routine reports compact, but make the completion criterion explicit:

```text
Local runner:
- repo: owner/Project
- runner: project-2
- backend: systemd
- policy: on-demand
- boot: disabled
- lifecycle: active | idle | failed | unknown
- capacity: available-now | provisioned-idle | unavailable | inconclusive
- GitHub: online | offline | busy | unknown
```

Do not claim remote health from local checks alone.

## Prohibitions

- Never expose registration tokens.
- Never invoke `sudo runnerctl`, `sudo systemctl` or interactive privilege escalation to unblock runtime lifecycle; use the one-time human `runnerctl platform-authorize` setup.
- Never repeat `runnerctl add` merely because a previous add returned PARTIAL or INCONCLUSIVE.
- Never treat UNKNOWN/query-error lifecycle state as healthy idle capacity.
- Never start a shared group when repository-scoped or exact-runner operation satisfies the request.
- Never start all runners unless explicitly requested.
- Never enable the entire fleet at boot by default.
- Never delete a runner merely because it is idle.
- Never bypass `runnerctl` with internal scripts unless the task explicitly concerns platform development.
- Never claim CI success from an inconclusive watcher result.
- Never restart/remove a runner merely because a CI step failed.
