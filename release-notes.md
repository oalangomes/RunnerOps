## RunnerOps v0.3.0 — Governed autoscaling + operator UX

RunnerOps v0.3.0 evolves the project from local self-hosted runner operations into an **observable, explainable and governed capacity control plane**.

The core flow introduced in this release is:

```text
queue/capacity observation
→ durable evidence
→ deterministic planning
→ governed START_LOCAL
→ structured verification
→ auditable outcome
```

### Capacity observability

RunnerOps can now inspect GitHub Actions queue pressure together with matching local runner capacity:

```bash
runnerctl capacity .
runnerctl autoscale status .
```

Capacity is explicitly classified as:

- `available_now`
- `busy_capacity`
- `provisioned_idle`
- `inconclusive`

Unknown or contradictory evidence remains inconclusive instead of triggering speculative action.

### Deterministic autoscale planning

```bash
runnerctl autoscale plan .
```

The new planner turns normalized capacity evidence and policy into deterministic decisions:

- `WAIT`
- `START_LOCAL`
- `PROVISION_LOCAL`
- `BURST_CLOUD`
- `HOLD`
- `BLOCKED`
- `INCONCLUSIVE`

Plans include stable reason codes, policy fingerprints and structured evidence.

Planning is completely read-only.

### Durable autoscale evidence

RunnerOps now uses a local SQLite audit/evidence store to retain bounded operational evidence for:

- queue observations;
- autoscale decisions;
- actions;
- action lifecycle transitions;
- replay and recovery.

Historical decisions can be inspected with:

```bash
runnerctl autoscale history
runnerctl autoscale explain --decision <id>
```

### Governed local autoscaling

v0.3.0 introduces the first mutating autoscale path:

```bash
RUNNER_AUTOSCALE_ENABLED=true runnerctl autoscale run-once .
```

Mutation is intentionally limited to `START_LOCAL`: activating one exact, already provisioned local runner selected by the deterministic planner.

Before mutation, RunnerOps:

1. persists the decision;
2. acquires a host-level lock;
3. collects fresh capacity evidence;
4. re-runs the planner;
5. revalidates policy, target and registration identity.

After the lifecycle operation, success is determined from fresh structured local + GitHub evidence rather than from the start command alone.

Every action remains reconstructable through the audit trail.

### Operator UX

This release also improves everyday CLI operation:

```bash
runnerctl add . --plan
runnerctl overview .
runnerctl help logs
runnerctl logs my-runner --since 30m --follow
runnerctl completion install bash
```

Highlights include:

- read-only provisioning previews;
- repository overview;
- hierarchical command help;
- actionable `[SUMMARY]` and `[NEXT]` feedback;
- bounded log controls;
- Bash, Zsh and Fish completion;
- TTY-only progress for mutable bootstrap operations;
- safer non-interactive systemd lifecycle authorization.

### Safety boundaries

v0.3.0 deliberately does **not** execute:

- `PROVISION_LOCAL`;
- `BURST_CLOUD`;
- continuous autonomous autoscaling.

Autoscaling remains opt-in, and the deterministic planner remains the scaling authority.

RunnerOps does not delegate scaling policy or target selection to an LLM.

### Validation

The release was validated through:

- hosted contract validation;
- GitHub API smoke tests;
- RunnerOps-managed self-hosted dogfood;
- controlled real-host `START_LOCAL` dogfood;
- release identity and GitHub Pages validation.

This release closes the first governed autoscaling boundary for **already provisioned local capacity**.

Agent-native operation, Agent Skills v2, managed skill distribution, bounded local provisioning and cloud burst remain future work.

**Full changelog:**  
https://github.com/oalangomes/RunnerOps/compare/v0.2.2...v0.3.0
