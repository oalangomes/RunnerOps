# Autoscale audit store

RunnerOps persists autoscale evidence locally so decisions can be based on observations it actually made instead of reconstructing state from GitHub job age.

The store records four related kinds of evidence:

1. exact queue observations;
2. aggregate capability-pressure qualifications;
3. deterministic decisions;
4. governed action state and action events.

It is an evidence and audit component. It does not independently decide policy or perform runner lifecycle actions.

## Storage boundary

The database lives at:

```text
RUNNER_STATE_ROOT/autoscale.db
```

The default path is:

```text
${XDG_STATE_HOME:-$HOME/.local/state}/actions-runners/autoscale.db
```

RunnerOps uses Python's built-in `sqlite3` module and SQLite 3.24+.

Writable access uses:

- `BEGIN IMMEDIATE` transactions;
- foreign keys;
- `synchronous=FULL`;
- bounded `busy_timeout`;
- DELETE journal mode;
- a private state directory (`0700`);
- a private database file (`0600`).

Relative state roots, unsafe permissions, and state paths resolving inside the RunnerOps checkout are rejected.

Read-only consumers open the database with SQLite `mode=ro` and `query_only=ON`. They never create directories, initialize schema, prune records, repair corruption, or migrate schema.

## Schema v2

The current store format is:

```text
PRAGMA user_version = 2
```

A RunnerOps `application_id` plus `schema_migrations` identifies the database and migration history.

Migrations are explicit:

```text
v0 → v1  core audit store
v1 → v2  durable aggregate pressure evidence
```

The migration to v2 is performed transactionally by a writable `AuditStore`. Existing v1 queue episodes, decisions, actions, and action events are preserved.

Read-only commands deliberately do **not** migrate an existing v1 database. Schema mutation belongs to the writer boundary. Once a legitimate autoscale writer opens the store, migration happens before new evidence is accepted.

Newer or foreign schemas, failed integrity checks, and incomplete migration receipts fail closed.

## Tables

| Table | Purpose |
| --- | --- |
| `schema_migrations` | Applied schema versions and timestamps |
| `repository_observations` | Latest canonical repository checkpoint and collection completeness |
| `queue_observations` | Strict exact-job queue episodes |
| `pressure_qualifications` | Durable aggregate pressure state per repository + exact normalized capability scope |
| `pressure_segments` | Explicitly observed time segments that contribute to aggregate proved pressure |
| `decisions` | Validated deterministic decision records |
| `actions` | Current governed action state |
| `action_events` | Immutable per-state action receipts |

Repository matching is case-insensitive while canonical repository casing is preserved.

## Exact queue episodes

Exact queue evidence is keyed by:

```text
repository
+ run_id
+ run_attempt
+ job_id
+ first_seen_queued_at
```

An open episode records:

- `first_seen_queued_at`;
- `last_seen_queued_at`;
- `observation_count`;
- optional `github_created_at` provenance;
- bounded `required_labels`;
- `max_gap_seconds`;
- optional end time and end reason.

The proved duration for one exact episode is:

```text
last_seen_queued_at - first_seen_queued_at
```

A single sample therefore proves `0s`.

The exact episode never derives its threshold time from GitHub `job.created_at`.

### Exact episode endings

An exact episode closes on:

- `left_queue`;
- `inconclusive_observation`;
- `observation_gap`;
- `evidence_changed`.

Reruns have distinct `run_attempt` values and cannot share an exact episode.

A transient incomplete collection therefore remains visible as a break in exact per-job continuity. Schema v2 does not weaken that contract.

## Aggregate pressure evidence

Autoscaling pressure is broader than one GitHub job identity. Schema v2 adds a separate durable evidence model keyed by:

```text
repository + exact normalized required-label scope
```

For example:

```text
[self-hosted, linux, cpu]
[self-hosted, linux, gpu]
```

Those are independent qualifications.

An aggregate qualification is composed of one or more observed segments. The proved duration is the sum of those segments:

```text
proven_queued_seconds = Σ segment.observed_seconds
```

Unknown intervals are not segments and therefore cannot increase proved pressure.

Example:

```text
955s observed
+ 64s unknown
+ 60s observed
= 1015s proven
```

not `1079s`.

### Aggregate states

A qualification can be:

```text
active
suspended
ended
```

An inconclusive observation changes `active → suspended` without advancing any segment.

If the same exact normalized capability scope returns within the configured queue-gap bound, RunnerOps resumes the qualification and starts a new zero-duration segment. Previously proved time is retained.

A confirmed disappearance, excessive observation gap, or evidence/capability change ends or resets the affected qualification.

The full state machine and safety rationale are documented in [autoscale-pressure-evidence.md](autoscale-pressure-evidence.md).

## Planner read paths

RunnerOps has two planner entry paths.

### Governed controller

The controller performs:

```text
collect
→ persist observation
→ read planner evidence
→ deterministic plan
```

Its targeted reader returns:

- exact queue episodes;
- current aggregate pressure qualifications;
- active burst evidence;
- latest started scaling action for cooldown.

Because collection and persistence precede planning, this path has a coherent persisted observation timestamp.

### Standalone read-only plan

`runnerctl autoscale plan` intentionally does not write SQLite.

It combines a fresh `CapacitySnapshot` with retained evidence. The #108 temporal-projection rule allows a bounded gap from the latest persisted observation to the fresh snapshot only when an exact current job anchor still proves that the relevant scope is current.

That unpersisted lag never increases proved queue duration.

## Relationship between exact and aggregate evidence

The two evidence models answer different questions.

Exact episodes answer:

> Did this exact GitHub Actions job remain observed as queued?

Aggregate pressure answers:

> How much queue pressure has RunnerOps proved for this exact capability scope?

After a transient unknown observation, a job can have a fresh exact episode while its capability scope resumes previously proved aggregate pressure.

That is intentional and auditable:

```text
exact identity: reset
aggregate capability pressure: suspended → resumed
unknown time: not counted
```

## Decision persistence

A stored decision uses a deliberately closed contract:

- `decision_id`;
- `timestamp`;
- canonical `repository`;
- policy fingerprint;
- decision value;
- stable reason codes;
- requested capacity delta;
- bounded evidence projection.

Supported decisions are:

```text
WAIT
START_LOCAL
PROVISION_LOCAL
BURST_CLOUD
HOLD
BLOCKED
INCONCLUSIVE
```

Decision replay is idempotent. A conflicting payload under the same decision ID fails with `idempotency_conflict`.

The richer planner scope object is not copied wholesale into the closed Decision v1 evidence payload. Schema v2 makes aggregate qualification independently durable in dedicated pressure tables instead of adding an arbitrary metadata escape hatch to stored decisions.

## Action persistence

Actions support:

```text
START_LOCAL
PROVISION_LOCAL
BURST_CLOUD
```

States are:

```text
planned
started
succeeded
failed
cancelled
```

Allowed transitions are:

```text
planned → started | cancelled
started → succeeded | failed | cancelled
```

Each action state has an immutable event receipt. Replay of an identical receipt is a no-op; contradictory replay is rejected.

Diagnostics are bounded structured data. RunnerOps does not persist free-form command output, credentials, workflow bodies, or logs through this API.

## Retention

Relevant settings are:

| Variable | Default | Range |
| --- | ---: | ---: |
| `RUNNER_AUTOSCALE_RETENTION_DAYS` | 30 | 1–3650 |
| `RUNNER_AUTOSCALE_MAX_RECORDS` | 10000 | 1–100000 |
| `RUNNER_AUTOSCALE_QUEUE_GAP_SECONDS` | 300 | 1–86400 |
| `RUNNER_AUTOSCALE_BUSY_TIMEOUT_MS` | 2000 | 1–5000 |

Retention runs inside writer transactions.

Queue episodes expire by last observed time. Aggregate pressure qualifications and their segments are retained under the same bounded-store intent. Decision retention follows the latest action update so a recent action outcome keeps its originating decision.

Pending `planned`/`started` actions are protected from automatic deletion. If protected state exhausts a configured bound, the writer fails with `retention_capacity_exhausted` instead of silently deleting evidence.

The database has a 64 MiB page limit. Payloads and labels are also bounded.

## Security model

RunnerOps projects CapacitySnapshot data into closed storage contracts instead of storing arbitrary snapshots wholesale.

The store rejects unknown decision/action fields and common secret-bearing values. SQL values use bound parameters. Registration tokens, `GH_TOKEN`, cloud credentials, workflow bodies, environment dumps, and logs have no storage API.

This is local operational state, not encrypted secret storage. OS permissions are part of the security boundary.

## Public read commands

```bash
runnerctl autoscale history --since 24h
runnerctl autoscale history --since 24h --json
runnerctl autoscale explain --decision <id>
runnerctl autoscale explain --decision <id> --json
```

These commands are read-only.

`history` exposes persisted decisions and exact queue episodes. Aggregate pressure is primarily consumed through planner evidence and is deliberately kept separate from the existing public History v1 response for this slice.

`explain` resolves only decisions actually persisted by a writer. A standalone `autoscale plan` result is not automatically an explainable stored decision.

Runtime errors use structured error codes and exit `3`; argument errors use exit `2`.

## Internal writer contract

The writer is intentionally internal. A simplified observation flow is:

```python
from capacity import snapshot
from autoscale_store import AuditStore

observed = snapshot("example/project")
with AuditStore(writable=True) as store:
    store.observe(observed)
```

One observation transaction updates:

```text
repository checkpoint
+ exact queue episodes
+ aggregate pressure state/segments
```

or rolls the entire change back.

## Validation

The SQLite contracts include:

```text
tests/test-autoscale-audit-contracts.py
tests/test-autoscale-pressure-contracts.py
tests/test-autoscale-pressure-planner-contracts.py
```

They exercise real SQLite transactions, migration, rollback, idempotency, retention, permissions, exact queue semantics, resumable pressure, process restart, scope isolation, and planner consumption.

The governing invariant is:

> Persist what RunnerOps observed, preserve what it proved, and fail closed on what it does not know.
