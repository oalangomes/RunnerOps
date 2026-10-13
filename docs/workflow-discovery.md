# Workflow Discovery & Audit — #146

**Boundary:** offline, read-only static GitHub Actions inspection.

    runnerctl workflow discover . --json
    runnerctl workflow discover /path/to/project --json
    runnerctl workflow discover /path/to/project/.github/workflows/ci.yml --json

## Contract

Scans only *.yml/*.yaml under .github/workflows (or a single explicit file),
in lexical order; no calls to GitHub, no YAML command execution, no file edits.
Produces WorkflowDiscovery schema_version=1, deterministic SHA-256 fingerprint
of path+content hashes, workflows, triggers, jobs, runner_kind, label arrays,
job needs and diagnostics with file/job/code/severity.

Supported static runs-on shapes are literal scalar GitHub-hosted images and
inline bracketed self-hosted labels. Multiline mappings, matrix expressions,
YAML aliases/anchors and other unrecognized routes are UNKNOWN/INCONCLUSIVE,
not inferred. This **is not** a full YAML validator or CI simulation.

The extractor is bounded to 100 workflow files and 256 KiB each. Symlink files
are rejected. Jobs with externally sourced triggers and existing self-hosted
routing are identified with UNTRUSTED_EVENT_SELF_HOSTED.

## Status and exits

- exit 0 + complete: bounded static extraction succeeded
- exit 3 + inconclusive: incomplete/missing/dynamic evidence
- exit 2 + error: invalid input/IO/implementation failure

Review every diagnostic before adoption. Never treat UNKNOWN as approval.

## Tests

    python3 -B -m unittest tests.workflows.test_discovery
