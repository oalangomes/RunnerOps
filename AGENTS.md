# Contributor and agent guidance

This repository manages local GitHub Actions self-hosted runners.

## Architectural invariants

- The Git repository contains platform code, not machine inventory.
- Machine-specific configuration/data/cache/state belong under `RUNNERS_CONFIG`, `RUNNER_DATA_ROOT`, `RUNNER_CACHE_ROOT` and `RUNNER_STATE_ROOT`; normal operation must not write into the Git checkout.
- Never commit registration tokens, machine-local registry contents or runner credentials.
- systemd is the lifecycle authority for migrated runners.
- `RUNNER_BOOT_POLICY=on-demand` is the default and an inactive/boot-disabled runner may be healthy idle capacity.
- `runnerctl` is the stable public CLI; internal scripts are implementation details.
- Prefer provider-neutral Agent Skills under `skills/<name>/SKILL.md`.
- Do not introduce provider-specific copies unless a client cannot express the behavior through the shared skill.

## Change discipline

- Keep public examples generic; do not add maintainer usernames, hostnames or project names.
- Prefer repository slug as the default group; project-specific grouping belongs in machine-local configuration.
- Do not expand the legacy PID lifecycle. New operational features should use systemd/journal/Cockpit.
- Preserve existing runner registrations and local directories unless a change explicitly targets migration/removal.

## Validation

For shell changes:

```bash
bash -n configure-runner.sh runners.sh runner-services.sh runner-runtime-env.sh \
  init-machine-config.sh sync-local-git-excludes.sh install-agent-skills.sh runnerctl install.sh runner-package.sh
```

For Agent Skills:

```bash
./install-agent-skills.sh --list
./install-agent-skills.sh --tool all --dry-run
```

Optional local code-navigation tools may be used when installed, but they are not prerequisites for contributing to this repository.
