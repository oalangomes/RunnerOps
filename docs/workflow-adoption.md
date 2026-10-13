# Workflow Adoption — #147 (P0)

**Boundary:** deterministic line-preserving plan and explicit checked apply.

    runnerctl workflow adopt . --profile local-first --plan --json
    runnerctl workflow adopt . --profile hosted --plan --json
    runnerctl workflow adopt . --profile hybrid --job ci.yml:test --plan --json

The plan includes schema_version, profile, source_fingerprint, fingerprint,
changes (file/job/line,
from/to/original file hash), blockers and mutation=false. It is not an LLM
authored YAML rewrite. Profiles: hosted => ubuntu-latest;
local-first => [self-hosted, Linux, X64, local-runner]. hybrid changes
only explicitly selected jobs (--job FILE.yml:JOB; repeatable) using local-first
labels while preserving other jobs (including deploy or sensitive build jobs).

### Apply only after explicit review

    runnerctl workflow adopt . --profile local-first --apply --expect-fingerprint SHA_FROM_PLAN --yes --json
    runnerctl workflow adopt . --profile hybrid --job ci.yml:test --apply --expect-fingerprint SHA_FROM_PLAN --yes --json
    git diff -- .github/workflows

Exact plan fingerprint binds profile, selected jobs, source contents and change
set, and is checked again. A fingerprint for a different profile or job selection
is never accepted, even against identical workflow source files. The original file hash and source
line must match. Every candidate must pass the audit before the first edit.
Each modified file is atomically replaced using a same-directory temporary file.
Use --profile hybrid --job FILE.yml:JOB (repeatable) to adopt only selected
jobs; diagnostics on unrelated jobs remain visible in Discovery but do not
block the selected job unless they are global to the workflow.

Cross-file application is **not** a transaction; any partial failure is
reported as PARTIAL_ADOPTION and must be reviewed before retry.

### Safety

local-first is blocked for pull_request, pull_request_target, workflow_call,
workflow_run, repository_dispatch, issue_comment, unknown and other external
triggers. It also blocks unsupported/dynamic runs-on, unreviewed job isolation,
and nonstandard hosted platforms. Reusable jobs are not rewritten. Hosted
migration still fails closed for unknown/unsupported runs-on.

This capability does not install a runner or enable autoscale. The operator
should prepare a normal PR, inspect workflow permissions and test its YAML
using GitHub validation. Do not automatically merge. Do not route untrusted
fork code to persistent local runners.

### Status / tests

0: plan ready or applied; 3: blocked/inconclusive; 2: invalid input/stale plan.
A repeated plan with no changes is idempotent.

    python3 -B -m unittest tests.workflows.test_adoption
