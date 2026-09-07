---
name: manage-local-github-runners
description: Manage the user's local GitHub Actions self-hosted runner platform from an AI coding agent. Use when the user asks to inspect, start, stop, restart, diagnose, validate, register, create, remove, or configure local runners for user-owned GitHub repositories, or asks whether a project's runner is healthy/available. Prefer the current repository when no target is specified. Support machine-local runner registries and on-demand systemd operation. Never expose registration tokens or perform destructive removal without explicit user intent.
---

# Manage Local GitHub Runners

Operate the user's shared local GitHub Actions runner installation safely and project-first.

This skill is for **local runner administration**, not for editing GitHub Actions workflows unless the user explicitly asks for workflow changes.

## Platform location

Resolve the central installation in this order:

1. `$ACTIONS_RUNNERS_HOME`
2. `~/actions-runners`

```bash
RUNNERS_HOME="${ACTIONS_RUNNERS_HOME:-$HOME/actions-runners}"
```

If `$RUNNERS_HOME/.env.local` exists, load it before resolving the registry:

```bash
if [[ -f "$RUNNERS_HOME/.env.local" ]]; then
  set -a
  source "$RUNNERS_HOME/.env.local"
  set +a
fi

RUNNERS_CONFIG="${RUNNERS_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/actions-runners/runners.conf}"
RUNNER_BOOT_POLICY="${RUNNER_BOOT_POLICY:-on-demand}"
```

Do not assume runner definitions are versioned. Treat `RUNNERS_CONFIG` as machine-local state.

## Default operating model

The preferred policy is:

```text
systemd authority
+ boot disabled
+ runner started on demand
+ only runners needed by a project are started
```

Do not run `start all` unless the user explicitly requests the entire fleet.

When the user asks to work with "the runner" and a current Git repository exists, scope actions to runners mapped to that repository.

## Resolve the current repository

Use:

```bash
remote="$(git remote get-url origin 2>/dev/null || true)"
repo="$remote"
repo="${repo%.git}"
repo="${repo#git@github.com:}"
repo="${repo#ssh://git@github.com/}"
repo="${repo#https://github.com/}"
repo="${repo#http://github.com/}"
repo="${repo,,}"
```

A valid resolved repo must look like `owner/name`.

If no current repository can be resolved and the user did not name one, ask which repository to manage.

## Resolve runners mapped to a repository

Read `RUNNERS_CONFIG` rather than hard-coding runner names.

```bash
mapfile -t project_runners < <(
  awk -F'|' -v wanted="$repo" '
    /^[[:space:]]*#/ || NF < 5 { next }
    {
      name=$1
      mapped=$4
      enabled=$5

      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", mapped)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", enabled)

      if (tolower(mapped) == tolower(wanted) && tolower(enabled) != "false") {
        print name
      }
    }
  ' "$RUNNERS_CONFIG"
)
```

Never infer that a group covers a repository if the registry says otherwise.

## Common operations

For inventory:

```bash
"$RUNNERS_HOME/runners.sh" list
"$RUNNERS_HOME/runners.sh" groups
"$RUNNERS_HOME/runner-services.sh" plan all
```

For a named runner:

```bash
"$RUNNERS_HOME/runners.sh" status "$runner"
"$RUNNERS_HOME/runners.sh" health "$runner"
"$RUNNERS_HOME/runners.sh" doctor "$runner"
```

Interpret on-demand systemd correctly:

- `active` + boot disabled: healthy and currently available;
- `inactive` + boot disabled: healthy idle runner;
- `failed`: unhealthy;
- registration-deleted evidence in logs: stale remote registration, not a systemd failure.

Do not call an on-demand idle runner broken merely because it is inactive.

### Start the current project's runners

```bash
for runner in "${project_runners[@]}"; do
  "$RUNNERS_HOME/runners.sh" start "$runner"
done
```

Then validate status and health for each runner.

### Stop the current project's runners

```bash
for runner in "${project_runners[@]}"; do
  "$RUNNERS_HOME/runners.sh" stop "$runner"
done
```

Stopping an on-demand runner is normal.

### Switch boot policy

When supported:

```bash
"$RUNNERS_HOME/runner-services.sh" on-demand "$runner"
"$RUNNERS_HOME/runner-services.sh" autostart "$runner"
```

Prefer `on-demand` for local runners unless the user explicitly wants always-on.

## Register a runner for a user-owned repository

Use this when the user asks to create/cadastrar/adicionar a local runner for a user-owned GitHub project.

### Resolve and validate the repository

Accept either the current Git repository or explicit `owner/repo`.

```bash
gh repo view "$repo" --json nameWithOwner,viewerPermission
```

For automatic registration, require enough permission to manage Actions runners.

### Infer profile

Inspect files and workflows:

- `pyproject.toml`, `requirements.txt`, `poetry.lock` -> `python`
- `pubspec.yaml` -> `flutter`
- `package.json` -> `node`
- `pom.xml`, `build.gradle*` -> `java`
- `go.mod` -> `go`
- `*.csproj`, `*.sln` -> `dotnet`
- otherwise -> `generic`

When multiple stacks exist, choose the profile required by Actions workflows.

### Build labels

Always include `local-runner`, plus useful stack/project labels. Do not add the final instance name manually; `configure-runner.sh` adds it.

### Obtain a short-lived registration token

Prefer authenticated GitHub CLI:

```bash
token="$(gh api --method POST "repos/$repo/actions/runners/registration-token" --jq .token)"
```

Never print, persist, or return the token.

If unauthorized, ask the user for the normal GitHub registration line instead of inventing credentials.

### Configure

```bash
github_line="./config.sh --url https://github.com/$repo --token $token"

"$RUNNERS_HOME/configure-runner.sh" \
  --github-line "$github_line" \
  --labels "$labels" \
  --profile "$profile" \
  --group "$group"

unset token github_line
```

Capture the final runner name from output.

Do not manually edit the registry when `configure-runner.sh` can update it.

### Install service and validate

```bash
"$RUNNERS_HOME/runner-services.sh" migrate "$runner_name"
"$RUNNERS_HOME/runners.sh" doctor "$runner_name"
"$RUNNERS_HOME/runners.sh" health "$runner_name"
"$RUNNERS_HOME/runner-services.sh" plan "$runner_name"
```

Under on-demand policy, desired state is service installed, boot disabled, idle until needed.

If the installed platform predates on-demand support, report that instead of using hidden systemd workarounds.

Optionally start to prove the GitHub session, then stop again if the user wants idle state.

## Diagnose failures

If a runner dies after start:

```bash
"$RUNNERS_HOME/runners.sh" logs "$runner" | tail -80
```

If logs contain:

```text
The runner registration has been deleted from the server
```

classify as stale GitHub registration. Do not repeatedly restart it.

Offer re-register or remove. Removal requires explicit user intent.

For duplicate sessions, confirm no other process/service is using the same runner directory or registration before killing anything.

For toolchain/cache issues:

```bash
"$RUNNERS_HOME/cache.sh" profiles
"$RUNNERS_HOME/cache.sh" doctor --profile "$profile"
```

## Remove a runner

Only on explicit remove/delete intent:

1. show resolved name, repo and path;
2. stop it;
3. uninstall via `runner-services.sh uninstall`;
4. remove its registry entry only after confirming the target;
5. remove local directory only if full local removal was requested;
6. do not delete another runner for the same repo.

## Reporting

Keep routine output compact:

```text
Local runner:
- repo: owner/project
- runner: project
- policy: on-demand
- state: active | idle | failed
- boot: disabled
- registration: healthy | stale
```

For new registration also report name, GitHub runner name, profile, labels without secrets, registry path, and systemd state.

## Prohibitions

- Never expose runner registration tokens.
- Never commit the machine-local runner registry.
- Never use `start all` unless explicitly requested.
- Never enable all runners at boot by default.
- Never reconfigure an existing runner without explicit intent.
- Never delete a runner because it is merely inactive under on-demand policy.
- Never edit unrelated workflows while managing local runners.
- Never claim GitHub registration health solely from a local process being alive.
