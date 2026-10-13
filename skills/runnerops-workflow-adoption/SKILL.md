---
name: runnerops-workflow-adoption
description: Govern RunnerOps GitHub Actions workflow discovery, planned runner adoption, read-only policy reconciliation, and evidence-grounded CI intelligence without bypassing the autoscale planner.
---

# RunnerOps Workflow Adoption

Use `runnerctl` as the public interface. The RunnerOps Operator composes this
skill with `runnerops-ci-performance` and `runnerops-manage-runners`.

## 1. Discovery & Audit (read-only)

    runnerctl workflow discover . --json

Inspect status, fingerprint, triggers, each jobs runner_kind/labels/needs and
diagnostics. The bounded extractor is not a complete YAML validator.
Dynamic expressions and unsupported syntax remain UNKNOWN. Validate any proposed
workflow changes with GitHub Actions before merge.

## 2. Adoption (human-gated)

    runnerctl workflow adopt . --profile local-first --plan --json
    runnerctl workflow adopt . --profile hosted --plan --json
    runnerctl workflow adopt . --profile hybrid --job ci.yml:test --plan --json

A plan is a review artifact. Never fabricate the fingerprint, silently choose
self-hosted for untrusted PR events, or edit a job that the plan marks blocked.
When a user explicitly approves the exact plan and repository changes:

    runnerctl workflow adopt . --profile local-first --apply --expect-fingerprint SHA --yes --json

Review git diff, commit and open a normal PR. Do not auto-merge or silently
edit workflows. No workflow job may start RunnerOps itself to obtain its runner.

## 3. Policy Reconciliation (read-only)

    runnerctl workflow reconcile . --json
    runnerctl workflow reconcile . --policy-evidence policy.json --repository owner/repo --json

The evidence file must be explicitly provided and versioned, and is not proof
of live effective scheduler state. UNKNOWN is the correct response when public
policy evidence is absent. Do not edit env files, scheduler units or call
autoscale run-once to solve a configuration mismatch. Canonical policy
show/plan/apply is tracked separately in #144.

## 4. Intelligence (read-only, AI opt-in)

    runnerctl workflow intelligence . --since 24h --json
    runnerctl workflow intelligence --evidence evidence.json --json
    runnerctl workflow intelligence --evidence evidence.json --provider ollama --model MODEL --json

Replay must be bounded and not collect new evidence. The existing reviewer
handles consent and provider security. Any remote model transmission requires
explicit --allow-remote. Never turn a finding into a runner action.

## Non-negotiables

- Discovery does not run workflow contents.
- Changes always require explicit intent, exact fingerprint and git review.
- Policy reconciler does not pretend #144 is already delivered.
- Never use start all, ensure . or direct ephemeral create to fix queue pressure.
- Never alter autoscale policy or labels from LLM-generated guesses.
- Preserve UNKNOWN/PARTIAL/INCONCLUSIVE honestly and verify postconditions.
