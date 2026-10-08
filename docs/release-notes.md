## RunnerOps v0.6.0 — Governed Ephemeral Capacity & Agent-Native Operations

RunnerOps v0.6.0 turns the local ephemeral runner experiment from v0.5.0's next study into a governed capacity path, while also tightening the agent-facing operating model around the deterministic RunnerOps control plane.

The new local ephemeral autoscale path is:

```text
queue + capacity evidence
        ↓
deterministic planner
        ↓
CREATE_EPHEMERAL
        ↓
persist exact autoscale action
        ↓
exact ephemeral lifecycle
        ↓
REGISTERED → ONLINE → BUSY
        ↓
TERMINAL → CLEANUP_PENDING → CLEANED
        ↓
durable evidence
```

### One-job local ephemeral lifecycle

RunnerOps now exposes an explicit one-job lifecycle primitive:

```bash
runnerctl ephemeral create .
runnerctl ephemeral status <action-id>
runnerctl ephemeral reconcile <action-id>
runnerctl ephemeral cleanup <action-id>
```

The lifecycle keeps desired, local and GitHub-observed state separate and preserves uncertainty instead of turning missing evidence into success.

Key contracts include:

- deterministic action-to-runner identity;
- one disposable root per lifecycle;
- no silent name auto-increment after collisions;
- no blind second registration after an uncertain mutation;
- explicit inconclusive registration/online/terminal states;
- local process exit alone does not prove workload completion;
- remote deregistration alone does not prove local cleanup;
- cleanup requires terminal evidence, stays bounded and is idempotent;
- persistent runner registrations and state remain outside the ephemeral cleanup boundary;
- registration material is never persisted.

### Governed `CREATE_EPHEMERAL`

The deterministic autoscale planner/controller can now choose `CREATE_EPHEMERAL` when reusable persistent capacity cannot satisfy qualified demand and local ephemeral policy explicitly permits it.

The path is opt-in and bounded by:

- repository/capability label scope;
- finite active ephemeral limits;
- scale-out cooldown;
- configured profile/labels;
- existing host and autoscale safety evidence.

RunnerOps persists the exact autoscale action → ephemeral lifecycle link and reconciles the same lifecycle through completion rather than creating another runner when state is uncertain.

Persistent capacity remains preferred when it can safely satisfy demand.

### Real-host qualification

The final implementation was exercised through two consecutive controlled real-host cycles on Linux/systemd.

For both cycles RunnerOps observed:

```text
CREATE_EPHEMERAL
→ exact registration
→ ONLINE
→ real GitHub Actions workload
→ BUSY
→ TERMINAL
→ CLEANUP_PENDING
→ CLEANED
```

Both jobs succeeded.

Each lifecycle recorded one registration attempt, workload observation, proven terminality and successful cleanup. Both autoscale actions ended with `EPHEMERAL_CLEANED`.

After the first lifecycle was fully cleaned, a distinct second ephemeral runner was created and completed the same path.

No duplicate ephemeral runners/actions were produced, and the same five persistent runners remained healthy and on-demand idle after qualification.

### Capacity-first Agent Skills

RunnerOps Agent Skills now follow the current control-plane semantics instead of proactively waking broad repository capacity.

The default operating pattern is:

```text
observe capacity
→ inspect governed autoscale
→ use one exact runner when evidence justifies it
→ keep ensure . as an explicit broad override
```

Skills do not translate queue pressure into agent-selected `ephemeral create`, `start all` or other ad-hoc scaling actions.

A repository-level synchronization rule now requires functional RunnerOps changes to review and update affected Agent Skills and contracts in the same delivery. GitHub Copilot repository instructions carry the same boundary so agent-facing behavior cannot silently drift behind the product.

### RunnerOps Operator

v0.6.0 also introduces the canonical global `runnerops-operator` persona.

Its role is orchestration:

```text
user intent
    ↓
runnerops-operator
    ↓
RunnerOps Agent Skills
    ↓
runnerctl
    ↓
deterministic RunnerOps contracts
```

The operator can compose host and multi-repository workflows while keeping the RunnerOps runtime repository-scoped.

This deliberately does not introduce a second fleet control plane, agent-owned scaling policy or LLM-selected runtime mutations.

### Deterministic authority remains the boundary

v0.5.0 introduced evidence-grounded AI review with Ollama/LiteLLM.

v0.6.0 keeps the same architectural separation:

```text
LLM / agent
    = analysis + orchestration

RunnerOps evidence + policy + planner/controller
    = runtime authority
```

Generative models may explain evidence and compose safe public interfaces, but scaling decisions and lifecycle postconditions remain deterministic and evidence-backed.

### Safety boundary

v0.6.0 does **not** add:

- LLM-defined scaling policy or target selection;
- cloud burst execution;
- generic scale-in;
- multi-host scheduling;
- container or VM orchestration;
- automatic fleet-wide policy;
- registration-token persistence;
- persistent queue/job caching.

Cloud burst and broader fleet experiments remain separate work.

**Full changelog:**  
https://github.com/oalangomes/RunnerOps/compare/v0.5.0...v0.6.0
