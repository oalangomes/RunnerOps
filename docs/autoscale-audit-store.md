# Autoscale audit store — Slice #69

The optional audit store records what RunnerOps observed and can retain a supplied
decision, its reasons, the planned actions and their outcomes. It does not produce
scaling decisions or perform actions. `capacity` and `autoscale status` remain
read-only; no polling loop, controller, apply command or cloud provider is added.

## Storage and dependency choice

The database is `RUNNER_STATE_ROOT/autoscale.db`. The existing runtime configuration
defaults to `${XDG_STATE_HOME:-$HOME/.local/state}/actions-runners/autoscale.db`.
All existing `actions-runners` config/data/cache/state namespaces are preserved.
Relative state roots and paths resolving inside the platform checkout are rejected.

The implementation uses Python 3.8+ and its optional `sqlite3` module linked against
SQLite 3.24+. It requires no separate `sqlite3` executable or external server.
`platform-doctor` reports the capability; history/explain preflight reports missing
or unsupported SQLite explicitly. Traditional lifecycle, diagnostics and CI watch
commands do not import or depend on SQLite. Capacity observation also works without
the module.

Python's [SQLite interface](https://docs.python.org/3/library/sqlite3.html) supplies
bound SQL parameters, exceptions, transaction control and structured rows directly.
A subprocess-based SQLite CLI would add a binary dependency and another quoting,
serialization and error-parsing boundary. No ORM or third-party Python package is
needed. The implementation uses APIs available in Python 3.8, including explicit
SQL transaction boundaries rather than newer Python autocommit arguments.

Writers use `BEGIN IMMEDIATE`, foreign keys, `synchronous=FULL`, and bounded
`busy_timeout`. The journal mode is **DELETE**, deliberately: this slice has short
transactions and no continuous writer. It allows `mode=ro`/`query_only` readers
without WAL/SHM creation or maintenance. SQLite's
[WAL documentation](https://www.sqlite.org/wal.html) explains the sidecars and
read-only opening constraints. WAL can be reconsidered with measured contention
in a future controller; it is not necessary for this audit contract.

Only an explicit internal writer opens/creates a database and applies migrations.
Creation uses a private state directory (`0700`) and file (`0600`). The canonical
`runnerctl init` path creates or tightens `RUNNER_STATE_ROOT` to `0700`, including
existing RunnerOps state roots created under a permissive umask. The audit writer
still rejects an unsafe state root when reached outside that initialization path;
it never weakens the permission check itself. Read commands never create the state
directory, initialize schema, prune records or repair corruption.

## Schema v1

`PRAGMA user_version=1`, a RunnerOps `application_id`, and `schema_migrations`
identify the format. `MIGRATIONS[1]` in `autoscale_store.py` is the explicit 0→1
bootstrap. Schema creation, version advancement and migration receipts commit
together. Reopen is safe; newer/foreign schemas and failed integrity checks return
errors. Future migrations must be separate numbered entries, never implicit
opportunistic column creation on reads.

| Table | Durable purpose |
| --- | --- |
| `schema_migrations` | Applied schema version and timestamp |
| `repository_observations` | One checkpoint per repository: canonical identity, latest observation time, completeness and sanitized digest |
| `queue_observations` | One row per queued episode, keyed by repository/run/attempt/job/first-seen time; consecutive polls update this row |
| `decisions` | Immutable validated decision payload, ID, canonical repository, decision time and latest action update time |
| `actions` | One current record per action ID, linked to a decision by foreign key |
| `action_events` | Immutable per-state receipts, linked to the action; exact event replay never rolls current state backward |

Repository matching is case-insensitive. Canonical casing is supplied by the
CapacitySnapshot or decision producer and preserved. The store does not call
GitHub itself to resolve identity. When a subsequent lookup fails but a known
`match_key` is available, the prior canonical identity is kept and continuity is
broken. An unresolved identity without a prior checkpoint is rejected.

## Queue duration means observed samples

Each queued episode contains:

- `observation_id`, canonical repository, `job_id`, `run_id`, `run_attempt`;
- `first_seen_queued_at`, `last_seen_queued_at`, `observation_count`;
- `github_created_at`, bounded `required_labels`, `max_gap_seconds`;
- `ended_at` and `end_reason`, or null while the stored episode remains open.

Consecutive complete observations of the same identity preserve first-seen and
advance last-seen. The derived `observed_queued_seconds` is **last-seen minus
first-seen**. It never uses GitHub creation time, adds time since the last poll, or
claims uninterrupted scheduler wait between samples. A single sample has duration
zero. The episode can continue across a process restart if samples remain within
the configured maximum gap.

An episode closes on `left_queue`, `inconclusive_observation`, `observation_gap`, or
`evidence_changed` (labels/GitHub creation evidence changed). Reruns have distinct
run attempts and cannot share episodes. A later appearance starts at the new
observation time. Partial pagination/API failure closes existing episodes and
does not seed new continuous episodes from a partial job list. Capacity evidence
may be inconclusive while queue enumeration is complete; that alone does not
invalidate observed queue membership.

Reads derive `continuous_queued=false` for closed or stale episodes, even if no
writer has subsequently closed them. The last observed duration remains visible
as historical evidence. Lowering the gap setting is applied conservatively on
the next observation; increasing it does not revive a previously stale gap under
an older episode's limit. A disappeared job is known absent only when the next
complete observation arrives. No background freshness or retention task exists.

`github_created_at` is optional source evidence. Invalid timestamps become null;
raw invalid text is discarded. It can include dependency/approval waits and
must **never alone trigger future autoscaling**. The future planner #70 will use
RunnerOps-observed episodes with freshness and completeness checks, not substitute
`queue_age_seconds` from CapacitySnapshot for scheduler wait.

## Decision and action input contracts

Decision fields are `decision_id`, `timestamp`, `repository`,
`policy_fingerprint` (`sha256:` plus 64 lowercase hex characters), `decision`,
`reason_codes`, `requested_capacity_delta`, and `evidence`. Supported decision
values are `WAIT`, `START_LOCAL`, `PROVISION_LOCAL`, `BURST_CLOUD`, `HOLD`, `BLOCKED`
and `INCONCLUSIVE`. These are storage vocabulary, not implemented policies.

Evidence is a deliberately closed structure:

- `observed_at`, `queue_status` (`complete`/`inconclusive`), `queued_job_count`;
- `queue`: at most 100 relevant job references, each with run/job/attempt IDs,
  first/last observed times and `continuous_queued`, optionally labels and
  `github_created_at`;
- `capacity`: `available_now`, `busy_capacity`, `provisioned_idle`, `inconclusive`
  and `active_local_runner_count`;
- `active_burst_capacity`, reserved for a future producer.

Counts can be null for unknown evidence. The caller supplies the relevant job
subset and capacity evidence; the store does not infer policy or invent capacity.
Decision/evidence time ordering is checked. Decisions are immutable: exact replay
is a no-op, conflicting payloads under one ID fail with `idempotency_conflict`.

An action has `action_id`, `decision_id`, `kind`, `target`, `state`, `timestamp`,
`started_at`, `finished_at`, `external_id`, and `diagnostic`. Kinds are
`START_LOCAL`, `PROVISION_LOCAL`, `BURST_CLOUD`. Diagnostics contain only a bounded
uppercase `code` and nullable integer `exit_code`; no free-form message or command
output is accepted. IDs/targets/provider IDs are bounded identifiers, not URLs
with credentials or arbitrary JSON. There are at most 100 actions per decision.

Actions begin `planned`; allowed transitions are planned→started/cancelled and
started→succeeded/failed/cancelled. Timestamps must agree with the transition.
The decision, kind and target cannot change. Once present, start time and external
ID cannot change. Each state receipt is unique; replaying an earlier identical
receipt does not regress a terminal outcome. A conflicting receipt is rejected.
An initial decision and its action batch can be written in one transaction.

Idempotency survives reopen while records are retained. The latest identical
repository observation is a no-op. Same-time conflicting observations and older
observations are rejected, rather than rewinding continuity. Checkpoints replace
per-poll receipts: historical observation replay is intentionally unsupported.
After retention removes an ID, indefinite replay deduplication is not promised.
New input timestamps must lie within the retention window and not in the future;
there is no automatic stale-action recovery in this slice.

## Bounded retention

Settings come from exported environment variables or the existing machine-local
`config.env`. Defaults and valid ranges are:

| Variable | Default | Range |
| --- | --- | --- |
| `RUNNER_AUTOSCALE_RETENTION_DAYS` | 30 days | 1–3650 |
| `RUNNER_AUTOSCALE_MAX_RECORDS` | 10000 per principal table | 1–100000 |
| `RUNNER_AUTOSCALE_QUEUE_GAP_SECONDS` | 300 seconds | 1–86400 |
| `RUNNER_AUTOSCALE_BUSY_TIMEOUT_MS` | 2000 ms | 1–5000 |

Pruning runs transactionally before/after writes, or explicitly through the
internal `prune()` method. Queue episodes expire by last-seen; decision retention
uses the last action update, so a recent outcome keeps its original decision.
Expired repository checkpoints are removed. Age limits are supplemented by row
caps: oldest closed episodes and decision bundles without pending actions can be
removed earlier. Actions/events cascade with their decision, preserving correlation.

Decisions with `planned`/`started` actions are protected from automatic deletion.
The incoming decision/checkpoint is also protected from being immediately evicted.
If protected records exhaust a limit, the write fails with
`retention_capacity_exhausted` and rolls back, including pruning. Operators must
resolve pending action records or deliberately change retention/capacity settings;
the store never executes actions to free space.

Payloads are bounded to 32 KiB, labels to 32×64 characters, and there are at most
five event receipts per action. The database also has a 64 MiB page limit; a full
database yields `store_full` with rollback. The transient DELETE rollback journal
can use additional disk space. Pruning reuses free pages; it does not VACUUM or
shrink the historical file high-water mark. No endless per-poll snapshots are saved:
steady queue polls update one checkpoint and the open episode rows.

Retention is a policy applied on writes, not a timer; readers can see old retained
rows until a writer/prune runs. Protected incomplete actions can outlive the age
window. This is an operational audit store, not a high-frequency metrics database,
an immutable compliance ledger, or a distributed coordination service.

## Security boundary

Decision/action schemas reject unknown fields. CapacitySnapshot input is projected
to the queue contract, discarding workflow/job names, arbitrary payloads, URLs,
environment dumps and credentials. It is never stored wholesale. Code/identifier
fields have length and character allowlists; common credential formats and opaque
values present in secret-bearing environment variables are rejected before SQL.
All data values use SQL parameters; SQL errors and rejected values are not echoed.
The tests inspect actual database bytes for token leakage, not just query results.

No string filter can recognize every possible opaque secret. Internal producers
must pass identifiers/evidence, never credentials disguised as allowed labels or
IDs. There is no arbitrary metadata escape hatch. Future payload extensions must
extend the allowlist and tests explicitly. The database is private local state,
not encrypted storage; OS permissions are part of its boundary. Registration
tokens, GH_TOKEN, cloud credentials, workflow bodies and logs have no storage API.

## Read CLI and output v1

```bash
runnerctl autoscale history --since 24h
runnerctl autoscale history --since 24h --json
runnerctl autoscale explain --decision decision-example
runnerctl autoscale explain --decision decision-example --json
```

`--since` accepts a positive integer with `s`, `m`, `h` or `d` (up to 3650 days).
Omitted means retained history. History filters decisions by their latest action
update and queue episodes by last-seen. Results are ordered by descending time,
then ID, with a public limit of 100 per section and explicit `truncated`. Internal
readers can request up to 1000; `explain` retrieves a selected decision regardless
of history truncation. Explain contains all its bounded actions and event receipts.

History JSON contains `schema_version: 1`, `kind: AutoscaleHistory`, `status: ok`,
`since` (UTC/null), `decisions`, `queue_observations`, `limit`, and `truncated`.
Decisions include the validated payload plus `updated_at`. Queue rows include
the episode fields above plus derived `continuous_queued`/`observed_queued_seconds`.
Explain contains `schema_version: 1`, `kind: AutoscaleExplanation`, `status: ok`,
`decision`, and `actions`; each action includes its ordered `events`.

Runtime errors use `{schema_version: 1, kind: AutoscaleAuditError, status: error,
error: CODE}` with exit code `3`. Codes include `store_missing`,
`store_invalid_or_unreadable`, `store_corrupt`, `unsupported_schema`, `store_busy`,
`store_full`, `decision_not_found`, `sqlite_capability_unavailable`,
`sqlite_version_unsupported`, `invalid_settings`, and permission/path errors.
Success is `0`; argument errors are `2`. Shell dispatch errors (missing Python/helper
or unsupported autoscale subcommand) retain the existing `runnerctl` exit code `1`.
Versioned JSON supports additive fields; incompatible meanings require a new version.

## Internal writer API and demonstration

There is intentionally no public `record`, `run`, `enable` or `apply` command.
An internal producer can explicitly persist a CapacitySnapshot:

```bash
# From a RunnerOps checkout; loads the existing machine-local state configuration.
source ./runner-runtime-env.sh
python3 -B - <<'PY'
from capacity import snapshot
from autoscale_store import AuditStore

observed = snapshot("example/project")
with AuditStore(writable=True) as store:
    store.observe(observed)
PY
```

Repeating the explicit capture later advances last-seen for jobs still queued
within the permitted gap. It never calls runner lifecycle or provisions anything.
`AuditStore()` without `writable=True` only reads an existing database.
`record_decision(payload, actions=[...])`, `record_action(payload)` and `prune()`
are internal methods for later producers and current tests; they do not make
policy decisions. Direct Python consumers should also use `-B` so imports do not
create bytecode files in the checkout.

The executable, credential-free demonstration is
`python3 -B tests/test-autoscale-audit-contracts.py`. It uses temporary state to
prove two-poll first/last-seen durability, disappearance, reruns, gaps, decisions,
action outcomes, JSON/human CLI reads, idempotency, migrations, real SQL rollback,
retention saturation and operation of traditional commands without SQLite.
No test writes the actual machine registry or production audit database.

Before a later controller ships, revisit polling cadence/gap policy, crash recovery
of pending actions, and measured lock contention. These are deliberately not
implemented by this storage slice; #70/#71/#73/#72 remain separate work.
