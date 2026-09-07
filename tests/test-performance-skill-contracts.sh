#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILL="$ROOT/skills/analyze-ci-workflow-performance/SKILL.md"

fail() {
  printf '[FAIL] %s\n' "$1" >&2
  exit 1
}

pass() {
  printf '[PASS] %s\n' "$1"
}

require_text() {
  local needle="$1" message="$2"
  grep -Fq -- "$needle" "$SKILL" || fail "$message"
}

require_absent() {
  local needle="$1" message="$2"
  if grep -Fq -- "$needle" "$SKILL"; then
    fail "$message"
  fi
}

[[ -f "$SKILL" ]] || fail "skill analyze-ci-workflow-performance ausente"

require_text "name: analyze-ci-workflow-performance" "frontmatter deve preservar nome canônico"
require_text "Do not rewrite workflow YAML unless the user explicitly asks" "skill deve ser read-only por padrão"

require_text "**STATIC**" "skill deve distinguir evidência STATIC"
require_text "**OBSERVED**" "skill deve distinguir evidência OBSERVED"
require_text "**ESTIMATED**" "skill deve distinguir evidência ESTIMATED"
require_text "Never present an estimated speedup as observed fact." "skill deve impedir ganho inventado"

require_text "needs:" "skill deve analisar DAG/needs"
require_text "matrix" "skill deve analisar matrix"
require_text "fail-fast" "skill deve analisar fail-fast"
require_text "paths-ignore" "skill deve analisar eficiência de triggers"
require_text "artifact" "skill deve analisar artifacts"
require_text "cache" "skill deve analisar cache"
require_text "queue time" "skill deve analisar fila"
require_text "critical path" "skill deve analisar critical path"
require_text "No-change decisions" "skill deve permitir concluir que não há otimização justificada"

require_text 'gh api "repos/<owner>/<repo>/actions/runs?per_page=30"' "skill deve consultar histórico real de runs via GET"
require_text 'gh api "repos/<owner>/<repo>/actions/runs/<run_id>/jobs?per_page=100"' "skill deve consultar jobs reais via GET"
require_text "These are read-only GET requests." "skill deve declarar boundary read-only da API"
require_text "Report the sample size." "skill deve exigir tamanho da amostra"

require_text "runnerctl ci watch . --pr <number> --json" "skill deve integrar estado conclusivo por PR"
require_text 'Do not use `runnerctl ci watch` as a substitute for historical duration or queue-time analysis.' "skill deve separar watch atual de histórico"
require_text "A CI failure is not automatically a runner failure." "skill deve preservar CI failure != runner failure"

require_text "Only edit workflow files after the user explicitly asks" "aplicação deve exigir pedido explícito"
require_text "make the smallest reasonable diff" "aplicação deve ser mínima e governada"
require_text "compare before/after GitHub Actions evidence" "mudança deve pedir validação antes/depois"

require_absent "gh api --method POST" "skill não pode executar mutação POST"
require_absent "gh api --method PATCH" "skill não pode executar mutação PATCH"
require_absent "gh api --method DELETE" "skill não pode executar mutação DELETE"
require_absent "gh workflow run" "skill não pode disparar workflow por padrão"
require_absent "gh run rerun" "skill não pode rerodar workflow por padrão"

skills_list="$("$ROOT/install-agent-skills.sh" --list)"
if ! grep -Fxq "analyze-ci-workflow-performance" <<< "$skills_list"; then
  fail "installer deve descobrir a nova skill automaticamente"
fi

pass "skill de performance preserva evidência, read-only e boundary provider-neutral"
