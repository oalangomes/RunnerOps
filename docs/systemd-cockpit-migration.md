# systemd + Cockpit

## Current model

The recommended lifecycle is:

```text
runner registration
      ↓
svc.sh
      ↓
systemd unit
      ↓
journalctl
      ↓
optional Cockpit UI
```

`runners.sh` is systemd-first. The default boot policy is `on-demand`: services are installed but disabled at boot until a project or operator starts them.

Legacy PID/process management remains only for compatibility during migration.

## Prerequisites

```bash
systemctl --version
test -d /run/systemd/system
```

On WSL, enable systemd in `/etc/wsl.conf` when necessary:

```ini
[boot]
systemd=true
```

Then run `wsl --shutdown` from PowerShell and reopen the distro.

## Inspect before changing anything

```bash
./runner-services.sh doctor all
./runner-services.sh list
./runner-services.sh plan all
```

## Migrate one runner

```bash
./runner-services.sh migrate my-api
```

The migration:

1. stops the legacy process when present;
2. installs the official `svc.sh` systemd service;
3. applies the cache environment drop-in;
4. starts the service long enough to prove the GitHub session;
5. under `on-demand`, stops it again and leaves boot disabled.

Inspect:

```bash
./runner-services.sh status my-api
./runner-services.sh logs my-api
./runners.sh health my-api
```

Expected on-demand idle state:

```text
state=inactive
boot=disabled
policy=on-demand
```

## Groups

Groups come from the machine-local registry. If an old entry omits the group column, the fallback is the repository slug.

```bash
./runner-services.sh migrate group:my-team
./runner-services.sh status group:my-team
```

Avoid large migrations before reviewing `plan`.

## Switch boot policy

```bash
./runner-services.sh on-demand my-api
./runner-services.sh autostart my-api
```

Use autostart only when a runner must remain available after host boot.

## Cockpit

Install optionally:

```bash
./setup-cockpit.sh install
```

Cockpit provides standard host/service administration for:

- systemd units;
- journal logs;
- CPU and memory;
- disk and processes.

Do not expose the administrative port directly to the public internet. Prefer VPN/private networking and normal Linux user permissions.

## Cache environment

`runner-services.sh` snapshots cache variables into:

```text
.runner-service-env/<runner>.env
```

and creates a systemd drop-in under:

```text
/etc/systemd/system/<unit>.d/10-actions-runners-cache.conf
```

These are machine-local artifacts and are not versioned.

## Rollback

```bash
./runner-services.sh uninstall my-api
```

`uninstall` removes the systemd integration but preserves the GitHub registration and runner directory.

The legacy lifecycle can still be used during migration, but new installations should remain systemd-first.

## Future scale

If a dedicated Linux host eventually needs ephemeral or scale-to-zero runners, evaluate a dedicated runner manager/virtualization layer. Kubernetes is not required merely to operate a small local runner fleet.
