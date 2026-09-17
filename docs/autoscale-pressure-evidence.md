# Resumable autoscale pressure evidence

RunnerOps treats queue pressure as evidence, not as an estimate derived from GitHub job age.

This document describes the durable aggregate-pressure model introduced for #107. It complements the strict per-job queue episodes already stored by the autoscale audit store.

## Problem

The original evidence model tracked queue continuity by exact GitHub Actions job identity:

```text
repository + run_id + run_attempt + job_id
```

That is the correct boundary for an individual job. It is deliberately strict.

But autoscaling pressure is not always tied to one job identity. A repository can have sustained pressure for the same runner capability while jobs complete, are replaced, or temporarily cannot be observed because a GitHub API collection is inconclusive.

A real sequence can therefore look like:

```text
955s of proven pressure
→ one inconclusive observation
→ same capability scope is visible again
```

Resetting the aggregate qualification to zero loses 955 seconds that RunnerOps already proved. Counting the unknown interval as queued time is also wrong.

The required result is:

```text
955s proven
+ 64s unknown
+ same scope resumes
= 955s proven
```

Unknown is neither `0` nor `64 seconds of queue`. It is unknown.

## Two evidence layers

RunnerOps keeps two separate contracts.

### Exact job episodes

`queue_observations` remains strict and auditable.

An incomplete observation closes an open exact episode with:

```text
end_reason = inconclusive_observation
```

If the job appears again later, it starts a new exact episode. #107 does not weaken this behavior.

### Aggregate capability pressure

Schema v2 adds a second evidence layer keyed by:

```text
repository + exact normalized required-label scope
```

Examples of distinct scopes:

```text
[self-hosted, linux, cpu]
[self-hosted, linux, gpu]
[self-hosted, linux, x64, local-runner]
```

Scopes are case-insensitive and isolated. Pressure proved for one scope cannot qualify another.

## Segmented proof

An aggregate qualification is not represented as one wall-clock interval. It is represented as a durable sequence of **observed segments**.

```text
segment A: observed
unknown interval: not a segment
segment B: observed
```

The proved queue duration is:

```text
proven_queued_seconds = Σ observed_segment_seconds
```

The unknown interval is structurally absent from the sum.

This makes time inflation difficult to introduce accidentally later: callers do not subtract `first_seen` from the current clock to reconstruct aggregate pressure.

Example:

```text
segment A = 955s
unknown   = 64s
segment B = 60s

proven_queued_seconds = 955 + 60 = 1015s
```

It is never `1079s`.

## State machine

A qualification has one of three states:

```text
active
suspended
ended
```

### Complete observation, scope present

A new scope starts `active` with a zero-duration segment.

Consecutive complete observations within the configured queue-gap bound advance the current segment.

### Inconclusive observation

An `active` qualification becomes `suspended`.

No segment is advanced and no seconds are added.

Exact job episodes still close independently.

### Complete observation after suspension

If the same exact normalized capability scope is present again and the gap from the last proved observation is within `RUNNER_AUTOSCALE_QUEUE_GAP_SECONDS`, the qualification resumes:

```text
suspended → active
```

RunnerOps creates a new zero-duration observed segment. Previous proved seconds are retained.

The qualification records:

- `resume_count`;
- `last_resume_at`;
- `last_unknown_seconds`.

These fields explain the recovery but do not turn the unknown interval into queue duration.

### Confirmed disappearance

A complete observation where the scope is absent ends the qualification with:

```text
scope_left_queue
```

A later reappearance starts a new qualification at zero.

### Observation gap exceeded

If the gap exceeds the safe bound, the previous qualification ends with:

```text
observation_gap
```

A present scope starts a new qualification at zero.

### Evidence or label change

An exact episode ending with `evidence_changed` resets the affected old capability scope.

A changed scope starts separately at zero. Unaffected scopes continue independently.

## Durable schema

Audit schema v2 introduces:

```text
pressure_qualifications
pressure_segments
```

`pressure_qualifications` stores the current lifecycle and scope identity. `pressure_segments` stores the observed time intervals that form the proof.

The migration from schema v1 to v2 is explicit and transactional. Existing exact queue observations, decisions, actions, and action events are preserved.

A writable `AuditStore` migrates v1 → v2 before accepting new evidence. Read-only commands never perform schema migration as a side effect.

## Planner contract

The production planner consumes the durable aggregate qualification instead of rebuilding aggregate proof by stitching closed exact-job episodes.

A qualified resumed scope can therefore produce the normal deterministic decision flow:

```text
proven aggregate pressure >= threshold
+ matching current pressure work
+ local capacity deficit
+ matching provisioned-idle runner
→ START_LOCAL
```

When resumed evidence contributes to a qualified scaling decision, the plan includes:

```text
PRESSURE_QUALIFICATION_RESUMED
```

The plan evidence also exposes the qualification ID, resume metadata, segments, and proved seconds.

## Relationship with read-only temporal projection

#108 and #107 solve different problems.

### #107

Handles durable scope-level evidence **between persisted observations**.

A complete post-unknown observation can resume a scope even if the exact current job identity differs from the job seen before the unknown interval.

### #108

Handles the small timing difference between the latest persisted evidence and a newer fresh `runnerctl autoscale plan` snapshot.

That read-only projection still requires an exact current job anchor and a bounded lag. The unpersisted T0→T1 interval is never added to proved pressure.

So the two rules coexist:

```text
persisted observations:
  scope-level resume is allowed under the #107 contract

fresh read-only projection after the latest persisted observation:
  exact current identity anchor is still required by #108
```

## Safety properties

The model intentionally preserves these properties:

- GitHub `job.created_at` is provenance, never the autoscale threshold clock;
- unknown time never increases `proven_queued_seconds`;
- exact per-job evidence remains strict;
- confirmed queue disappearance resets pressure;
- excessive observation gaps reset pressure;
- label/capability scopes cannot borrow qualification from each other;
- state survives process restart because it lives in SQLite;
- planner output remains deterministic from normalized policy and evidence;
- no LLM decides whether evidence continuity is valid.

## Validation

The contracts in:

```text
tests/test-autoscale-pressure-contracts.py
tests/test-autoscale-pressure-planner-contracts.py
```

cover:

- `955s proven → unknown → resume` without time inflation;
- exact job reset while aggregate scope resumes;
- positive disappearance reset;
- observation-gap reset;
- label-scope isolation;
- process restart during suspension;
- schema v1 → v2 migration;
- resumed durable pressure driving a real `START_LOCAL` planner decision.

The important invariant is simple:

> RunnerOps may retain time it has proved. It never manufactures time it did not observe.
