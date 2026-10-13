# Workflow Policy Reconciliation — #148 (P1)

**Boundary:** read-only comparison between workflow job demand and *provided*
policy evidence. Does not implement the separate policy-management issue #144.

    runnerctl workflow reconcile . --json
    runnerctl workflow reconcile . --policy-evidence /tmp/policy.json --repository example/api --json

The preferred portable flow against *shipped public* RunnerOps contracts is:

    runnerctl autoscale plan example/api --json > plan.json
    runnerctl workflow reconcile . --policy-evidence plan.json --repository example/api --json

An AutoscalePlan v1 contains public label scope and provisioning/ephemeral
template labels. It does NOT prove template enablement or the current effective
scheduler policy, so the result explicitly remains unknown unless another
versioned policy evidence object confirms enablement. This avoids falsely
concluding that a configured runner can already accept jobs.

Without a policy snapshot, self-hosted jobs become
POLICY_EVIDENCE_MISSING/unknown, rather than assuming current policy. A policy
evidence file has schema_version=1, repository=owner/repo,
optional fingerprint, and a typed policy object:

    {
      "schema_version": 1,
      "repository": "example/api",
      "fingerprint": "sha256:operator-supplied",
      "policy": {
        "label_scope": ["self-hosted", "linux", "local-runner"],
        "local_provision": {
          "enabled": true,
          "labels": ["self-hosted", "linux", "x64", "local-runner"]
        },
        "local_ephemeral": {"enabled": false, "labels": []}
      }
    }

The --repository value must match the policy evidence repository; otherwise
reconciliation fails closed. Without that explicit identity, policy-dependent
job results remain POLICY_REPOSITORY_NOT_VERIFIED/unknown.

This is an **operator-supplied fixture**, not a claim that RunnerOps can yet
fetch or apply canonical effective policy through the #144 interface.
Workflow labels are matched case-insensitively. label_scope must be a subset
of job required labels; enabled provision/ephemeral templates must offer all
job labels. Hosted jobs are not capacity demand for RunnerOps. If no verified
capacity template exists, report unknown, not BLOCKED: existing persistent
runners may still satisfy the job.

The result exposes status, policy_source, policy_fingerprint, per-job
reason/status and a next action. A matching label set does **not** establish
online capacity, matching runner groups, scheduler health or execution success.

Never edit scheduler environment files, start runners, change policy or enable
autoscale during reconciliation. #144 remains the separately governed public
policy show/plan/apply dependency for mutation.

    python3 -B -m unittest tests.workflows.test_reconciliation
