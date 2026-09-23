# RunnerOps Issue #73 / PR #113 — Real-host Qualification

## 1. Executive Summary

**Overall result: INCONCLUSIVE for the complete acceptance sequence; core provisioning and activation evidence PASS.**

The durable audit proves the real-host path `PROVISION_LOCAL -> provisioned_idle -> START_LOCAL -> VERIFIED_ONLINE` for the exact target `runnerops-gate-113-01`, registration `27`, with no duplicate registration. The provisioning pool grew from 4 to 5 under `max_local_runners=5`, and the target was deterministic.

This execution did not repeat either lifecycle mutation because registration 27 was already online when the pre-flight snapshot was taken. The current scoped workload was no longer visible to the local capacity collector, while an unrelated `runnerops-provision-probe` job remained queued. The GitHub API query for the target run timed out, so the exact runner identity that consumed the job cannot be independently confirmed here. PR #113 remains open and draft; it was not marked ready and was not merged.

No product code was changed during this qualification.

## 2. Environment

- Date/time: `2026-09-22T23:26:05-03:00` (host clock); audit timestamps are UTC.
- Hostname: `AlanGomes-PC`
- OS: Linux WSL2, kernel `6.6.87.2-microsoft-standard-WSL2`
- Branch: `feat/issue-73-provision-local`
- HEAD: `bf62ba8cf8b080fae532f972e5f5315ced201fd5`
- runnerctl: `0.3.0`
- Repository: `oalangomes/RunnerOps`
- PR: `#113`, open, draft

## 3. Safety Controls

- Scheduler before and after: `enabled=false`, `active=false`, `next_run=null`.
- Cloud burst: disabled by policy and not used.
- No `runnerctl ensure`, batch provisioning, removal, registry cleanup, registration deletion, scheduler autoscale, lifecycle loop, or blind retry was executed.
- Existing runners were preserved. AgentsOrchNext runners were not changed.
- Controlled policy fingerprint: `sha256:05a170a7b02a612e9f81810529d7b14c8f71891ceb71d5d0509c85aad099812b`.
- Explicit policy included provisioning enabled, `max_local_runners=5`, `max_active_local_runners=10`, queue threshold `0`, cooldown `0`, generic profile, group `runnerops`, exact labels, prefix `runnerops-gate-113`, and burst disabled.
- All explicit planner calls removed `RUNNEROPS_AUTOSCALE_POLICY_FILE`.

## 4. Initial State

The first capacity snapshot in this session showed registration 27 already online. This differs from the previously recorded post-provisioning idle state and is why no second `START_LOCAL` was attempted.

| Runner | Registration ID | Local State | GitHub State | Busy | Category | Relevant Labels |
|---|---:|---|---|---|---|---|
| `runnerops-1` | 23 | `healthy_idle`, boot disabled | offline | false | provisioned_idle | `runnerops-1`, python |
| `runnerops-autoscale-probe` | 24 | `healthy_idle`, boot disabled | offline | false | provisioned_idle | `runnerops-autoscale-probe` |
| `agentsorchnext-6` | 25 | `healthy_idle`, boot disabled | offline | false | provisioned_idle | `agentsorchnext-6` |
| `runnerops-provision-01` | 26 | `healthy_idle`, boot disabled | offline | false | provisioned_idle | `runnerops-provision-probe` |
| `runnerops-gate-113-01` | 27 | active, boot disabled | online | false | available_now | `runnerops-provision-gate-113`, `runnerops-gate-113-01` |

At the final snapshot: `available_now=1`, `busy_capacity=0`, `provisioned_idle=4`, `inconclusive=0`, host active local count `10`. The remaining queued job was unrelated: run `35799447193`, job `106986278366`, scope `runnerops-provision-probe`.

## 5. Gate Results

| Gate | Result | Evidence | IDs / Artifacts | Notes |
|---:|---|---|---|---|
| 0 | PASS | Correct branch, SHA, clean initial worktree, scheduler disabled/inactive | `bf62ba8...`; status snapshot | No scheduler mutation required. |
| 1 | INCONCLUSIVE | Capacity complete, but target was already online and queue was unrelated | registration `27`; run `35799447193` | Expected idle/offline precondition was not present. |
| 2 | PASS | Durable historical plan selected exact `PROVISION_LOCAL` target with required reasons | `plan-0412740...` | Historical gate, not recreated. |
| 3 | PASS | Pool evidence `4 -> 5`, one registration, no suffix fallback | registration `27` | No `runnerops-gate-113-02` observed. |
| 4 | PASS | `planned -> started -> succeeded`, `PROVISION_VERIFIED_IDLE`, external ID `27` | action `action-2b2b847...` | Same action, decision, and target. |
| 5 | PASS | Durable recovery record shows inconclusive crossing followed by reconciliation without blind add | registration `27`; action `action-2b2b847...` | No second registration. |
| 6 | NOT_REEXECUTED — verified from durable evidence | Provisioned idle state and exact matching labels recorded after provisioning | registration `27` | Current state is later `online`, not idle. |
| 7 | PASS | START_LOCAL selected after the target existed and was idle | `plan-f50a296...` | Same exact target and policy fingerprint. |
| 8 | NOT_REEXECUTED — verified from durable evidence | Governed START_LOCAL already completed before this session | `action-732c505...` | No second mutation was safe. |
| 9 | PASS | Current capacity confirms exact registration 27 online | `runnerops-gate-113-01` | No other runner accepted as evidence. |
| 10 | PASS | `planned -> started -> succeeded`, diagnostic `VERIFIED_ONLINE` | `plan-f50a296...`, `action-732c505...` | External ID `27`, exit code `0`. |
| 11 | INCONCLUSIVE | Durable queue evidence says target job left queue; GitHub API runner identity query timed out | run `35806685914`, job `107009183775` | Exact consuming runner could not be independently confirmed. |
| 12 | PASS | Exactly one target registration remains; no suffix duplicate | registration `27` | No duplicate or fallback target observed. |
| 13 | PASS | Pool reached `5` with maximum `5`; planner evidence records the bound | `max_local_runners=5` | No destructive over-limit mutation attempted. |
| 14 | NOT_REEXECUTED — verified from durable evidence | Active host count was recorded separately from matching capacity; no extra activation was attempted at host count `10` | host active `10`; matching active `0` | Limit separation covered by contracts. |
| 15 | PASS | Required-label matching and cardinality contracts passed | labels on registration `27`; tests | Matching scope is exact. |
| 16 | PASS | Stale queue evidence and safe no-op refresh are durable/tested behavior | planner/runtime tests | Current planner returned no mutation for absent scoped work. |
| 17 | PASS | Opt-in policy, separate limits, fingerprint persistence, and scheduler policy mode covered | fingerprint above; scheduler tests | Managed policy pointer was removed for controlled calls. |
| 18 | PASS | Provisioning ended idle with boot disabled; START_LOCAL was a separate action | actions `PROVISION_LOCAL`, `START_LOCAL` | No automatic provisioning start. |
| 19 | PASS | No removal, registry cleanup, cloud burst, ensure, or unrelated runner mutation | final capacity/status | Host safety preserved. |
| 20 | PASS | Focused contract suite passed | 133 tests, 11 files | Details in section 12. |
| 21 | INCONCLUSIVE | Hosted validation success; two checks neutral; direct run lookup timed out | run `35806376337` | Not green as a complete check set. |
| 22 | PASS | Qualification label/workflow change is visible in PR metadata | `runnerops-provision-gate-113` | Test-only temporary change; recommend revert before final PR. |
| 23 | PASS | Current scoped planner returned no mutation (`BLOCKED/LABEL_SCOPE_BLOCKED`) | `plan-0c3134...` | No scoped queued work remained. |
| 24 | PASS | Final scheduler state remained disabled/inactive | final status snapshot | `next_run=null`. |
| 25 | PASS | Final Git, scheduler, and capacity snapshots captured | HEAD above; final snapshots | Generated `__pycache__` was removed after tests. |

## 6. PROVISION_LOCAL Evidence

- Decision: `plan-0412740b3c0d5bdc0dfa459eb8c047fa`
- Action: `action-2b2b847efbc367185ef6deb3b5e312dd`
- Target: `runnerops-gate-113-01`
- External ID: `27`
- Policy fingerprint: `sha256:05a170a7b02a612e9f81810529d7b14c8f71891ceb71d5d0509c85aad099812b`
- Reasons: `LOCAL_CAPACITY_DEFICIT`, `LOCAL_POOL_BELOW_MAX`, `OBSERVED_QUEUE_THRESHOLD_MET`, `SUSTAINED_QUEUE_PRESSURE`
- Planned: `2026-09-23T02:03:05.018976Z`
- Started: `2026-09-23T02:03:05.042260Z`
- Succeeded: `2026-09-23T02:09:09.221567Z`
- Terminal diagnostic: `PROVISION_VERIFIED_IDLE`, exit code `0`

The first real iteration crossed the mutation boundary and became `started` with `PROVISION_VERIFICATION_INCONCLUSIVE`; registration 27 then appeared. The later iteration reconciled the same target and action to `succeeded`, with no second `runnerctl add` and no second registration. This recovery evidence was treated as durable historical evidence and was not synthetically reproduced.

## 7. START_LOCAL Evidence

- Decision: `plan-f50a296ad48c9ed5116c240896e8b59b`
- Action: `action-732c505e08dbbbbfa3716781b6e5f92a`
- Target/external ID: `runnerops-gate-113-01` / `27`
- Policy fingerprint: same `sha256:05a170...9812b` fingerprint
- Planned: `2026-09-23T02:11:53.438374Z`
- Started: `2026-09-23T02:11:53.456541Z`
- Succeeded: `2026-09-23T02:12:00.785391Z`
- Terminal diagnostic: `VERIFIED_ONLINE`, exit code `0`

This action was already complete when this session began. The current capacity snapshot independently confirms registration 27 online, so no duplicate start was issued.

## 8. Workload Evidence

Target workload: run `35806685914`, job `107009183775`, required labels `Linux`, `X64`, `local-runner`, `runnerops`, `runnerops-provision-gate-113`, `self-hosted`.

Durable queue observations show five observations and `175` seconds of observed queue time; the final observation ended with `left_queue` at `2026-09-23T02:15:04.227206Z`. This establishes that the job left the queue after the START_LOCAL action. A direct GitHub API lookup timed out, so the exact runner name/registration used by the job is not independently available in this run. The workload gate therefore remains INCONCLUSIVE rather than being promoted to PASS.

## 9. Idempotency / Duplicate Protection

The target exists exactly once with registration ID `27`. Final capacity contains no `runnerops-gate-113-02`, no `runnerops-gate-113-01-2`, and no equivalent suffix fallback. The durable provisioning recovery also records one action and one external registration. Existing registrations 23–26 were preserved.

## 10. Bounded Pool

The provisioning evidence records `current_local_pool_size=4`, `max_local_pool_size=5`, `requested_capacity_delta=1`, and target slot `runnerops-gate-113-01`. The final local pool has five runners, including registration 27. The planner and provisioning contract suites cover the no-growth-at-limit behavior; no artificial workload or over-limit mutation was used.

## 11. Audit Timeline

All times below are UTC.

| Timestamp | Event |
|---|---|
| `2026-09-23T02:02:54.263736Z` | `PROVISION_LOCAL` plan recorded with exact target and policy fingerprint. |
| `2026-09-23T02:03:05.018976Z` | Provision action planned. |
| `2026-09-23T02:03:05.042260Z` | Provision action started; mutation boundary crossed. |
| `2026-09-23T02:09:09.221567Z` | Provision reconciled to succeeded, external ID 27, `PROVISION_VERIFIED_IDLE`. |
| `2026-09-23T02:11:45.225433Z` | START_LOCAL plan recorded for the same target. |
| `2026-09-23T02:11:53.438374Z` | START_LOCAL action planned. |
| `2026-09-23T02:11:53.456541Z` | START_LOCAL action started. |
| `2026-09-23T02:12:00.785391Z` | START_LOCAL succeeded, external ID 27, `VERIFIED_ONLINE`. |
| `2026-09-23T02:15:04.227206Z` | Target workload queue observation ended with `left_queue`. |
| `2026-09-23T02:23:01.592748Z` | Read-only planner returned `BLOCKED/LABEL_SCOPE_BLOCKED`; no scoped queue. |
| `2026-09-23T02:26:35.515961Z` | Final capacity snapshot: registration 27 online; scheduler disabled/inactive. |

## 12. Test Results

All commands were run with `python3 -B`; total reported test count was 133 and all returned `RC=0`.

| Command | Result | Duration |
|---|---|---:|
| `tests/test-autoscale-planner-contracts.py` | 31 passed | 0.018s |
| `tests/test-autoscale-provision-contracts.py` | 13 passed | 0.452s |
| `tests/test-autoscale-provision-controller-contracts.py` | 6 passed | 0.007s |
| `tests/test-autoscale-provision-planner-contracts.py` | 9 passed | 0.004s |
| `tests/test-autoscale-provision-run-once-contracts.py` | 4 passed | 0.879s |
| `tests/test-autoscale-exact-provisioning-contracts.py` | 3 passed | 0.183s |
| `tests/test-autoscale-controller-contracts.py` | 23 passed | 3.341s |
| `tests/test-autoscale-scheduler-contracts.py` | 9 passed | 1.549s |
| `tests/test-autoscale-readonly-plan-timing.py` | 8 passed | 0.003s |
| `tests/test-capacity-contracts.py` | 26 passed | 24.090s |
| `tests/test-capacity-bom-contract.py` | 1 passed | 0.002s |

No general full-suite run was attempted; no test failures were masked.

## 13. CI Results

PR status checks for run `35806376337`:

- `Hosted validation`: success, job `107008047641`.
- `Hosted GitHub API smoke`: neutral, job `107008163635`.
- `RunnerOps self-hosted dogfood`: neutral, job `107008163263`.

The active PR is open and draft. A direct GitHub API request for run `35806685914` timed out, which is an infrastructure/access limitation in this execution, not evidence of a product failure. Overall CI is therefore `no` for a fully green set.

## 14. Findings

### Blockers

- Complete real-host qualification is not closed because the exact runner identity for the consumed target job could not be confirmed after the API timeout.
- The initial pre-flight state did not match the requested idle/offline precondition: registration 27 was already online, and the visible queued workload was for `runnerops-provision-probe`, not the target scope.

### Warnings

- Two PR checks are neutral rather than successful.
- The final host has an unrelated queued job for registration 26; it was not acted on.
- The workflow label `runnerops-provision-gate-113` is a temporary qualification change and should be reverted before finalizing the PR, according to the PR acceptance notes.

### Observations

- The implementation's durable action model preserved the mutation boundary and reconciled the same target without blind retry.
- Current host active count is 10 while matching active capacity for the target scope is separate; the snapshots preserve that distinction.
- The target remains online and must not be removed automatically; registration 27 is experiment evidence and was intentionally preserved.

## 15. Temporary Test Artifacts

- Temporary workflow label: `runnerops-provision-gate-113`.
- Qualification workflow change: self-hosted dogfood `runs-on` includes that label; test-only and temporary.
- Provisioned runner: `runnerops-gate-113-01`.
- GitHub registration: `27`.
- No automatic cleanup was performed.

## 16. Final Host State

Final scheduler:

```text
enabled = false
active = false
next_run = null
```

Final target:

```text
runnerops-gate-113-01
registration_id = 27
local.state = active
local.boot = disabled
github.status = online
github.busy = false
category = available_now
```

Final aggregate capacity: `available_now=1`, `busy_capacity=0`, `provisioned_idle=4`, `inconclusive=0`, host active local count `10`. No runner removal or registry cleanup was performed.

## 17. Conclusion

- PROVISION_LOCAL real-host qualified? **yes, from durable evidence**
- recovery qualified? **yes, from durable evidence**
- START_LOCAL-after-provision qualified? **yes, from durable evidence**
- exact target verification qualified? **yes**
- duplicate protection qualified? **yes**
- bounded pool qualified? **yes**
- audit qualified? **yes**
- CI green? **no**
- blockers remaining? **yes: exact workload-consumer identity and precondition mismatch require review**

The PR is technically strong on the implemented lifecycle evidence but should remain draft until the workload-consumer identity and neutral checks are resolved by an independent review. No merge or ready-for-review decision was made automatically.