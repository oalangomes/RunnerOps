# Issue #120 adversarial lifecycle hardening report

This report records the contract-level adversarial review and hardening performed
after the successful real-host qualification of the one-job ephemeral runner.
It covers lifecycle safety, evidence semantics, filesystem invariants, and the
repository validation suite. It does not claim a new real-host qualification.
The corresponding sanitized machine-readable record is
[`docs/evidence/issue-120-adversarial-hardening.json`](evidence/issue-120-adversarial-hardening.json).

## Result

**PASS — adversarial findings addressed and contract suite green**

```text
BLOCKERS: 0
HIGH: 0
MEDIUM: 0
LOW: 0
```

The exact tested implementation revision is
`970a2669796598e7e1059ad491fdb838f5f34ab4` on
`feat/issue-120-ephemeral-lifecycle`. This follow-up round started at reviewed
revision `3a2fe5f8c5bc444d8f741ed92e84ee11c5136436`.

## Findings closed

| Finding | Resulting invariant | Contract evidence |
| --- | --- | --- |
| Cleanup before terminality | Destructive cleanup requires `terminal_evidence.proven is True`; `BUSY`, `UNKNOWN`, and `AMBIGUOUS` remain fail-closed | ONLINE-idle and OFFLINE refuse with zero DELETE, stop, or root removal; TERMINAL succeeds; CLEANED repeats idempotently |
| Stale terminal cleanup authorization | Durable terminal proof must be revalidated by fresh remote `ABSENT` plus local `EXITED`, `ABSENT`, `ALLOCATED`, or idempotent `CLEANED` evidence | Proven terminal plus fresh ONLINE/OFFLINE or RUNNING/STARTING records an explicit contradiction and performs zero DELETE, stop, or root removal; interrupted CLEANUP_PENDING still converges |
| Unbounded special float values | Every ephemeral CLI, environment, lock, and runtime duration must satisfy `math.isfinite(value) and value > 0` | Both public duration flags and all three environment settings reject NaN, infinities, zero, and negative values; shell machine config follows the same finite-positive rule |
| Name-only remote ownership | Local `.runner` `agentName` and positive `agentId` must correlate with the exact remote name and positive runner id | Correct identity is accepted; mismatched/missing ids and `ephemeral=false` become inconclusive and never DELETE |
| Start recovery hole | A configured, correlated, non-busy, non-terminal action may restart its exact unit regardless of its previous inconclusive state | Start failure recovers through reconcile with one token/registration attempt; repeated failure remains inconclusive |
| Immediate absence retry | Registration absence needs persisted observations separated by a positive bounded interval | Default three-second window uses an injected clock; immediate repetition is refused and interruptions reset the sequence |
| CLEANED regression | `CLEANED` is a convergent terminal action state | status, reconcile, and repeated cleanup return CLEANED without new runtime observations or mutation |
| Qualification input ambiguity | The workflow derives the expected runner suffix from the supplied action id before scheduling/inside the exact job | Shell contract proves `EXPECTED_RUNNER == runnerops-ephemeral-<sha256(EXPECTED_ACTION)[:16]>`; `RUNNER_NAME` equality remains required |
| Duplicate dogfood commands | The read-only `status`, `health`, `plan`, and `list` block runs once | Workflow source contract and review |

## Preserved safety boundaries

- The disposable root remains the exact derived action child.
- Ownership marker, symlink, path-escape, and symlink-safe recursive removal
  guards remain unchanged.
- systemd remains the local lifecycle authority; PID evidence is observational.
- Registration material is neither persisted nor passed in process arguments.
- Persistent runner registry and autoscale/capacity contracts are unchanged.
- The per-action bounded lock and narrow privileged systemd helper remain in use.

## Test evidence

The required targeted commands passed:

```text
python3 -B tests/ephemeral/test-ephemeral-foundation-contracts.py       11 passed
python3 -B tests/ephemeral/test-ephemeral-github-runtime-contracts.py   4 passed
python3 -B tests/ephemeral/test-ephemeral-cleanup-safety-contracts.py   9 passed
python3 -B tests/ephemeral/test-ephemeral-lifecycle-contracts.py       27 passed
bash tests/ephemeral/test-ephemeral-runtime-contracts.sh                PASS
bash tests/runner/test-runnerctl-contracts.sh                           PASS
bash tests/runner/test-runnerctl-routing-contracts.sh                   PASS
bash -n runnerctl scripts/runner/ephemeral.sh                           PASS
python3 -B -m py_compile src/runnerops/ephemeral/*.py                   PASS
```

The full local equivalent of the static `validate.yml` job also passed:

- shell syntax for `runnerctl`, installation/systemd scripts, and all shell tests;
- Python compilation for `src`, `tests`, and `.github/scripts`;
- release automation contract;
- 12 runner/ephemeral/skill shell contract files;
- 279 capacity, operational, autoscale, and ephemeral Python tests across 21 files;
- portable Agent Skill listing and dry-run installation;
- runner identity resolution, XDG defaults, governed removal plan, brand assets,
  Pages JavaScript/content, tracked-source portability, and action-to-identity derivation;
- `git diff --check`.

No mutating real-host qualification was dispatched in this round. The earlier
qualification of exact SHA
`3c83ddadef6f43c3de1638c2cca9239342f60c45` remains documented separately and
unchanged in
[`issue-120-real-host-qualification.md`](issue-120-real-host-qualification.md)
and its machine-readable evidence. Revision
`970a2669796598e7e1059ad491fdb838f5f34ab4` has static and adversarial contract
evidence only; it has not been qualified on a real host.

## Remaining limitation

A job that is shorter than the observation interval can complete without a
persisted `busy=true` sample. Local exit plus remote disappearance is not strong
enough to prove workload consumption, so V1 deliberately remains
`INCONCLUSIVE_TERMINAL`. Adding tamper-resistant workflow/job correlation is a
separate follow-up; this hardening does not infer a job conclusion.

## Review state

READY FOR ADVERSARIAL RE-REVIEW
