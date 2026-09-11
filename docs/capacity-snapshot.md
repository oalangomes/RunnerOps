# Queue and capacity observability

`runnerctl capacity [owner/repo|.] [--json]` and
`runnerctl autoscale status [owner/repo|.] [--json]` produce the same read-only
`CapacitySnapshot`. Omitted repository means the current Git repository.
Explicit targets accept `owner/repo`. Lookup and registry matching ignore case;
`repository.nameWithOwner` preserves the identity returned by GitHub. If lookup
fails, this field is `null`; a normalized requested slug or Git origin can still
identify local records in `repository.match_key`.

Requirements: Python 3.8+ (standard library), authenticated `gh`, a readable local
registry, and query access to systemd. The collector only invokes `gh repo view`,
`gh api --method GET`, `git remote get-url origin` as a fallback, and
`systemctl show/list-unit-files`. It does not create runtime directories, store
snapshots, read credentials, request registration tokens, or invoke lifecycle
commands. No controller, cloud integration, or apply command is included.

Exit codes: `0` for a complete observation, `3` for an inconclusive observation
(still emitting the snapshot), and `2` for invalid capacity arguments. Unsupported
`autoscale` subcommands are rejected by `runnerctl` with exit code `1`.
`complete` describes evidence, not infrastructure health or an empty queue.

## JSON v1

All fields below are present. Unknown scalar values are `null`, never invented
zeros or empty success results. Additional fields can be added within v1; changing
field meanings or types requires a new schema version.

| Field | Meaning |
| --- | --- |
| `schema_version`, `kind` | Integer `1`, string `CapacitySnapshot` |
| `observed_at` | UTC collection-start timestamp; collection is not atomic |
| `status` | `complete` or `inconclusive` across evidence and classification |
| `repository` | `requested`, canonical `nameWithOwner`, normalized `match_key` |
| `sources` | `repository`, `queue`, `local`, `github_runners`: `complete` or `inconclusive` for collection completeness |
| `errors` | Array of `{source, reason}` collection failures; partial data is retained |
| `queue.status` | Completeness of job enumeration |
| `queue.queued_job_count` | Total observed queued jobs when enumeration is complete; otherwise `null` |
| `queue.observed_queued_job_count` | Number of jobs retained, including partial results |
| `queue.oldest_queued_job_id` | Oldest enumerated queued job; `null` if empty, enumeration incomplete, or any age is unknown |
| `queue.oldest_matching_queued_job_id` | Oldest job with observed matching local capacity by labels, under the same completeness/age rules |
| `queue.jobs` | Job evidence and label-based capacity, described below |
| `capacity.counts` | Counts for all four capacity categories, including remote-only runners as inconclusive; these are observed counts, not totals for unobserved capacity |
| `capacity.runners` | Local records for the repository plus unmatched remote registrations |
| `capacity.matching_basis` | `required_labels` |
| `capacity.remote_scope` | `repository_runners_endpoint` |
| `host.active_local_runner_count` | Active runner count across the entire local registry, including other repositories and disabled records; `null` if registry/systemd observation is incomplete |
| `host.observed_active_local_runner_count` | Known active count even during partial collection |
| `host.status` | Completeness of that host observation |

Each job exposes `job_id`, `name`, `workflow_id`, `workflow_name`, `run_id`,
`run_attempt`, `head_sha`, `head_branch`, `run_status`, `status`, `created_at`,
`queue_age_seconds`, `queue_age_source`, and `required_labels`. Names, workflow
metadata, timestamps and labels can be `null` when absent. Jobs belong to each
run's current attempt as observed; SHA/branch/workflow/attempt identify cohorts
without discarding older active runs or unrelated branches.

`queue_age_seconds` is the integer age of GitHub's **job** `created_at` at collection
start. `queue_age_source` is `job.created_at`. Invalid, absent, timezone-less or
future timestamps yield `null` age/source. Workflow creation and job start times
are not substituted: they do not establish how long this job has been queued.
Job creation age can include dependency or approval waits; it is not a guarantee
of time spent waiting for a runner.

Each job also exposes `capacity_status`, `matching_capacity` (four category
counts), `matching_runner_ids`, and `matching_local_runner_names`. Labels match
case-insensitively and must all be present. Empty/missing labels are unknown,
not a wildcard. Disabled local records do not participate in job matching.

The job status is `available_now` when at least one matched runner is available;
otherwise it is `inconclusive` when candidate evidence is missing, followed by
`busy_capacity`, `provisioned_idle`, or `no_matching_capacity` when none match.
When both busy and provisioned idle candidates exist, the status is
`busy_capacity` and the counts retain both categories. One runner can match many
jobs; do not add job-level counts to estimate independent slots.

Each runner exposes `name`, `registration_id`, `scope` (`local` or `remote_only`),
`enabled` (boolean for local records, otherwise `null`), `local`, `github`,
`category`, and `reason`. `local` contains `unit`, `state`, `boot`, and `reason`;
it is `null` for remote-only registrations. `github` contains `id`, `name`,
`status`, `busy`, and `labels`, or is `null` when registration evidence is missing.

## Classification

Local correlation requires a valid `.runner` ID/name, executable `run.sh`, and
matching remote ID/name. GitHub's `.runner` metadata is accepted as UTF-8 with or
without a BOM. A stored `gitHubUrl`, when present, must match the registry
repository. Duplicate IDs are inconclusive. Units must be loaded and their
`WorkingDirectory` must match the runner directory. Missing `.service` markers can
be recovered through read-only unit discovery; PID files are unused.

| Category | Required evidence |
| --- | --- |
| `available_now` | Enabled local record, valid registration, systemd active/running, GitHub online and `busy: false` |
| `busy_capacity` | Same evidence with `busy: true` |
| `provisioned_idle` | Enabled valid on-demand runner, systemd inactive/dead with successful result and disabled boot, GitHub offline and not busy |
| `inconclusive` | Missing/failed/contradictory evidence, disabled local record, or remote registration without local lifecycle evidence |

Local `state` vocabulary: `active`, `healthy_idle`, `failed`, `unknown`.
GitHub raw status and busy evidence are preserved independently. A failed
systemd unit cannot become provisioned idle. An online remote-only runner is
reported, but cannot satisfy the required local active evidence.

Runner/local reason vocabulary: `systemd_unknown`, `systemd_active`,
`systemd_failed`, `on_demand_inactive`, `registry_disabled`,
`invalid_registration`, `duplicate_registration`, `github_runners_unknown`,
`registration_not_found`, `registration_identity_mismatch`, `github_state_unknown`, `github_labels_unknown`,
`active_online_busy`, `active_online_idle`, `state_disagreement`,
`local_evidence_missing`.

Collection reason vocabulary: `canonical_identity_unavailable`,
`registry_unavailable`, `invalid_registry_record`, `duplicate_registry_record`,
`query_failed`, `incomplete_pagination`, `pagination_limit`, `invalid_run`,
`invalid_job`, `unknown_job_status`, `invalid_runners`.

## CapacitySnapshot as planner input

`runnerctl autoscale plan [owner/repo|.] [--json]` consumes a fresh
`CapacitySnapshot` but does not reinterpret `queue_age_seconds` as scheduler wait.
Threshold-dependent decisions require a matching, continuous queue episode from
the optional audit store and use only the RunnerOps-observed interval
`last_seen_queued_at - first_seen_queued_at`.

The planner combines three evidence classes without mutating any of them:

```text
CapacitySnapshot
+ host memory / optional CPU headroom
+ read-only audit continuity / cooldown / active burst evidence
        ↓
AutoscalePlan
```

A complete CapacitySnapshot is necessary for decisions that depend on local or
GitHub runner state, but it is not sufficient by itself to justify scaling. Missing
or contradictory required evidence produces `INCONCLUSIVE`; it never promotes an
old GitHub creation timestamp into `START_LOCAL`, `PROVISION_LOCAL`, or
`BURST_CLOUD`. See `runnerctl autoscale plan --help` and the audit-store contract
for the continuity boundary.

## Collection boundaries

The collector paginates workflow runs in `queued`, `in_progress`, `waiting`,
`pending`, and `requested` states, then inspects jobs for their current attempts.
Only jobs with status `queued` contribute to queue counts. Duplicate runs/jobs
are removed; the highest observed attempt wins. This includes queued jobs in
workflows already running and avoids historical rerun jobs. Runs can change
between calls, so this is a bounded observation, not scheduler authority.

GitHub limits status-filtered run searches to 1,000 results. Reaching that bound
is inconclusive. Jobs and runners have a defensive 100-page bound; API failures,
malformed collections and pagination gaps are also explicit. Every subprocess
has a 30-second timeout. Counts and oldest-job claims remain unknown when queue
enumeration is incomplete. There is no persistence or cross-poll age tracking in
CapacitySnapshot itself; continuity lives in the separate audit store.
See the [workflow runs API](https://docs.github.com/en/rest/actions/workflow-runs)
and [workflow jobs API](https://docs.github.com/en/rest/actions/workflow-jobs).

Queue reads need Actions read access. The repository runners endpoint needs
repository administration read access (classic tokens require repository admin
access and the appropriate `repo` scope). A denied request makes capacity
inconclusive, even when queue reads succeed. See the
[self-hosted runners API](https://docs.github.com/en/rest/actions/self-hosted-runners#list-self-hosted-runners-for-a-repository).

Remote scope is exactly that repository endpoint. Organization/enterprise pools
not returned there, runner group restrictions, workflow access policies,
concurrency limits, dependencies and approvals are not independently resolved.
`no_matching_capacity` means no matching capacity in this observation; it does
not mean no eligible runner exists anywhere. Label matching and active/online
evidence do not guarantee scheduling or explain every queued job causally.
Host counts are registry facts, not by themselves a claim that it is safe to
activate more runners. The read-only autoscale planner adds explicit memory/CPU,
policy and durable queue-continuity guards before producing a planned decision;
applying that decision remains outside Slice #70.
