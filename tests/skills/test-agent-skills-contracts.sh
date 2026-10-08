#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PRE="$ROOT/skills/runnerops-pr-validation/SKILL.md"
MANAGE="$ROOT/skills/runnerops-manage-runners/SKILL.md"
PERF="$ROOT/skills/runnerops-ci-performance/SKILL.md"
OPERATOR="$ROOT/agents/runnerops-operator/AGENT.md"
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
[[ -f "$OPERATOR" ]] || fail "agent runnerops-operator ausente"
[[ -f "$AGENTS" ]] || fail "AGENTS.md ausente"
[[ -f "$COPILOT" ]] || fail "copilot instructions ausente"

require_text "$PRE" "name: runnerops-pr-validation" "skill pre-PR deve usar nome RunnerOps canônico"
require_text "$MANAGE" "name: runnerops-manage-runners" "skill de gestão deve usar nomrequire_text "$OPERATOR" 'runnerops-operator' "agent deve definir a identidade canônica do operador"
require_text "$OPERATOR" 'runnerctl autoscale enable owner/repo' "agent deve preservar o contrato repository-scoped do core"
require_text "$OPERATOR" 'enable-all' "agent deve rejeitar a abstração multi-repo no core"
require_text "$OPERATOR" 'runnerops-manage-runners' "agent deve compor a skill de gestão"
require_text "$OPERATOR" 'runnerops-pr-validation' "agent deve compor a skill de PR validation"
require_text "$OPERATOR" 'runnerops-ci-performance' "agent deve compor a skill de performance"e RunnerOps canônico"
require_text "$OPERATOR" "name: runnerops-operator" "agent deve usar nome RunnerOps canônico"
require_text "$OPERATOR" 'runnerops-operator' "agent deve definir a identidade canônica do operador"
require_text "$OPERATOR" 'runnerctl autoscale enable owner/repo' "agent deve preservar o contrato repository-scoped do core"
require_text "$OPERATOR" 'enable-all' "agent deve rejeitar a abstração multi-repo no core"
require_text "$OPERATOR" 'runnerops-manage-runners' "agent deve compor a skill de gestão"
require_text "$OPERATOR" 'runnerops-pr-validation' "agent deve compor a skill de PR validation"
require_text "$OPERATOR" 'runnerops-ci-performance' "agent deve compor a skill de performance"

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
require_text "$PRE" 'opt-in `CREATE_EPHEMERAL`' "skill pre-PR deve conhecer boundary ephemeral governada"

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
require_text "$MANAGE" "runnerctl platform-authorize" "skill de gestão deve exigir gate humano para autorizar runtime"
require_text "$MANAGE" "runnerctl init" "skill de gestão deve documentar bootstrap do host"
require_text "$MANAGE" "runnerctl platform-doctor" "skill de gestão deve validar doctor do host"
require_text "$MANAGE" "gh auth status" "skill de gestão deve validar auth do GitHub"
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

require_text "$AGENTS" "Sincronização obrigatória das Agent Skills" "AGENTS deve exigir sincronização das skills"
require_text "$AGENTS" 'Nenhuma mudança funcional está completa até que o impacto em `skills/` tenha sido revisado.' "AGENTS deve tratar skill review como gate funcional"
require_text "$AGENTS" "tests/skills/test-agent-skills-contracts.sh" "AGENTS deve exigir contratos das skills"
require_text "$COPILOT" "Mandatory Agent Skills synchronization" "Copilot instructions deve exigir sincronização das skills"
require_text "$COPILOT" "No functional change is complete while an affected skill still teaches obsolete behavior." "Copilot deve bloquear skill drift"
require_text "$COPILOT" "Agent Skills impact: none" "Copilot deve exigir justificativa quando não houver impacto"

# Executable examples must stay on the public boundary. Prose may explain that
# internal scripts and systemctl must not be called directly.
for skill in "$PRE" "$MANAGE" "$PERF" "$OPERATOR"; do
  require_text "$skill" 'Use `runnerctl` as the public interface' "skills devem apontar runnerctl como interface pública"
  require_absent_executable_example "$skill" '(\./)?scripts/runner/lifecycle\.sh' "skill não pode executar scripts/runner/lifecycle.sh"
  require_absent_executable_example "$skill" '(\./)?scripts/runner/services\.sh' "skill não pode executar scripts/runner/services.sh"
  require_absent_executable_example "$skill" '(\./)?scripts/runner/configure\.sh' "skill não pode executar scripts/runner/configure.sh"
  require_absent_executable_example "$skill" 'systemctl' "skill não pode executar systemctl diretamente"
done

# --skill selects exactly one target; each scenario must run in a fresh HOME to avoid cross-contamination.
skill_home="$(mktemp -d)"
operator_home="$(mktemp -d)"
plain_home="$(mktemp -d)"
invalid_home="$(mktemp -d)"
tmp_home="$(mktemp -d)"
trap 'rm -rf "$skill_home" "$operator_home" "$plain_home" "$invalid_home" "$tmp_home"' EXIT
mkdir -p "$skill_home/.codex/agents" "$operator_home/.codex/agents" "$plain_home/.codex/agents" "$tmp_home/.agents/skills/manage-local-github-runners"

# A copied installer must discover a new canonical skill without a code change.
fixture_root="$tmp_home/fixture-repo"
mkdir -p "$fixture_root/scripts/setup" "$fixture_root/skills/runnerops-extra-contract" "$fixture_root/agents/runnerops-operator"
cp "$ROOT/scripts/setup/install-agent-skills.sh" "$fixture_root/scripts/setup/"
cp "$OPERATOR" "$fixture_root/agents/runnerops-operator/AGENT.md"
cat > "$fixture_root/skills/runnerops-extra-contract/SKILL.md" <<'EOF'
---
name: runnerops-extra-contract
description: RunnerOps discovery contract fixture.
---
Use runnerctl.
EOF
fixture_list="$("$fixture_root/scripts/setup/install-agent-skills.sh" --list)"
grep -Fxq runnerops-extra-contract <<< "$fixture_list" || fail "installer deve descobrir skill canônica adicional"
HOME="$tmp_home/discovery-home" "$fixture_root/scripts/setup/install-agent-skills.sh" --tool codex --skill runnerops-extra-contract >/dev/null
cmp -s "$fixture_root/skills/runnerops-extra-contract/SKILL.md" "$tmp_home/discovery-home/.codex/skills/runnerops-extra-contract/SKILL.md" || fail "--skill deve aceitar nova skill canônica sem lista hardcoded"
[[ ! -e "$tmp_home/discovery-home/.codex/agents" ]] || fail "nova skill não deve instalar o operator"
pass "Nova skill canônica descoberta e aceita por --skill sem cadastro manual"

HOME="$skill_home" "$ROOT/scripts/setup/install-agent-skills.sh" --tool codex --skill runnerops-manage-runners >/dev/null
[[ -d "$skill_home/.codex/skills/runnerops-manage-runners" ]] || fail "--skill runnerops-manage-runners deve instalar somente a skill selecionada"
[[ ! -e "$skill_home/.codex/agents/runnerops-operator.toml" ]] || fail "--skill runnerops-manage-runners não deve instalar o operator"

HOME="$operator_home" "$ROOT/scripts/setup/install-agent-skills.sh" --tool codex --skill runnerops-operator >/dev/null
[[ ! -d "$operator_home/.codex/skills/runnerops-manage-runners" ]] || fail "--skill runnerops-operator deve instalar apenas o operador"
[[ -f "$operator_home/.codex/agents/runnerops-operator.toml" ]] || fail "--skill runnerops-operator deve instalar a projeção do operator"

HOME="$plain_home" "$ROOT/scripts/setup/install-agent-skills.sh" --tool codex >/dev/null
[[ -d "$plain_home/.codex/skills/runnerops-manage-runners" ]] || fail "instalação sem --skill deve reinstalar skills e operador"
[[ -f "$plain_home/.codex/agents/runnerops-operator.toml" ]] || fail "instalação sem --skill deve incluir o operador"

if HOME="$invalid_home" "$ROOT/scripts/setup/install-agent-skills.sh" --tool all --skill nome-inexistente >"$tmp_home/invalid.out" 2>&1; then
  fail "--skill nome-inexistente deve falhar"
fi
require_text "$tmp_home/invalid.out" "skill/agent invalido: nome-inexistente" "target inválido deve falhar na validação"
[[ -z "$(find "$invalid_home" -mindepth 1 -print -quit)" ]] || fail "--skill inválida não deve criar nenhum arquivo ou diretório"
pass "Target inválido rejeitado antes de qualquer side effect"

printf '%s\n' legacy > "$tmp_home/.agents/skills/manage-local-github-runners/marker"
HOME="$tmp_home" "$ROOT/scripts/setup/install-agent-skills.sh" --tool agents --skill runnerops-manage-runners >/dev/null
[[ ! -e "$tmp_home/.agents/skills/manage-local-github-runners" ]] || fail "installer deve remover nome legado correspondente"
[[ -f "$tmp_home/.agents/skills/runnerops-manage-runners/SKILL.md" ]] || fail "installer deve instalar nome canônico novo"

for tool in codex copilot claude agents; do
  case "$tool" in
    codex) installed_path="$tmp_home/.codex/agents/runnerops-operator.toml" ;;
    copilot) installed_path="$tmp_home/.copilot/agents/runnerops-operator.agent.md" ;;
    claude) installed_path="$tmp_home/.claude/agents/runnerops-operator.md" ;;
    agents) installed_path="$tmp_home/.agents/agents/runnerops-operator.md" ;;
  esac

  HOME="$tmp_home" "$ROOT/scripts/setup/install-agent-skills.sh" --tool "$tool" >/dev/null
  [[ -f "$installed_path" ]] || fail "installer deve projetar o operador global em $tool"
  [[ ! -e "$tmp_home/.codex/agents/runnerops-operator/AGENT.md" ]] || fail "installer não deve manter projeção legacy do operador no Codex"
  [[ ! -e "$tmp_home/.copilot/agents/runnerops-operator/AGENT.md" ]] || fail "installer não deve manter projeção legacy do operador no Copilot"
  [[ ! -e "$tmp_home/.claude/agents/runnerops-operator/AGENT.md" ]] || fail "installer não deve manter projeção legacy do operador no Claude"
  HOME="$tmp_home" "$ROOT/scripts/setup/install-agent-skills.sh" --tool "$tool" >/dev/null
  [[ -f "$installed_path" ]] || fail "installer deve ser idempotente para $tool"
done

# Exercise escapes through the real renderer, changing only a temporary source.
python3 - "$fixture_root/agents/runnerops-operator/AGENT.md" <<'PY'
import sys
from pathlib import Path

source = Path(sys.argv[1])
with source.open('a', encoding='utf-8') as fh:
    fh.write('\nEscapes: "quoted", backslash \\, tab\t, newline\n, return\r, backspace\b, formfeed\f, control\x01, unicode ç.\n')
PY
HOME="$tmp_home/escaped-home" "$fixture_root/scripts/setup/install-agent-skills.sh" --tool codex --skill runnerops-operator >/dev/null

python3 - "$tmp_home/.codex/agents/runnerops-operator.toml" "$tmp_home/escaped-home/.codex/agents/runnerops-operator.toml" "$fixture_root/agents/runnerops-operator/AGENT.md" <<'PY'
import json
import re
import sys
from pathlib import Path

# This is only the renderer's single-line basic-string subset, not a TOML parser.
# Check TOML-compatible escapes before decoding the shared JSON string syntax.
# All modules and APIs used here are available in Python 3.8.
basic_string = re.compile(
    r'"(?:[^"\\\x00-\x1f\x7f]|\\["\\bfnrt]|'
    r'\\u(?:[0-9a-cA-Ce-fE-F][0-9a-fA-F]{3}|[dD][0-7][0-9a-fA-F]{2}))*"'
)


def decode_string(value):
    assert basic_string.fullmatch(value), value
    return json.loads(value)


def read_projection(path):
    data = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        key, separator, value = line.partition(' = ')
        assert separator and key not in data, line
        data[key] = decode_string(value)
    assert set(data) == {'name', 'description', 'developer_instructions'}, data
    return data


for invalid in ('"bad\\/escape"', '"bad\\x01"', '"bad\\uD800"', '"bad\\uD83D\\uDE00"', '"raw\tcontrol"', '"raw\x7fcontrol"', '"unescaped"quote"'):
    try:
        decode_string(invalid)
    except (AssertionError, ValueError):
        pass
    else:
        raise AssertionError('Accepted invalid basic string: ' + repr(invalid))

data = read_projection(sys.argv[1])
assert data['name'] == 'runnerops-operator', data
assert 'RunnerOps' in data['description'], data['description']
body = data['developer_instructions']
assert 'runnerctl' in body, body
assert 'runnerops-manage-runners' in body, body
assert 'multi-repo' in body.lower() or 'multi repository' in body.lower(), body
assert 'planner' in body.lower() and 'controller' in body.lower(), body
assert 'Repository iteration is an agent-side orchestration pattern, not a RunnerOps core runtime feature.' in body
assert 'Never replace planner/controller decisions with ad-hoc heuristics.' in body

escaped = read_projection(sys.argv[2])
source = Path(sys.argv[3]).read_text(encoding='utf-8')
expected_body = re.match(r'^---\n.*?\n---\n(.*)$', source, re.S).group(1).strip()
assert escaped['name'] == data['name']
assert escaped['description'] == data['description']
assert escaped['developer_instructions'] == expected_body
for escape in ('\\"', '\\\\', '\\t', '\\n', '\\b', '\\f', '\\u0001', '\\u00e7'):
    assert escape in Path(sys.argv[2]).read_text(encoding='utf-8'), escape
PY
pass "Projeção Codex e escapes validados com stdlib compatível com Python 3.8"

for tool in copilot claude agents; do
  case "$tool" in
    copilot) installed_path="$tmp_home/.copilot/agents/runnerops-operator.agent.md" ;;
    claude) installed_path="$tmp_home/.claude/agents/runnerops-operator.md" ;;
    agents) installed_path="$tmp_home/.agents/agents/runnerops-operator.md" ;;
  esac
  grep -Eq '^name: runnerops-operator$' "$installed_path" || fail "projeção de $tool deve conter name canônico"
  grep -Eq '^description: .*RunnerOps.*' "$installed_path" || fail "projeção de $tool deve conter description canônica"
done

[[ "$(find "$tmp_home/.codex" "$tmp_home/.copilot" "$tmp_home/.claude" "$tmp_home/.agents" -type f \( -name 'runnerops-operator.toml' -o -name 'runnerops-operator.agent.md' -o -name 'runnerops-operator.md' \) | wc -l)" -eq 4 ]] || fail "installer deve manter uma projeção ativa por provider suportado"

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
