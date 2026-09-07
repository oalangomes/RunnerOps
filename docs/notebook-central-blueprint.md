# Home lab blueprint for self-hosted runners

A notebook, mini PC or small Ubuntu host can work well as a private CI/lab node when treated as:

```text
private CI + development lab + test environments
```

not as a public production service with guaranteed availability.

## Suggested topology

```text
GitHub
  │
  ▼
Ubuntu host
├── systemd-managed GitHub runners
├── actions-runners management CLI
├── Docker Engine + Compose
├── private VPN / SSH
├── persistent volumes
└── backups
      │
      └── optional heavy workstation/GPU node on demand
```

## Host responsibilities

Run directly on the host:

- SSH / private VPN;
- GitHub Actions runners;
- systemd and journal;
- `runners.sh` / `runner-services.sh`;
- Docker Engine;
- host monitoring and backups.

Application-specific APIs, databases, queues and test stacks should normally live in containers instead of polluting the runner host.

## Data layout

One possible layout:

```text
~/.config/actions-runners/
└── runners.conf

~/.local/share/actions-runners/
└── runners/
    ├── my-api/
    └── my-web/

/srv/stacks/
├── project-a/
└── project-b/

/srv/data/
├── databases/
├── backups/
└── logs/
```

The platform checkout can stay under `~/actions-runners`; runner instances do not need to live inside it.

## Groups and capabilities

Use groups to represent operational ownership or a pool, and labels to represent capabilities.

Examples:

```text
groups:
  backend
  frontend
  mobile

labels:
  python
  node
  flutter
  android
  gpu
  heavy
```

Do not encode one maintainer's project taxonomy into platform code; keep those choices in the local registry.

## Capacity

Runner count is not the same as host capacity.

Start with a small number of concurrent runners and observe:

- CPU load;
- RAM and swap;
- SSD I/O;
- temperature;
- queue time;
- job duration.

Increase concurrency only when measurements support it.

## Test environments

A private runner host can also run integration environments through Docker Compose:

```text
reverse proxy
├── API
├── web
└── supporting services
    ├── database
    └── cache/queue
```

Expose only the minimum required surface. Databases and internal services should usually stay on private Docker networks.

## Remote access

Prefer:

```text
private VPN
  → SSH
  → Cockpit / test APIs
```

Avoid public port forwarding for:

- SSH;
- Cockpit;
- databases;
- Docker socket;
- administrative APIs.

## Heavy worker

A more powerful workstation can be an optional second pool for:

- Android/Flutter builds;
- GPU tasks;
- high-CPU jobs;
- temporary parallelism.

Use explicit labels so normal jobs remain on the efficient host.

## Reliability

For an always-on home-lab host:

- keep the SSD healthy;
- monitor temperature;
- prevent unwanted sleep/suspend;
- keep backups outside the machine;
- consider battery/UPS coverage for host and network equipment;
- use auto-restart after power loss when supported.

## Security

- do not run untrusted pull requests on persistent privileged runners;
- avoid giving runner jobs unrestricted Docker socket access;
- separate secrets by project;
- use least-privilege GitHub tokens;
- isolate autonomous tooling in containers/users when appropriate;
- keep administrative surfaces on a private network.

## Result

```text
small Ubuntu host
→ predictable private CI + test lab

optional heavy workstation
→ on-demand capacity

GitHub Actions
→ orchestration and checks

systemd
→ local runner lifecycle

Docker Compose
→ disposable/private test stacks
```
