# Portable Agent Skills

These are the canonical self-hosted runner skills for this repository.

They use the portable `SKILL.md` Agent Skills format so the workflow itself does not need a separate implementation for each coding agent.

## Included skills

| Skill | Purpose |
|---|---|
| `start-project-runners-before-pr` | Before publishing a PR, start and validate only the local runners mapped to the current repository. |
| `manage-local-github-runners` | Inventory, start/stop, diagnose, register and validate local GitHub Actions runners for personal repositories. |

## Install

Install both skills for the primary user-level targets (Codex, Copilot and Claude):

```bash
./install-agent-skills.sh --tool all
```

Or choose one tool:

```bash
./install-agent-skills.sh --tool codex
./install-agent-skills.sh --tool copilot
./install-agent-skills.sh --tool claude
./install-agent-skills.sh --tool agents
```

Install only one skill:

```bash
./install-agent-skills.sh \
  --tool claude \
  --skill manage-local-github-runners
```

Preview without writing:

```bash
./install-agent-skills.sh --tool all --dry-run
```

## User-level destinations

| Target | Destination |
|---|---|
| Codex | `~/.codex/skills/<skill>/SKILL.md` |
| GitHub Copilot CLI | `~/.copilot/skills/<skill>/SKILL.md` |
| Claude Code | `~/.claude/skills/<skill>/SKILL.md` |
| Generic Agent Skills | `~/.agents/skills/<skill>/SKILL.md` |

The generic `~/.agents/skills` target is useful for tools that support the shared Agent Skills convention. It is installed only when `--tool agents` is requested, avoiding duplicate discovery in clients that also scan their own tool-specific directory.

## Project-local install

To install into another repository instead of your home directory:

```bash
./install-agent-skills.sh \
  --tool copilot \
  --scope project \
  --project-dir ~/projects/example
```

`--tool all` is intentionally rejected with `--scope project`, because some agents discover more than one project-level skills directory. Choose one explicit project target to avoid duplicate skill discovery.

Project destinations:

| Target | Destination |
|---|---|
| Codex | `<repo>/.codex/skills` |
| GitHub Copilot | `<repo>/.github/skills` |
| Claude Code | `<repo>/.claude/skills` |
| Generic Agent Skills | `<repo>/.agents/skills` |

## Configuration contract

The skills do not contain machine-specific runner inventory.

They expect the runner platform to resolve its local state using:

```bash
ACTIONS_RUNNERS_HOME=~/actions-runners
RUNNERS_CONFIG=~/.config/actions-runners/runners.conf
RUNNER_DATA_ROOT=~/.local/share/actions-runners/runners
RUNNER_BOOT_POLICY=on-demand
```

The skills never embed or persist GitHub runner registration tokens.

## Compatibility

The previous provider-specific `codex-skills/` tree has been removed. Canonical Agent Skills live only under `skills/` and should be installed through `install-agent-skills.sh`.

Existing copies already installed under a client-specific home directory continue to be local files; reinstall to receive current provider-neutral versions.

Provider-specific adapters should only be introduced when a tool requires behavior that cannot be expressed through the shared `SKILL.md`.
