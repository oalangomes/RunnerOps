# Capacity collector observability

RunnerOps treats capacity evidence as a safety boundary. Collector optimizations must not turn stale or incomplete remote state into complete evidence.

## Per-snapshot metrics

`runnerctl capacity ... --json` exposes an additive `collector` object with bounded operational metrics:

- `wall_time_ms`: end-to-end snapshot collection time;
- `github_calls`: total GitHub CLI/API calls made by the collector;
- `repo_resolution_calls`: `gh repo view` canonicalization calls;
- `run_list_calls`: workflow-run list API calls, including pagination;
- `job_list_calls`: per-run jobs API calls, including pagination;
- `runner_list_calls`: repository runner inventory API calls;
- `repo_cache_hits`: successful process-local canonical repository cache hits;
- `canonical_source`: `remote`, `process_cache`, or `fallback`.

The metrics are observational only. They do not participate in planner policy, queue qualification, decision IDs, or durable audit evidence.

## Process-local repository cache

Successful canonical repository resolution is reused for up to 60 seconds inside the same CLI process. This removes redundant `gh repo view` calls during a governed `run-once` iteration and its fresh verification snapshots.

The cache:

- is memory-only and never written to disk;
- is scoped by working directory when the requested repository is `.`;
- also stores the remotely verified canonical `owner/repo` as an alias;
- never caches failed/fallback-only canonicalization;
- expires after 60 seconds;
- does not cache queue, job, runner status, or capacity state.

This intentionally keeps volatile autoscale evidence fresh. In particular, RunnerOps does not currently skip per-run jobs requests based only on a workflow run's `updated_at`; doing so would require a stronger freshness contract before it can be used safely for autoscaling.

## #105 baseline

The metrics make the current amplification explicit:

```text
repository resolution
+ workflow-run listing across supported statuses/pages
+ one jobs listing per discovered active run/page
+ repository runner inventory/pages
```

Real-host dogfood should capture `collector.wall_time_ms` and the call counters before the next optimization slice. The next slice should target the measured dominant source while preserving `CapacitySnapshot` and fail-safe incomplete-evidence semantics.
