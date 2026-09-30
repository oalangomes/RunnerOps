## RunnerOps v0.5.0 — Operational Evidence & Review

RunnerOps v0.5.0 adds a bounded operational evidence layer and a grounded, read-only AI review path without handing control-plane authority to a model.

The new analysis flow is:

```text
runtime + GitHub evidence
        ↓
OperationalEvidence v1
        ↓
canonical safe serialization + digest
        ↓
Ollama or LiteLLM
        ↓
strict grounded validation
        ↓
OperationalReview v1
```

### OperationalEvidence v1

`runnerctl report` turns the evidence RunnerOps already has into a bounded operational artifact:

```bash
runnerctl report . --since 24h
runnerctl report . --since 24h --json
```

The report keeps current capacity, available autoscale/audit history, collector metadata and known evidence gaps separate.

Historical information that RunnerOps does not persist is not fabricated. Missing capabilities, truncation and runtime collection problems remain explicit.

### Evidence-grounded AI review

`runnerctl review` analyzes OperationalEvidence without becoming part of runner lifecycle or autoscale control:

```bash
runnerctl review . --since 24h --provider ollama --model <model>
runnerctl review --evidence evidence.json --provider ollama --model <model>
runnerctl review . --since 24h --provider litellm --model <model> --allow-remote
```

The review contract includes:

- explicit provider and model selection;
- frozen-evidence replay;
- canonical evidence hashing;
- bounded findings and unknowns;
- exact RFC 6901 JSON Pointer evidence references;
- strict fail-closed validation;
- explicit remote acknowledgement before evidence leaves the local boundary.

RunnerOps owns the trusted metadata and rendering. Model output cannot invoke lifecycle, planner, controller, provisioning, shell/tools or audit-store writes.

### Fresh evidence over unsafe cache reuse

During the #105 collector work, RunnerOps experimented with persistent jobs-list reuse.

Adversarial review found the assumption unsafe: unchanged workflow-run metadata does not prove that the jobs collection is unchanged. A stale empty jobs response could therefore suppress newly queued work.

v0.5.0 keeps the safe performance improvements while restoring the stronger evidence contract:

- fresh jobs requests for every discovered active workflow run;
- process-local repository canonicalization cache only;
- bounded parallel jobs requests;
- one bounded retry;
- GitHub-call and wall-time instrumentation;
- scheduler headroom / overrun visibility.

The persistent jobs cache is gone.

### Repository architecture

The repository has also been reorganized around explicit domains:

```text
runnerctl / install.sh        public boundary
src/runnerops/                Python runtime
scripts/                      internal shell implementation
tests/                        subsystem contracts
docs/                         architecture / operations
skills/                       agent-facing operational contracts
```

The migration does not move user state or change runner registrations, autoscale semantics, CapacitySnapshot, OperationalEvidence or OperationalReview schemas.

The runtime now uses normal `runnerops.*` package modules, with `<platform>/src` preserved in `PYTHONPATH` even when machine configuration defines its own Python path.

### Real-host qualification

The new surfaces were exercised beyond fixtures:

- OperationalReview was dogfooded with configured local Ollama using both frozen and live OperationalEvidence;
- repository-layout qualification used the exact workflow checkout on a real self-hosted RunnerOps host;
- read-only dogfood exercised runner inventory, capacity, autoscale status/plan and operational report without mutating the persistent runner pool.

### Automated release cadence

Starting with this baseline, RunnerOps can publish a SemVer release for every merged pull request after master CI is green.

The default is PATCH. A PR may opt into MINOR or MAJOR through an explicit release label, and the same three bump modes are available through manual workflow dispatch.

The generated release commit is validated again before its tag and GitHub Release are published, so release identity is never tagged before the exact commit passes CI.

### Safety boundary

v0.5.0 still keeps deterministic RunnerOps logic as the scaling authority.

AI review is an analyst over evidence, not a controller.

The release does **not** add:

- LLM-driven scaling or target selection;
- cloud burst;
- scale-in;
- ephemeral runner lifecycle;
- containers/VM orchestration;
- persistent queue/job caching.

Ephemeral runners are the next lifecycle study, tracked separately in #120.

**Full changelog:**  
https://github.com/oalangomes/RunnerOps/compare/v0.4.0...v0.5.0
