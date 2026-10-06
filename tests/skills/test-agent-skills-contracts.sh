#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PRE="$ROOT/skills/runnerops-pr-validation/SKILL.md"
MANAGE="$ROOT/skills/runnerops-manage-runners/SKILL.md"
PERF="$ROOT/skills/runnerops-ci-performance/SKILL.md"
AGENTS="$ROOT/AGENTS.md"
COPILOT="$ROOT/.github/copilot-instructions.md"

fail() {
  printf '[FAIL] %s\n' "$1" >&2
  exit 1
}

pass() {
  printf '[PASS] %s\n' "$1"
}

require_text() {
  local file="$1" needle="$2" message="$3"
  grep -Fq -- "$needle" "$file" || fail "$message"
}

require_absent_executable_example() {
  local file="$1" command="$2" message="$3"
  local examples
  examples="$(awk '/^```/ { in_code = !in_code; next } in_code { print }' "$file")"
  if grep -Eq "^[[:space:]]*(sudo[[:space:]]+)?(bash[[:space:]]+)?${command}([[:space:]]|$)" <<< "$examples"; then
    fail "$message"
  fi
}

[[ -f "$PRE" ]] || fail "skill pre-PR ausente"
[[ -f "$MANAGE" ]] || fail "skill de gestão ausente"
[[ -f "$PERF" ]] || fail "skill de performance ausente"
[[ -f "$AGENTS" ]] || fail "AGENTS.md ausente"
[[ -f "$COPILOT" ]] || fail "copilot instructions ausente"

require_text "$PRE" "name: runnerops-pr-validation" "skill pre-PR deve usar nome RunnerOps canônico"
require_text "$MANAGE" "name: runnerops-manage-runners" "skill de gestão deve usar nome RunnerOps canônico"

require_text "$PRE" "runnerctl overview ." "skill pre-PR deve inspecionar overview sem mutar"
require_text "$PRE" "runnerctl capacity . --json" "skill pre-PR deve inspecionar capacidade"
require_text "$PRE" "runnerctl autoscale status . --json" "skill pre-PR deve inspecionar autoscale"
require_text "$PRE" 'Do not call `runnerctl ensure .` by default.' "skill pre-PR não deve usar ensure como gate padrão"
require_text "$PRE" 'allowed only when the user explicitly asks to activate repository-wide capacity' "skill pre-PR deve reservar ensure para override explícito"
require_text "$PRE" "runnerctl ci watch . --pr <number> --json" "skill pre-PR deve preferir watcher por PR"
require_text "$PRE" "runnerctl ci watch . --json" "skill pre-PR deve suportar watcher por HEAD"
require_text "$PRE" '`0`: CI completed successfully.' "skill pre-PR deve documentar exit 0"
require_text "$PRE" '`1`: CI/workflow failed.' "skill pre-PR deve documentar exit 1"
require_text "$PRE" '`2`: infrastructure/access failure.' "skill pre-PR deve documentar exit 2"
require_text "$PRE" '`3`: timeout, cancellation or inconclusive state.' "skill pre-PR deve documentar exit 3"
require_text "$PRE" "run_attempt" "skill pre-PR deve orientar reruns por run_attempt"
require_text "$PRE" "Do not restart/remove a runner because a test, lint or build step failed." "skill pre-PR deve preservar boundary CI != runner"

require_text "$MANAGE" "runnerctl ci watch . --pr <number> --json" "skill de gestão deve conhecer watcher por PR"
require_text "$MANAGE" "runnerctl ci watch owner/repo --sha <sha> --json" "skill de gestão deve conhecer watcher por SHA"
require_text "$MANAGE" "Never claim CI success from an inconclusive watcher result." "skill de gestão deve preservar semântica inconclusiva"

require_text "$MANAGE" "runnerctl overview ." "skill de gestão deve começar por evidência do repo"
require_text "$MANAGE" "runnerctl capacity . --json" "skill de gestão deve inspecionar capacidade"
require_text "$MANAGE" "runnerctl autoscale status . --json" "skill de gestão deve conhecer autoscale"
require_text "$MANAGE" 'not the default' "skill de gestão deve tratar ensure como override e não padrão"
require_text "$MANAGE" "runnerctl ephemeral create ." "skill de gestão deve conhecer lifecycle ephemeral explícito"
require_text "$MANAGE" 'Only the planner selects `CREATE_EPHEMERAL`' "skill de gestão deve preservar autoridade do planner"
require_text "$MANAGE" 'RUNNER_AUTOSCALE_LOCAL_EPHEMERAL_ENABLED=true' "skill de gestão deve documentar opt-in ephemeral"
require_text "$MANAGE" "After every explicit start or restart, verify the result:" "skill de gestão deve validar start/restart"
require_text "$MANAGE" "runnerctl status <runner>" "skill de gestão deve verificar status após lifecycle"
require_text "$MANAGE" "runnerctl health <runner>" "skill de gestão deve verificar health após lifecycle"
require_text "$MANAGE" "not guaranteed to be repository-scoped" "skill de gestão deve alertar que grupos podem cruzar repositórios"
require_text "$MANAGE" 'do **not** repeat `runnerctl add`' "skill de gestão deve evitar add duplicado após PARTIAL"
require_text "$MANAGE" "[INCONCLUSIVE] ... remote-registration=unknown" "skill de gestão deve tratar provisioning inconclusivo"
require_text "$MANAGE" '**provisioned**, not necessarily **available now**' "skill de gestão deve distinguir provisionado de capacidade imediata"
require_text "$MANAGE" "state=unknown" "skill de gestão deve tratar lifecycle unknown"
require_text "$MANAGE" "Do not paraphrase that as full functional integrity" "skill de gestão não deve superestimar doctor"
require_text "$MANAGE" "Never start a shared group when governed autoscale or exact-runner operation satisfies the request." "skill de gestão deve preferir autoscale/exato"
require_text "$MANAGE" 'Never use `runnerctl ensure .` as the default repository operation' "skill de gestão deve proibir ensure como default"
require_text "$MANAGE" 'Never call `runnerctl ephemeral create` merely because a queue exists' "skill não pode usar ephemeral como decisão LLM de autoscale"

require_text "$PERF" "runnerctl capacity . --json" "skill de performance deve usar evidência de capacidade"
require_text "$PERF" "runnerctl autoscale status . --json" "skill de performance deve ser autoscale-aware"
require_text "$PERF" 'Queue pressure does not imply “start all runners”.' "skill de performance não deve recomendar broad start por fila"
require_text "$PERF" 'CREATE_EPHEMERAL' "skill de performance deve distinguir primitive ephemeral de autoscale automático"
require_text "$PRE" 'opt-in `CREATE_EPHEMERAL`' "skill pre-PR deve conhecer boundary ephemeral governada"

require_text "$AGENTS" "Sincronização obrigatória das Agent Skills" "AGENTS deve exigir sincronização das skills"
require_text "$AGENTS" 'Nenhuma mudança funcional está completa até que o impacto em `skills/` tenha sido revisado.' "AGENTS deve tratar skill review como gate funcional"
require_text "$AGENTS" "tests/skills/test-agent-skills-contracts.sh" "AGENTS deve exigir contratos das skills"
require_text "$COPILOT" "Mandatory Agent Skills synchronization" "Copilot instructions deve exigir sincronização das skills"
require_text "$COPILOT" "No functional change is complete while an affected skill still teaches obsolete behavior." "Copilot deve bloquear skill drift"
require_text "$COPILOT" "Agent Skills impact: none" "Copilot deve exigir justificativa quando não houver impacto"

# Executable examples must stay on the public boundary. Prose may explain that
# internal scripts and systemctl must not be called directly.
for skill in "$PRE" "$MANAGE" "$PERF"; do
  require_text "$skill" 'Use `runnerctl` as the public interface' "skills devem apontar runnerctl como interface pública"
  require_absent_executable_example "$skill" '(\./)?scripts/runner/lifecycle\.sh' "skill não pode executar scripts/runner/lifecycle.sh"
  require_absent_executable_example "$skill" '(\./)?scripts/runner/services\.sh' "skill não pode executar scripts/runner/services.sh"
  require_absent_executable_example "$skill" '(\./)?scripts/runner/configure\.sh' "skill não pode executar scripts/runner/configure.sh"
  require_absent_executable_example "$skill" 'systemctl' "skill não pode executar systemctl diretamente"
done

tmp_home="$(mktemp -d)"
trap 'rm -rf "$tmp_home"' EXIT
mkdir -p "$tmp_home/.agents/skills/manage-local-github-runners"
printf '%s\n' legacy > "$tmp_home/.agents/skills/manage-local-github-runners/marker"
HOME="$tmp_home" "$ROOT/scripts/setup/install-agent-skills.sh" --tool agents --skill runnerops-manage-runners >/dev/null
[[ ! -e "$tmp_home/.agents/skills/manage-local-github-runners" ]] || fail "installer deve remover nome legado correspondente"
[[ -f "$tmp_home/.agents/skills/runnerops-manage-runners/SKILL.md" ]] || fail "installer deve instalar nome canônico novo"

skills_list="$("$ROOT/scripts/setup/install-agent-skills.sh" --list)"
for skill in runnerops-ci-performance runnerops-manage-runners runnerops-pr-validation; do
  grep -Fxq "$skill" <<< "$skills_list" || fail "installer deve listar $skill"
done

if grep -Eq '^(start-project-runners-before-pr|manage-local-github-runners|analyze-ci-workflow-performance)$' <<< "$skills_list"; then
  fail "installer não deve listar nomes legados"
fi

if grep -Ev '^runnerops-' <<< "$skills_list" | grep -q .; then
  fail "todas as skills canônicas devem usar prefixo runnerops-"
fi

pass "Agent Skills RunnerOps preservam boundary, descoberta e migração de nomes"
