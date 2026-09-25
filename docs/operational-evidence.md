# OperationalEvidence v1

`runnerctl report [owner/repo|.] --since DURATION [--json]` is a bounded,
read-only projection of existing RunnerOps evidence. The period is inclusive at
`from` and exclusive at `to`; an unbounded history mode is intentionally absent.

The JSON document has `schema_version: 1`, `kind: OperationalEvidence`,
`period`, `repository`, `ci`, `capacity`, `autoscale`, `collector`, and
`collection_status` and `incomplete_evidence`. `collection_status` is `success`
when requested available evidence was collected, and `failed` when runtime
collection evidence is unavailable or inconclusive. Known v1 capability gaps
remain visible in `incomplete_evidence` but do not make the command fail.

Autoscale decisions, actions, diagnostics and queue episode endings come from
the read-only `AuditStore`. Its history window is `since <= timestamp < until`:
decisions use their existing `updated_at` history timestamp, queue observations
use `last_seen_queued_at`, and actions use their own `updated_at`. The report
uses the inclusive `period.from` and exclusive `period.to` bounds for all three.
Actions are included only for decisions returned within that window and limit.
If the history limit is reached, `audit_history_truncated` explicitly marks
the aggregates incomplete.

Current capacity and collector metrics come from `CapacitySnapshot`. The
current master API refreshes jobs for every snapshot; report collection uses
that API directly and does not add a cache override. Collector output is
restricted to known metric fields.

V1 does not persist complete CI run/job history, historical capacity snapshots,
or historical collector metrics. The report exposes current queued-job evidence
and latest capacity separately, while recording `ci_history_not_persisted`,
`historical_capacity_not_persisted`, and
`historical_collector_metrics_unavailable` in `incomplete_evidence`. These
documented capability limitations alone produce exit code 0. Runtime failures,
including `canonical_repository_unavailable`, `audit_history_unavailable`,
`current_capacity_unavailable`, `current_capacity_inconclusive`,
`ci_history_incomplete`, `query_failed`, and `audit_history_truncated`, produce
exit code 3. Capacity collector errors are also runtime failures. All gaps remain
visible in JSON.

The report never starts or stops runners, provisions capacity, requests tokens,
writes audit observations, invokes the controller/planner, provisions capacity,
requests registration tokens, invokes an LLM, or emits raw logs, stderr,
credentials, registry contents, or runner secrets.
