# Local ephemeral one-job lifecycle

`runnerctl ephemeral` is an explicit control-plane primitive for one disposable
GitHub Actions runner on a persistent Linux host. It is not an autoscaler and it
does not provide container/VM isolation.

## Public boundary

```bash
runnerctl ephemeral create .
runnerctl ephemeral status <action-id>
runnerctl ephemeral reconcile <action-id>
runnerctl ephemeral cleanup <action-id>
```

`create` performs real GitHub registration and is never called by the autoscale
planner/controller in this slice. It reuses the verified official runner package
cache, registers with `--ephemeral`, and starts one exact systemd template
instance. Run `runnerctl platform-authorize` after installing/upgrading so the
root-owned ephemeral unit template is present. If `RUNNER_EPHEMERAL_ROOT` changes,
repeat authorization so the root-owned template and runtime boundary agree.

## Identity, state and evidence

One 32-hex-character `action_id` deterministically maps to one identity:

```text
action_id -> runnerops-ephemeral-<16 stable sha256 hex chars>
```

There is no numeric collision fallback. An existing or ambiguous exact remote
identity moves the action to reconciliation instead of requesting another token.

The lifecycle keeps these dimensions separate in `EphemeralAction` v1:

| Dimension | Evidence |
|---|---|
| desired | `ONE_JOB_TERMINAL_AND_CLEANED` |
| action | `REQUESTED`, `REGISTERING`, `REGISTERED`, `ONLINE`, `BUSY`, `TERMINAL`, `CLEANUP_PENDING`, `CLEANED`, or an explicit inconclusive state |
| local | exact systemd unit, active/sub state, service result, observed main PID, local config identity |
| GitHub | exact identity, runner id, online/offline/busy/absent/ambiguous |
| workload | first/last exact `busy=true` observation; conclusion remains `unknown` unless separately proven |
| terminal | explicit evidence and reason; local exit alone is insufficient |
| cleanup | attempts and independent remote/local/root results |

Evidence is written atomically with mode `0600` under:

```text
$RUNNER_STATE_ROOT/ephemeral/actions/<action-id>.json
```

Registration material and credentials are never fields in this contract. The
autoscale SQLite store, `CapacitySnapshot`, `OperationalEvidence v1`, and
`OperationalReview v1` are not changed.

## Registration uncertainty

If `config.sh` may have reached GitHub but did not return conclusively, the action
becomes `INCONCLUSIVE_REGISTRATION`. Re-running `create --action-id ...` is refused.

`reconcile` looks only for the same exact identity. If it exists and the matching
local config exists, RunnerOps resumes that registration without requesting new
material. If it is absent, two consecutive conclusive reconciliation observations
are required before the same action is authorized to retry registration.

## Cleanup safety

The disposable root is exactly:

```text
$RUNNER_EPHEMERAL_ROOT/<action-id>
```

Cleanup derives that path again, requires the exact ownership marker, rejects
symlink/path escape and never reads or writes the persistent runner registry.
Current exact `BUSY` evidence refuses destructive cleanup. Remote DELETE must be
followed by observed exact absence; the systemd unit must stop; only then is the
owned root removed. Repeated cleanup returns the same `CLEANED` result.

## Explicit real-host qualification

This sequence mutates GitHub and must be run deliberately on a trusted RunnerOps
host. The exact qualification job contains no checkout and executes only the
repository-owned fixed shell step. `validate.yml` already exists on the default
branch, so GitHub can dispatch its trusted feature-branch revision before merge.

```bash
# 1. Preserve before evidence for the persistent pool.
runnerctl list
runnerctl health all
runnerctl capacity . --json > /tmp/runnerops-capacity-before.json

# 2. Create one exact ephemeral runner and capture its durable identifiers.
create_json="$(runnerctl ephemeral create . \
  --profile generic \
  --labels runnerops-ephemeral-qualification \
  --json)"
action_id="$(printf '%s' "$create_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["action_id"])')"
runner_identity="$(printf '%s' "$create_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["runner_identity"])')"

# 3. Dispatch the controlled exact-label job from the published trusted branch.
qualification_ref="$(git branch --show-current)"
gh workflow run validate.yml \
  --repo "$(runnerctl repo .)" \
  --ref "$qualification_ref" \
  -f ephemeral_runner_label="$runner_identity" \
  -f ephemeral_action_id="$action_id"

# 4. During the fixed 20-second workload, observe exact ONLINE -> BUSY evidence.
runnerctl ephemeral status "$action_id" --json
runnerctl ephemeral reconcile "$action_id" --json

# 5. Watch the controlled run, then reconcile until TERMINAL is evidenced.
run_id="$(gh run list --workflow validate.yml --branch "$qualification_ref" \
  --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$run_id" --exit-status
runnerctl ephemeral reconcile "$action_id" --json

# 6. Perform bounded cleanup and prove the persistent pool remained healthy.
runnerctl ephemeral cleanup "$action_id" --json
runnerctl ephemeral status "$action_id" --json
runnerctl list
runnerctl health all
runnerctl capacity . --json > /tmp/runnerops-capacity-after.json
```

If the first post-job reconciliation is still eventual/inconclusive, repeat only
`status`/`reconcile`; do not repeat `create`. Save the action JSON, workflow run URL,
and before/after capacity artifacts as the qualification transcript.

Until that transcript exists on a real host, the implementation status is:

```text
CODE COMPLETE / CONTRACTS GREEN
REAL-HOST QUALIFICATION PENDING
```
