## RunnerOps v0.4.0 — Governed local provisioning

RunnerOps v0.4.0 extends the governed autoscaling boundary from activating existing capacity to **bounded local runner provisioning**.

The local scale-out path is now:

```text
queue/capacity observation
→ durable pressure evidence
→ deterministic planning
→ governed PROVISION_LOCAL
→ verified provisioned_idle
→ later START_LOCAL
→ VERIFIED_ONLINE
→ auditable outcome
```

### Bounded local pool growth

`runnerctl autoscale run-once` can now execute `PROVISION_LOCAL` when local provisioning is explicitly enabled and policy allows pool growth.

Provisioning is:

- opt-in and disabled by default;
- limited separately from active-runner capacity;
- one registration at most per controller iteration;
- deterministic, using the lowest free `<prefix>-NN` slot;
- constrained to an explicit profile, group and label template.

Existing compatible `provisioned_idle` capacity still takes precedence: the planner selects `START_LOCAL` before provisioning another runner.

### Replay-safe provisioning

Provisioning now has a durable action lifecycle:

```text
planned
→ started
→ succeeded | failed | inconclusive
```

Once an action crosses the provisioning boundary, RunnerOps never blindly calls `runnerctl add` again for that action. A later iteration reconciles the exact persisted target against fresh local + GitHub evidence.

This prevents an inconclusive verification window from becoming duplicate infrastructure.

### Exact target safety

The governed autoscaler does not use the human-friendly auto-suffix behavior of interactive `runnerctl add`.

For autoscale provisioning:

- the target identity is immutable;
- collisions are rejected rather than silently becoming `target-2`;
- success requires the exact target to be observed as a healthy local `provisioned_idle` registration;
- the GitHub registration identity is persisted in the audit trail.

### Independent limits

v0.4.0 separates:

- `RUNNER_AUTOSCALE_MAX_ACTIVE_LOCAL_RUNNERS` — active local capacity;
- `RUNNER_AUTOSCALE_MAX_LOCAL_RUNNERS` — total registered local pool size.

This allows RunnerOps to keep a bounded idle pool without conflating registrations with active processes.

### Real-host qualification

The v0.4.0 path was qualified on a real Linux/WSL2 + systemd host with a real GitHub Actions workload.

Observed end-to-end evidence included:

- real sustained queued pressure with no matching capacity;
- deterministic `PROVISION_LOCAL` selection;
- pool growth from 4 to the configured maximum of 5;
- one exact new registration;
- an inconclusive post-provision verification followed by recovery of the same action without a second add;
- `PROVISION_VERIFIED_IDLE`;
- a later planner decision selecting `START_LOCAL` for that same runner;
- `VERIFIED_ONLINE`;
- the queued GitHub Actions job executing successfully on the newly provisioned runner;
- no duplicate registration;
- final scheduler state disabled/inactive for the controlled qualification.

The focused qualification suite passed 133 tests, followed by green repository CI and public-portability validation.

### Safety boundaries

v0.4.0 still does **not** execute:

- `BURST_CLOUD`;
- scale-in;
- batch provisioning;
- `ensure .` as an autoscale fallback;
- LLM-based scaling policy or target selection.

The deterministic planner remains the scaling authority, and local provisioning remains explicit policy.

This release closes the governed local scale-out loop: RunnerOps can now both **activate existing local capacity** and **grow the bounded local runner pool**.

**Full changelog:**  
https://github.com/oalangomes/RunnerOps/compare/v0.3.0...v0.4.0
