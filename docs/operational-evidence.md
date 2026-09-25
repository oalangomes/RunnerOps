# OperationalEvidence v1

`runnerctl report [owner/repo|.] --since DURATION [--json]` is a bounded,
read-only projection of existing RunnerOps evidence. The period is inclusive at
`from` and exclusive at `to`; an unbounded history mode is intentionally absent.

The JSON document has `schema_version: 1`, `kind: OperationalEvidence`,
`period`, `repository`, `ci`, `capacity`, `autoscale`, `collector`, and
`incomplete_evidence`. Autoscale decisions, actions, diagnostics and queue
episode endings come from the read-only `AuditStore`. Current capacity and
collector values come from `CapacitySnapshot`; report collection disables the
collector's persistent job-cache write path.

The current collector does not persist a complete CI run/job history or
historical capacity snapshots. The report therefore exposes current queued-job
evidence and latest capacity separately, while recording
`ci_history_not_persisted`, `historical_capacity_not_persisted`, and
`historical_collector_metrics_unavailable` in `incomplete_evidence`.

The report never starts or stops runners, provisions capacity, requests tokens,
writes audit observations, invokes the controller/planner, or emits raw logs,
stderr, credentials, registry contents, or runner secrets.
