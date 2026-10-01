# GitHub Copilot instructions for RunnerOps

RunnerOps is the product in this repository. `runnerctl` is the stable public interface. Prefer current public contracts over internal implementation shortcuts.

## Product boundaries

- Keep Linux + systemd as the primary runtime model.
- Preserve `RUNNER_BOOT_POLICY=on-demand`: healthy idle capacity does not need to be active.
- Keep deterministic autoscale planning separate from mutation.
- Do not replace planner/controller decisions with LLM heuristics.
- Prefer governed autoscale or one exact runner over broad group/fleet activation.
- Treat `runnerctl ensure .` as an explicit repository-wide activation override, not the default capacity-management path.
- Treat the one-job ephemeral lifecycle according to the capability actually shipped; do not describe an explicit primitive as automatic ephemeral autoscaling before the planner/controller supports it.
- Never expose registration tokens, machine-local registry contents, credentials, personal hostnames, or maintainer-specific paths in committed examples.

## Mandatory Agent Skills synchronization

Agent Skills are a maintained product interface, not optional documentation.

Every functional change must perform an Agent Skills impact review.

A change that adds or changes any of the following must update the affected skills in the same PR:

- public `runnerctl` commands or options;
- lifecycle semantics;
- capacity/autoscale decisions or policies;
- provisioning/reconciliation behavior;
- ephemeral runner behavior;
- safety or mutation boundaries;
- recovery guidance;
- exit-code/error semantics;
- CI-watch behavior;
- default operational preferences.

Always inspect:

```text
skills/README.md
skills/*/SKILL.md
tests/skills/test-agent-skills-contracts.sh
```

No functional change is complete while an affected skill still teaches obsolete behavior.

When updating skills:

1. reflect current shipped behavior, not planned/future behavior;
2. prefer `runnerctl` public commands over internal scripts;
3. preserve deterministic planner/controller authority;
4. never let an agent synthesize its own autoscale decision when RunnerOps owns that decision;
5. distinguish explicit primitives from automatic control-plane features;
6. update skill contracts so the intended behavior cannot silently regress;
7. run the Agent Skills validation.

If a functional change genuinely has no agent-facing operational impact, state explicitly in the PR/report:

```text
Agent Skills impact: none
Reason: <short concrete reason>
```

Do not use “no impact” to avoid reviewing the skills.

## Validation

For Agent Skills changes, run at least:

```bash
bash tests/skills/test-agent-skills-contracts.sh
./scripts/setup/install-agent-skills.sh --list
./scripts/setup/install-agent-skills.sh --tool all --dry-run
```

Also run the domain-specific tests required by `AGENTS.md` for the runtime area being changed.
