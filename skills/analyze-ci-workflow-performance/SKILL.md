---
name: analyze-ci-workflow-performance
description: Analyze GitHub Actions workflow architecture and performance without changing workflow files by default. Use when the user asks about CI speed, queue time, critical path, parallelism, needs dependencies, matrices, caching, artifact reuse, runner capacity, duplicated workflows, or GitHub Actions optimization. Combine static workflow evidence with observed GitHub run/job history when available, and label recommendations as STATIC, OBSERVED, or ESTIMATED.
---

# Analyze CI Workflow Performance

Analyze first. Do not rewrite workflow YAML unless the user explicitly asks to apply a recommendation.

The goal is not to maximize parallelism or minimize one number at any cost. Optimize the workflow while preserving dependency correctness, reliability, debuggability, runner capacity, and maintainability.

## Evidence contract

Every meaningful finding must declare its evidence class:

- **STATIC** — supported directly by workflow structure or repository files.
- **OBSERVED** — supported by real GitHub Actions runs/jobs.
- **ESTIMATED** — a projected impact or hypothesis that still needs a benchmark.

Never present an estimated speedup as observed fact.

When evidence is weak, say so.

## Phase 1 — establish scope

Identify the workflows and the user's target:

- total PR feedback time;
- push/release duration;
- queue time;
- self-hosted runner utilization;
- one slow job;
- repeated setup/install;
- cache efficiency;
- architecture/DAG quality.

Inspect:

```bash
find .github/workflows -maxdepth 1 -type f \( -name '*.yml' -o -name '*.yaml' \) -print
```

Read the relevant workflow files before making recommendations.

Do not edit them during analysis.

## Phase 2 — static workflow model

For each relevant workflow, model:

- triggers: `push`, `pull_request`, `workflow_dispatch`, schedules;
- `paths` / `paths-ignore`;
- jobs and `needs:` edges;
- job-level `if:`;
- `runs-on` and required labels;
- matrix strategy and `fail-fast`;
- checkout/setup/install/build/test/package steps;
- cache actions or setup-action caches;
- artifact upload/download;
- repeated commands across jobs;
- environment/service dependencies;
- concurrency/cancellation policy.

Represent the current DAG explicitly.

Example:

```text
build ───────┐
lint ────────┼─> integration ─> package
unit-tests ──┘
```

Do not infer a dependency merely from job order in the YAML. GitHub Actions jobs without `needs:` can run independently.

## Static checks

Evaluate at least these questions when relevant:

### DAG and sequencing

- Are independent jobs accidentally serialized by `needs:`?
- Are required dependencies missing?
- Is one job doing unrelated work that could be separated without adding more overhead than value?
- Has the workflow been fragmented into tiny jobs whose startup/artifact overhead dominates?
- Does matrix execution make sense for the workload?
- Is `fail-fast` aligned with the desired feedback behavior?

### Repeated work

- Is dependency installation repeated across several jobs?
- Is the same build repeated instead of reused as an artifact?
- Are checkout/setup steps repeated because jobs genuinely need isolation?
- Are artifact transfers larger or more frequent than their benefit justifies?

### Trigger efficiency

- Do `push` and `pull_request` run equivalent work twice for the same development flow?
- Could `paths` or `paths-ignore` safely avoid irrelevant executions?
- Would concurrency cancellation reduce obsolete PR runs?

### Cache

- Is an ecosystem cache absent where dependency restoration is materially expensive?
- Is a configured cache keyed so narrowly that hits are unlikely?
- Is a cache so broad that correctness or reproducibility is at risk?
- Is there enough observed evidence to claim the cache helps?

### Runner capacity

- Does a simple job require an unnecessarily scarce runner label/capability?
- Are self-hosted jobs waiting for compatible capacity?
- Would changing labels improve routing without weakening isolation?
- Is a queue problem actually runner capacity rather than workflow structure?

Do not assume that a self-hosted runner is faster than a GitHub-hosted runner, or vice versa, without evidence.

## Phase 3 — observed history when GitHub access is available

If `gh` is installed and authenticated, collect read-only evidence from GitHub.

Resolve the repository:

```bash
gh repo view --json nameWithOwner --jq .nameWithOwner
```

Inspect recent workflow runs:

```bash
gh api "repos/<owner>/<repo>/actions/runs?per_page=30" \
  --jq '.workflow_runs[] | [.id, .name, .event, .status, (.conclusion // ""), .head_sha, .created_at, .run_started_at, .updated_at] | @tsv'
```

Inspect jobs for selected comparable runs:

```bash
gh api "repos/<owner>/<repo>/actions/runs/<run_id>/jobs?per_page=100" --paginate \
  --jq '.jobs[] | [.id, .name, .status, (.conclusion // ""), .created_at, .started_at, .completed_at, (.runner_name // ""), ((.labels // []) | join(","))] | @tsv'
```

These are read-only GET requests.

Prefer a bounded, comparable sample. Do not mix unrelated workflows, branches, events, or materially different workflow versions without saying so.

For performance baselines, prefer successful comparable runs. Analyze failures/cancellations separately because they can truncate duration.

## Metrics

When timestamps are available, calculate:

- **run duration** — completion/update minus start;
- **job queue time** — job start minus job creation;
- **job execution time** — completion minus start;
- median duration for comparable runs/jobs;
- p95 only when the sample is large enough to be meaningful;
- frequency of queueing or failures;
- approximate critical path.

Report the sample size.

Example:

```text
OBSERVED
sample: 24 successful pull_request runs
median total: 8m12s
median unit-tests: 3m01s
median lint: 42s
median queue: 6s
```

Do not report fake precision when the sample is small.

## Critical path

Use both the static DAG and observed timing.

A job is not on the critical path merely because it is individually slow. The critical path is the longest dependency-constrained path that determines workflow completion.

If timing is inferred from medians across different runs, label the result **ESTIMATED**.

If a specific run's timestamps and dependency graph support the path directly, it may be **OBSERVED** for that run.

## CI feedback bridge

`runnerctl ci watch` is useful for correlating and waiting on the current SHA/PR:

```bash
runnerctl ci watch . --json
runnerctl ci watch . --pr <number> --json
```

Use it for conclusive current-run state, workflow/job/step context, rerun `run_attempt`, and runner/infra diagnosis.

Do not use `runnerctl ci watch` as a substitute for historical duration or queue-time analysis. Historical metrics come from GitHub runs/jobs.

A CI failure is not automatically a runner failure.

## Prioritize findings

Prefer a small number of high-value findings over a long checklist.

For each finding provide:

1. priority/confidence;
2. evidence class;
3. current behavior;
4. evidence;
5. recommendation;
6. expected impact;
7. risk/trade-off;
8. how to validate.

Example:

```text
[HIGH / OBSERVED]

Finding: lint is serialized behind build without a data dependency.
Evidence: needs: [build] in ci.yml; 18 comparable PR runs.
Observed median: build 2m14s, lint 41s.
Recommendation: remove the unnecessary needs edge.
Expected impact: up to ~41s from the dependency-constrained path.
Validation: compare median PR completion time before/after over comparable runs.
```

If the gain is not directly observed:

```text
[MEDIUM / ESTIMATED]

Finding: integration-tests may benefit from a two-shard matrix.
Evidence: one job dominates observed execution time.
Expected impact: unknown until benchmarked.
Risk: duplicated setup and higher runner consumption may erase the gain.
Validation: benchmark current vs two-shard treatment on the same test corpus.
```

## Output

A useful report should contain:

```text
CI performance analysis

Scope:
- workflows:
- event/branch:
- sample:
- evidence quality:

Current DAG:
...

Observed baseline:
...

Critical path:
...

Prioritized findings:
1. ...
2. ...
3. ...

No-change decisions:
- ...

Benchmark plan:
- baseline:
- treatment:
- metric:
- sample:
- rollback:
```

Include **No-change decisions** when an apparently obvious optimization is not justified. Avoid manufacturing work merely to produce findings.

## Applying a recommendation

Only edit workflow files after the user explicitly asks to apply a recommendation.

Before editing:

1. restate the exact change;
2. identify the expected metric;
3. preserve unrelated workflow behavior;
4. make the smallest reasonable diff.

After editing:

1. validate workflow structure/syntax with available repository tooling;
2. inspect the diff;
3. run the relevant repository tests if applicable;
4. publish only when authorized;
5. compare before/after GitHub Actions evidence when enough runs exist;
6. revert the optimization if the evidence does not support it.

## Prohibitions

- Do not rewrite workflow YAML by default.
- Do not invent performance gains.
- Do not recommend parallelism without checking real dependencies.
- Do not treat every long job as something that should be split.
- Do not recommend caching without considering correctness and observed cost.
- Do not equate CI failure with runner failure.
- Do not start, stop, restart, remove, or reconfigure runners as part of performance analysis.
- Do not add new `runnerctl` runtime commands for this skill.
- Do not couple the skill to Codex, Claude, Copilot, or another specific agent.
