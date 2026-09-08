#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRE="$ROOT/skills/runnerops-pr-validation/SKILL.md"
MANAGE="$ROOT/skills/runnerops-manage-runners/SKILL.md"

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

require_absent_command() {
  local file="$1" command="$2" message="$3"
  if grep -Eq "^[[:space:]]*${command}([[:space:]]|$)" "$file"; then
    fail "$message"
  fi
}

[[ -f "$PRE" ]] || fail "skill pre-PR ausente"
[[ -f "$MANAGE" ]] || fail "skill de gestão ausente"

require_text "$PRE" "name: runnerops-pr-validation" "skill pre-PR deve usar nome RunnerOps canônico"
require_text "$MANAGE" "name: runnerops-manage-runners" "skill de gestão deve usar nome RunnerOps canônico"

require_text "$PRE" "runnerctl ensure ." "skill pre-PR deve usar runnerctl ensure ."
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

require_text "$MANAGE" "After every explicit start or restart, verify the result:" "skill de gestão deve validar start/restart"
require_text "$MANAGE" "runnerctl status <runner>" "skill de gestão deve verificar status após lifecycle"
require_text "$MANAGE" "runnerctl health <runner>" "skill de gestão deve verificar health após lifecycle"
require_text "$MANAGE" "not guaranteed to be repository-scoped" "skill de gestão deve alertar que grupos podem cruzar repositórios"
require_text "$MANAGE" 'do **not** repeat `runnerctl add`' "skill de gestão deve evitar add duplicado após PARTIAL"
require_text "$MANAGE" "[INCONCLUSIVE] ... remote-registration=unknown" "skill de gestão deve tratar provisioning inconclusivo"
require_text "$MANAGE" '**provisioned**, not necessarily **available now**' "skill de gestão deve distinguir provisionado de capacidade imediata"
require_text "$MANAGE" "state=unknown" "skill de gestão deve tratar lifecycle unknown"
require_text "$MANAGE" "Do not paraphrase that as full functional integrity" "skill de gestão não deve superestimar doctor"
require_text "$MANAGE" "Never start a shared group when repository-scoped or exact-runner operation satisfies the request." "skill de gestão deve preferir operação repo-scoped/exata"

# Executable examples must stay on the public boundary. Mentions in prose such as
# "do not call systemctl" are allowed; direct command examples are not.
require_absent_command "$PRE" "runners\.sh" "skill pre-PR não pode executar runners.sh"
require_absent_command "$PRE" "runner-services\.sh" "skill pre-PR não pode executar runner-services.sh"
require_absent_command "$PRE" "systemctl" "skill pre-PR não pode executar systemctl diretamente"
require_absent_command "$MANAGE" "runners\.sh" "skill de gestão não pode executar runners.sh"
require_absent_command "$MANAGE" "runner-services\.sh" "skill de gestão não pode executar runner-services.sh"
require_absent_command "$MANAGE" "systemctl" "skill de gestão não pode executar systemctl diretamente"

tmp_home="$(mktemp -d)"
trap 'rm -rf "$tmp_home"' EXIT
mkdir -p "$tmp_home/.agents/skills/manage-local-github-runners"
printf '%s\n' legacy > "$tmp_home/.agents/skills/manage-local-github-runners/marker"
HOME="$tmp_home" "$ROOT/install-agent-skills.sh" --tool agents --skill runnerops-manage-runners >/dev/null
[[ ! -e "$tmp_home/.agents/skills/manage-local-github-runners" ]] || fail "installer deve remover nome legado correspondente"
[[ -f "$tmp_home/.agents/skills/runnerops-manage-runners/SKILL.md" ]] || fail "installer deve instalar nome canônico novo"

skills_list="$("$ROOT/install-agent-skills.sh" --list)"
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
