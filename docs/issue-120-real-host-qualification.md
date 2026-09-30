# Issue #120 real-host ephemeral qualification

This document records the real-host qualification of the local one-job ephemeral
runner lifecycle introduced by Issue #120.

The result is engineering evidence for one controlled run. It is not a benchmark,
a soak test, or evidence for autoscaling. Machine-specific paths, usernames,
hostnames, persistent runner names, and credentials are intentionally omitted.
The corresponding sanitized machine-readable record is
[`docs/evidence/issue-120-real-host-qualification.json`](evidence/issue-120-real-host-qualification.json).

## Result

**PASS — CODE COMPLETE / CONTRACTS GREEN / REAL-HOST QUALIFICATION PASSED**

```text
persistent pool healthy
        ↓
exact ephemeral identity allocated
        ↓
fail-closed uncertainty reconciled
        ↓
exact runner ONLINE
        ↓
one controlled GitHub Actions job
        ↓
exact runner BUSY and consumes the job
        ↓
local exit + remote disappearance reconciled
        ↓
TERMINAL
        ↓
bounded idempotent cleanup
        ↓
CLEANED
        ↓
persistent pool unchanged and healthy
```

## Qualified revision

| Field | Evidence |
| --- | --- |
| Repository | this RunnerOps repository |
| Branch | `feat/issue-120-ephemeral-lifecycle` |
| Feature commit | `1f195b8` |
| Compatibility fix | `3c83dda` |
| Qualified SHA | `3c83ddadef6f43c3de1638c2cca9239342f60c45` |
| Workflow run | [`36785377870`](https://github.com/oalangomes/RunnerOps/actions/runs/36785377870) |
| Workflow conclusion | `success` |

## Exact lifecycle identity

| Field | Evidence |
| --- | --- |
| Action ID | `81008759391c4b4cbd508d4e993ad789` |
| Runner identity | `runnerops-ephemeral-557bb9f9d6e8eec2` |
| Registration attempts | `1` |
| Final action state | `CLEANED` |
| Final remote state | `ABSENT` |
| Final local state | `CLEANED` |

The action ID always mapped to the same runner identity. No suffix fallback or
second identity was created during recovery.

## Fail-closed recovery evidence

The first remote observation was inconclusive because the installed GitHub CLI
did not support the `gh api --slurp` flag used by the initial adapter.

RunnerOps responded by transitioning to `INCONCLUSIVE_REGISTRATION` before
requesting registration material:

```text
registration.attempted=false
registration.attempts=0
github_observation.status=UNKNOWN
final_reason=exact_identity_not_proven_absent_before_registration
```

No blind retry occurred. The adapter was changed to use bounded explicit GET
pages supported by the documented repository runners endpoint. Two consecutive
observations then proved that the exact remote identity was absent:

```text
INCONCLUSIVE_REGISTRATION
  → exact_remote_absence_requires_bounded_confirmation
INCONCLUSIVE_REGISTRATION
  → repeated_exact_remote_absence_proves_registration_retry_safe
REQUESTED
```

The same action ID resumed after reconciliation. The eventual successful
registration still recorded exactly one registration attempt.

## Observed timeline

Timestamps are UTC and come from the durable action evidence.

| Timestamp | State | Evidence/reason |
| --- | --- | --- |
| `22:21:08.441` | `REQUESTED` | Exact action and disposable root allocated |
| `22:21:28.391` | `INCONCLUSIVE_REGISTRATION` | Remote observation unknown; mutation refused |
| `22:23:43.713` | `REQUESTED` | Repeated exact absence authorized safe retry |
| `22:23:50.926` | `REGISTERING` | Registration initiated |
| `22:23:58.460` | `REGISTERED` | Local configuration succeeded with `--ephemeral` |
| `22:24:01.770` | `ONLINE` | Exact local and GitHub identity observed |
| `22:25:03.726` | `BUSY` | Exact GitHub runner reported busy; workload persisted |
| `22:25:45.359` | `TERMINAL` | Workload observed, local exit and remote absence proven |
| `22:26:58.870` | `CLEANUP_PENDING` | Bounded cleanup started |
| `22:26:59.211` | `CLEANED` | Remote/local/root cleanup completed |

## Controlled workload proof

The published `validate.yml` revision was dispatched with the exact runner label
and action ID. GitHub's server-side job record reported:

```json
{
  "name": "One controlled ephemeral job",
  "status": "completed",
  "conclusion": "success",
  "runner_name": "runnerops-ephemeral-557bb9f9d6e8eec2",
  "labels": [
    "self-hosted",
    "Linux",
    "X64",
    "runnerops-ephemeral-557bb9f9d6e8eec2"
  ]
}
```

The generic self-hosted dogfood job was `skipped`, so it could not compete for
the one-job runner. The qualification job contained no checkout and ran only the
fixed repository-owned identity assertion and bounded observation delay.

RunnerOps independently observed the exact registration as `BUSY` and persisted:

```text
workload_evidence.observed=true
first_busy_at=2026-09-30T22:25:03.723526+00:00
```

The durable lifecycle intentionally records `job_conclusion=unknown`: runner
process exit alone is not interpreted as job success. The `success` conclusion
above is separate, authoritative GitHub workflow evidence.

## Cleanup proof

The first cleanup converged to:

```json
{
  "result": "SUCCEEDED",
  "attempts": 1,
  "remote_removed": true,
  "local_stopped": true,
  "root_removed": true,
  "reason": "exact_remote_local_and_root_cleanup_complete"
}
```

Repeating cleanup returned the same `CLEANED` state and the same single completed
attempt. It did not repeat remote deletion or local removal.

## Persistent capacity before and after

The pre-publish gate had intentionally activated the five persistent runners
mapped to the repository. Capacity was captured immediately before ephemeral
creation and immediately after cleanup:

| Evidence | Before | After |
| --- | ---: | ---: |
| `available_now` | 5 | 5 |
| `busy_capacity` | 0 | 0 |
| `provisioned_idle` | 0 | 0 |
| active local runner count | 5 | 5 |
| queued jobs | 0 | 0 |
| collector errors | 0 | 0 |

Both snapshots also contained one `inconclusive` remote-only registration without
matching local evidence. This pre-existing condition made the aggregate snapshot
status `inconclusive`; it did not change between the two observations and was not
treated as healthy capacity.

After qualification, the four runners activated only for the publication gate
were returned to healthy on-demand idle state. Final repository capacity was one
available runner, four provisioned-idle runners, zero busy runners, and the same
one unrelated inconclusive remote-only registration.

## Validation evidence

- local ephemeral contracts: 32 Python tests and one shell integration contract passed;
- affected capacity/autoscale regressions: 116 tests passed;
- shell syntax, Python compilation, workflow YAML and `git diff --check` passed;
- public `runnerctl ci watch` classified the qualified SHA as `success` with one
  completed workflow run;
- the published hosted validation job and exact ephemeral job both concluded
  `success`.

## What this proves

This run proves that the Issue #120 V1 primitive can:

1. allocate one deterministic identity and one owned disposable root;
2. fail closed before remote mutation when observation is inconclusive;
3. require reconciliation before retrying the same action;
4. register explicitly as ephemeral without persisting registration material;
5. observe the exact runner `ONLINE` and then `BUSY`;
6. consume one controlled repository-owned GitHub Actions job;
7. distinguish local exit, remote disappearance, terminal evidence and job conclusion;
8. converge through bounded, repeated cleanup to `CLEANED`;
9. leave persistent registrations, directories and capacity healthy.

## What this does not prove

This qualification does not validate:

- autoscale decisions for `CREATE_EPHEMERAL`;
- continuous pool reconciliation or desired pool size;
- multi-host scheduling, cloud burst, containers or virtual machines;
- long-duration reliability, high concurrency or performance SLOs;
- success/failure ingestion for arbitrary jobs into the lifecycle evidence schema.

Those remain separate boundaries and must not be inferred from this one controlled
one-job qualification.
