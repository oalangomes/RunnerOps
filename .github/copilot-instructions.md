# GitHub Copilot instructions for RunnerOps

RunnerOps is the product in this repository. `runnerctl` is the stable public interface. Prefer public CLI behavior and repository contracts over internal implementation shortcuts.

## Product and architecture

- Keep Linux + systemd as the primary runtime model.
- Preserve `RUNNER_BOOT_POLICY=on-demand` semantics: healthy idle capacity does not need to be active.
- Keep deterministic autoscale planning separate from mutation.
- Do not replace planner/controller decisions with LLM heuristics.
- Prefer exact-runner or governed autoscale operations over broad group/fleet activation.
- Treat `runnerctl ensure .` as an explicit repository-wide activation override, not the default capacity-management path.
- The one-job ephemeral lifecycle is an explicit primitive unless/until the deterministic planner/controller supports automatic ephemeral scaling.
- Never expose registration tokens, machine-local registry contents, credentials, personal hostnames, or maintainer-specific paths in committed examples.

## Mandatory Agent Skills synchronization

Agent Skills are a maintained product interface, not optional documentation.

For every functional change, first perform an Agent Skills impact review.

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

1. reflect the current public behavior, not planned/future behavior;
2. prefer `runnerctl` public commands over internal scripts;
3. preserve deterministic planner/controller authority;
4. do not let an agent synthesize its own autoscale decision when RunnerOps owns that decision;
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
