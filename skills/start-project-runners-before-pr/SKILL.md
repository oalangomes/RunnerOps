---
name: start-project-runners-before-pr
description: Ensure the local GitHub Actions self-hosted runners mapped to the current repository are running before an agent creates or opens a pull request. Use when an agent is about to run gh pr create, open/publish a PR, or otherwise hand off a branch for PR review from a local project that may use the shared actions-runners installation. Start only runners configured for the current repository, validate they remain healthy, and block PR creation if a configured required runner cannot stay active. Do not use for PR reviews, PR-body edits, or tasks that are not creating a new PR.
---

# Start Project Runners Before PR

Before creating a pull request, ensure the self-hosted runner capacity for the current repository is online.

The user's explicit instruction takes precedence. If the user explicitly asks to skip local runner startup, do not block the PR.

## Runner home

Resolve the central runner installation in this order:

1. `$ACTIONS_RUNNERS_HOME`
2. `~/actions-runners`

```bash
RUNNERS_HOME="${ACTIONS_RUNNERS_HOME:-$HOME/actions-runners}"

if [[ -f "$RUNNERS_HOME/.env.local" ]]; then
  set -a
  source "$RUNNERS_HOME/.env.local"
  set +a
fi

RUNNERS_CONFIG="${RUNNERS_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/actions-runners/runners.conf}"
```

The runner registry is machine-local state and may live outside the `actions-runners` Git checkout.

Do not assume the current repository is the `actions-runners` repository.

## 1. Determine the current GitHub repository

Require a Git worktree and derive `owner/repo` from `origin`.

```bash
remote="$(git remote get-url origin)"
repo="$remote"

repo="${repo%.git}"
repo="${repo#git@github.com:}"
repo="${repo#ssh://git@github.com/}"
repo="${repo#https://github.com/}"
repo="${repo#http://github.com/}"
repo="${repo,,}"
```

If `origin` is not a GitHub repository, do not invent a mapping.

## 2. Check whether this project expects self-hosted runners

```bash
grep -R -n -E 'self-hosted|local-runner' .github/workflows 2>/dev/null || true
```

If no workflow references self-hosted/local-runner, runner startup is not required.

## 3. Validate the runner central

```bash
test -x "$RUNNERS_HOME/runners.sh"
test -f "$RUNNERS_CONFIG"
```

If either is missing, stop before PR creation and report it.

Do not create, register, replace, delete, or reconfigure runners from this skill.

## 4. Find enabled runners for the current repo

Match the `repo` column in the machine-local `RUNNERS_CONFIG` case-insensitively.

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

Never use `./runners.sh start all` from this skill.

If self-hosted workflows exist but no enabled runner maps to the repository, block PR creation and report that no runner is configured.

## 5. Start only the project's configured runners

```bash
for runner in "${project_runners[@]}"; do
  "$RUNNERS_HOME/runners.sh" start "$runner"
done
```

Treat non-zero as failed preflight.

Do not call `systemctl`, `svc.sh`, `run.sh`, `nohup`, or PID-management commands directly.

## 6. Verify runners stayed active

```bash
failed=0

for runner in "${project_runners[@]}"; do
  status="$("$RUNNERS_HOME/runners.sh" status "$runner" 2>&1 || true)"
  printf '%s\n' "$status"

  if ! grep -q '^\[OK\]' <<<"$status"; then
    failed=1
  fi

  if grep -q 'backend=systemd' <<<"$status" &&
     ! grep -q 'state=active' <<<"$status"; then
    failed=1
  fi
done

if (( failed )); then
  exit 1
fi
```

Then run health for the same runners. If health reports `CRITICAL`, block PR creation.

Warnings need not block unless they show the runner cannot execute jobs.

## 7. Failure handling

If a runner becomes inactive immediately after start:

```bash
"$RUNNERS_HOME/runners.sh" logs "$runner"
```

Look for:

```text
The runner registration has been deleted from the server
```

If found, report stale remote registration. Do not reconfigure or remove automatically.

## 8. Continue PR flow

Only after every enabled runner mapped to the current repo is active may the agent continue with `git push` / `gh pr create`.

Do not change PR metadata because of this skill.

## Success summary

```text
Runner preflight:
- repo: example/example
- runners: example, example-2
- status: active
- PR gate: passed
```

If no self-hosted workflow is present:

```text
Runner preflight: not required for this repository.
```

## Prohibitions

- Do not start every runner globally.
- Do not create a runner or request a registration token.
- Do not edit the machine-local registry directly when platform scripts can manage it.
- Do not remove stale runners automatically.
- Do not bypass an inactive/critical configured runner and silently create the PR.
- Do not claim the runner is online based only on an accepted start command.
