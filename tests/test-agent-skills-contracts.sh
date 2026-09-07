#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRE="$ROOT/skills/start-project-runners-before-pr/SKILL.md"
MANAGE="$ROOT/skills/manage-local-github-runners/SKILL.md"

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

require_text "$PRE" "runnerctl ensure ." "skill pre-PR deve usar runnerctl ensure ."
require_text "$PRE" "runnerctl ci watch . --pr <number> --json" "skill pre-PR deve preferir watcher por PR"
require_text "$PRE" "runnerctl ci watch . --json" "skill pre-PR deve suportar watcher por HEAD"
require_text "$PRE" "`0`: CI completed successfully." "skill pre-PR deve documentar exit 0"
require_text "$PRE" "`1`: CI/workflow failed." "skill pre-PR deve documentar exit 1"
require_text "$PRE" "`2`: infrastructure/access failure." "skill pre-PR deve documentar exit 2"
require_text "$PRE" "`3`: timeout, cancellation or inconclusive state." "skill pre-PR deve documentar exit 3"
require_text "$PRE" "run_attempt" "skill pre-PR deve orientar reruns por run_attempt"
require_text "$PRE" "Do not restart/remove a runner because a test, lint or build step failed." "skill pre-PR deve preservar boundary CI != runner"

require_text "$MANAGE" "runnerctl ci watch . --pr <number> --json" "skill de gestão deve conhecer watcher por PR"
require_text "$MANAGE" "runnerctl ci watch owner/repo --sha <sha> --json" "skill de gestão deve conhecer watcher por SHA"
require_text "$MANAGE" "Never claim CI success from an inconclusive watcher result." "skill de gestão deve preservar semântica inconclusiva"

# Executable examples must stay on the public boundary. Mentions in prose such as
# "do not call systemctl" are allowed; direct command examples are not.
require_absent_command "$PRE" "runners\.sh" "skill pre-PR não pode executar runners.sh"
require_absent_command "$PRE" "runner-services\.sh" "skill pre-PR não pode executar runner-services.sh"
require_absent_command "$PRE" "systemctl" "skill pre-PR não pode executar systemctl diretamente"
require_absent_command "$MANAGE" "runners\.sh" "skill de gestão não pode executar runners.sh"
require_absent_command "$MANAGE" "runner-services\.sh" "skill de gestão não pode executar runner-services.sh"
require_absent_command "$MANAGE" "systemctl" "skill de gestão não pode executar systemctl diretamente"

pass "Agent Skills preservam runnerctl como boundary e consomem ci watch"
