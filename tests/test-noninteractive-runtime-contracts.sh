#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

pass() {
  printf '[PASS] %s\n' "$1"
}

fail() {
  printf '[FAIL] %s\n' "$1" >&2
  exit 1
}

assert_contains() {
  local haystack="$1" needle="$2" message="$3"
  [[ "$haystack" == *"$needle"* ]] || {
    printf 'missing: %s\noutput:\n%s\n' "$needle" "$haystack" >&2
    fail "$message"
  }
}

make_fixture() {
  local name="$1"
  local state="$2"
  local dir="$TMP_ROOT/$name"
  local runner_dir="$dir/runner"
  local bin="$dir/bin"

  mkdir -p "$runner_dir" "$bin" "$dir/systemd"
  printf '%s\n' 'actions.runner.example-project.ci-a.service' > "$runner_dir/.service"
  printf '%s\n' "$state" > "$dir/state"
  : > "$dir/sudo.log"
  : > "$dir/mutation.log"

  cat > "$dir/runners.conf" <<EOF
# name|path|profile|repo|enabled|group
ci-a|$runner_dir|generic|example/project|true|example
EOF

  cat > "$bin/systemctl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cmd="${1:-}"
shift || true

case "$cmd" in
  show)
    if [[ "${TEST_SYSTEMD_UNKNOWN:-0}" == "1" ]]; then
      exit 1
    fi
    printf '%s\n' loaded
    ;;
  is-active)
    quiet=0
    if [[ "${1:-}" == "--quiet" ]]; then
      quiet=1
      shift
    fi
    state="$(cat "${TEST_STATE_FILE:?}")"
    if [[ "$quiet" -eq 0 ]]; then
      printf '%s\n' "$state"
    fi
    [[ "$state" == "active" ]]
    ;;
  is-enabled)
    printf '%s\n' disabled
    exit 1
    ;;
  start)
    printf 'start %s\n' "${1:-}" >> "${TEST_MUTATION_LOG:?}"
    printf '%s\n' active > "${TEST_STATE_FILE:?}"
    ;;
  stop)
    printf 'stop %s\n' "${1:-}" >> "${TEST_MUTATION_LOG:?}"
    printf '%s\n' inactive > "${TEST_STATE_FILE:?}"
    ;;
  restart)
    printf 'restart %s\n' "${1:-}" >> "${TEST_MUTATION_LOG:?}"
    printf '%s\n' active > "${TEST_STATE_FILE:?}"
    ;;
  *)
    printf 'unexpected systemctl command: %s %s\n' "$cmd" "$*" >&2
    exit 70
    ;;
esac
EOF

  cat > "$bin/sudo" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' "$*" >> "${TEST_SUDO_LOG:?}"

[[ "${1:-}" == "-n" ]] || {
  echo "interactive sudo invocation forbidden in runtime contract" >&2
  exit 91
}
shift

[[ "${1:-}" == "${RUNNEROPS_SYSTEMCTL_HELPER:?}" ]] || exit 1

if [[ "${2:-}" == "check" ]]; then
  [[ "${TEST_AUTHORIZED:-0}" == "1" ]]
  exit
fi

[[ "${TEST_AUTHORIZED:-0}" == "1" ]] || exit 1
helper="$1"
action="${2:-}"
unit="${3:-}"
[[ "$helper" == "${RUNNEROPS_SYSTEMCTL_HELPER:?}" ]] || exit 1
systemctl "$action" "$unit"
EOF

  chmod +x "$bin/systemctl" "$bin/sudo"
  cp "$ROOT/runnerops-systemctl" "$dir/runnerops-systemctl"
  chmod +x "$dir/runnerops-systemctl"

  printf '%s\n' "$dir"
}

run_ensure() {
  local dir="$1"
  shift

  PATH="$dir/bin:$PATH" \
  ACTIONS_RUNNERS_ENV="$dir/missing.env" \
  ACTIONS_RUNNERS_HOME="$ROOT" \
  RUNNERS_CONFIG="$dir/runners.conf" \
  RUNNER_STATE_ROOT="$dir/runtime-state" \
  RUNNER_CACHE_ROOT="$dir/cache" \
  RUNNER_SYSTEMD_RUNTIME_DIR="$dir/systemd" \
  RUNNER_SYSTEMD_START_SETTLE_SECONDS=0 \
  RUNNEROPS_SYSTEMCTL_HELPER="$dir/runnerops-systemctl" \
  TEST_STATE_FILE="$dir/state" \
  TEST_SUDO_LOG="$dir/sudo.log" \
  TEST_MUTATION_LOG="$dir/mutation.log" \
  "$@" \
  "$ROOT/runnerctl" ensure example/project
}

test_active_ensure_is_noop_without_sudo() {
  local dir output
  dir="$(make_fixture active-noop active)"

  output="$(TEST_AUTHORIZED=0 run_ensure "$dir" env)"

  assert_contains "$output" "Repo: example/project" "ensure deve manter escopo do repo"
  assert_contains "$output" "[OK] ci-a ja esta ativo backend=systemd" "runner ativo deve virar no-op"
  [[ ! -s "$dir/sudo.log" ]] || fail "runner ativo nao pode consultar/chamar sudo"
  [[ ! -s "$dir/mutation.log" ]] || fail "runner ativo nao pode chamar systemctl start"

  pass "ensure com runner ativo retorna sem sudo nem mutacao"
}

test_inactive_authorized_uses_noninteractive_helper() {
  local dir output
  dir="$(make_fixture inactive-authorized inactive)"

  output="$(TEST_AUTHORIZED=1 run_ensure "$dir" env)"

  assert_contains "$output" "[OK] ci-a iniciado backend=systemd" "runner inativo autorizado deve iniciar"
  [[ "$(cat "$dir/state")" == "active" ]] || fail "fixture deveria terminar ativa"
  grep -F -- "-n $dir/runnerops-systemctl check" "$dir/sudo.log" >/dev/null ||
    fail "runtime deve verificar autorizacao via sudo -n"
  grep -F -- "-n $dir/runnerops-systemctl start actions.runner.example-project.ci-a.service" "$dir/sudo.log" >/dev/null ||
    fail "runtime deve usar helper autorizado com sudo -n"
  if grep -Ev '^-n ' "$dir/sudo.log" | grep -q .; then
    fail "runtime jamais pode abrir sudo interativo"
  fi

  pass "runner inativo usa helper autorizado sem prompt"
}

test_inactive_without_authorization_fails_fast() {
  local dir output rc
  dir="$(make_fixture inactive-unauthorized inactive)"

  set +e
  output="$(TEST_AUTHORIZED=0 run_ensure "$dir" env 2>&1)"
  rc=$?
  set -e

  [[ "$rc" -ne 0 ]] || fail "ensure deve falhar quando mutacao exige autorizacao ausente"
  assert_contains "$output" "runnerctl platform-authorize" "erro deve orientar autorizacao one-time"
  [[ ! -s "$dir/mutation.log" ]] || fail "sem autorizacao nao pode haver mutacao"
  if grep -Ev '^-n ' "$dir/sudo.log" | grep -q .; then
    fail "falha de autorizacao nao pode cair em sudo interativo"
  fi

  pass "autorizacao ausente falha rapido sem prompt"
}

test_unknown_lifecycle_never_mutates() {
  local dir output rc
  dir="$(make_fixture unknown-state inactive)"

  set +e
  output="$(TEST_AUTHORIZED=1 TEST_SYSTEMD_UNKNOWN=1 run_ensure "$dir" env 2>&1)"
  rc=$?
  set -e

  [[ "$rc" -ne 0 ]] || fail "lifecycle unknown deve falhar"
  assert_contains "$output" "observation=query-error" "unknown deve ser explicito"
  assert_contains "$output" "refusing start without trustworthy lifecycle evidence" "unknown deve falhar safe"
  [[ ! -s "$dir/sudo.log" ]] || fail "unknown nao pode tentar autorizacao/mutacao"
  [[ ! -s "$dir/mutation.log" ]] || fail "unknown nao pode mutar"

  pass "lifecycle unknown falha safe antes de sudo"
}

test_privileged_helper_rejects_scope_escape() {
  if bash "$ROOT/runnerops-systemctl" reload actions.runner.example.ci.service >/dev/null 2>&1; then
    fail "helper deve rejeitar verbo fora do contrato"
  fi

  if bash "$ROOT/runnerops-systemctl" start ssh.service >/dev/null 2>&1; then
    fail "helper deve rejeitar unit fora do namespace actions.runner"
  fi

  if bash "$ROOT/runnerops-systemctl" start 'actions.runner.example.service --no-block' >/dev/null 2>&1; then
    fail "helper deve rejeitar argumentos injetados no nome da unit"
  fi

  pass "helper privilegiado restringe verbo e namespace da unit"
}

test_authorized_template_migration_never_executes_user_script_as_root() {
  local dir="$TMP_ROOT/template-migrate"
  local platform="$dir/platform"
  local runner_root="$dir/data"
  local runner_dir="$runner_root/ci-a"
  local bin="$dir/bin"
  local helper="$dir/runnerops-systemctl"
  local expected_unit="actions.runner.runnerops-$(id -un)@ci-a.service"
  local output

  mkdir -p "$platform" "$runner_dir" "$bin" "$dir/systemd" "$dir/state-root"
  cp "$ROOT/runner-services.sh" "$platform/runner-services.sh"
  cp "$ROOT/runner-runtime-env.sh" "$platform/runner-runtime-env.sh"
  cp "$ROOT/runner-cache-env.sh" "$platform/runner-cache-env.sh"
  chmod +x "$platform/runner-services.sh"

  printf '%s\n' '{"agentId":42,"agentName":"host-ci-a"}' > "$runner_dir/.runner"
  cat > "$runner_dir/svc.sh" <<'EOF'
#!/usr/bin/env bash
printf 'svc-root-path-called %s\n' "$*" >> "${TEST_SVC_LOG:?}"
exit 92
EOF
  cat > "$runner_dir/runsvc.sh" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$runner_dir/svc.sh" "$runner_dir/runsvc.sh"
  : > "$dir/svc.log"
  : > "$dir/sudo.log"
  : > "$dir/mutation.log"
  printf '%s\n' inactive > "$dir/state"

  cat > "$dir/runners.conf" <<EOF
# name|path|profile|repo|enabled|group
ci-a|$runner_dir|python|example/project|true|example
EOF

  cat > "$bin/systemctl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cmd="${1:-}"
shift || true
case "$cmd" in
  cat)
    [[ "${1:-}" == actions.runner.runnerops-*'@.service' ]]
    ;;
  list-unit-files)
    exit 0
    ;;
  show)
    exit 1
    ;;
  is-active)
    quiet=0
    if [[ "${1:-}" == "--quiet" ]]; then quiet=1; shift; fi
    state="$(cat "${TEST_STATE_FILE:?}")"
    [[ "$quiet" -eq 1 ]] || printf '%s\n' "$state"
    [[ "$state" == active ]]
    ;;
  is-enabled)
    printf '%s\n' disabled
    exit 1
    ;;
  start)
    printf 'start %s\n' "${1:-}" >> "${TEST_MUTATION_LOG:?}"
    printf '%s\n' active > "${TEST_STATE_FILE:?}"
    ;;
  stop)
    printf 'stop %s\n' "${1:-}" >> "${TEST_MUTATION_LOG:?}"
    printf '%s\n' inactive > "${TEST_STATE_FILE:?}"
    ;;
  enable|disable)
    printf '%s %s\n' "$cmd" "${1:-}" >> "${TEST_MUTATION_LOG:?}"
    ;;
  status)
    exit 0
    ;;
  *)
    printf 'unexpected systemctl command: %s %s\n' "$cmd" "$*" >&2
    exit 70
    ;;
esac
EOF

  cat > "$bin/sudo" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${TEST_SUDO_LOG:?}"
[[ "${1:-}" == "-n" ]] || {
  echo "interactive/arbitrary sudo forbidden" >&2
  exit 91
}
shift
[[ "${1:-}" == "${RUNNEROPS_SYSTEMCTL_HELPER:?}" ]] || exit 92
shift
if [[ "${1:-}" == check ]]; then
  exit 0
fi
action="${1:-}"
unit="${2:-}"
systemctl "$action" "$unit"
EOF

  cat > "$helper" <<'EOF'
#!/usr/bin/env bash
exit 99
EOF
  chmod +x "$bin/systemctl" "$bin/sudo" "$helper"

  output="$(
    PATH="$bin:$PATH" \
    ACTIONS_RUNNERS_ENV="$dir/missing.env" \
    RUNNERS_CONFIG="$dir/runners.conf" \
    RUNNER_DATA_ROOT="$runner_root" \
    RUNNER_STATE_ROOT="$dir/state-root" \
    RUNNER_CACHE_ROOT="$dir/cache" \
    RUNNER_BOOT_POLICY=on-demand \
    RUNNER_SYSTEMD_START_SETTLE_SECONDS=0 \
    RUNNEROPS_SYSTEMCTL_HELPER="$helper" \
    TEST_STATE_FILE="$dir/state" \
    TEST_SUDO_LOG="$dir/sudo.log" \
    TEST_MUTATION_LOG="$dir/mutation.log" \
    TEST_SVC_LOG="$dir/svc.log" \
      "$platform/runner-services.sh" migrate ci-a
  )"

  assert_contains "$output" "template RunnerOps autorizado" "migrate deve usar template root-owned"
  assert_contains "$output" "policy=on-demand" "migrate deve terminar idle/on-demand"
  [[ "$(cat "$runner_dir/.service")" == "$expected_unit" ]] ||
    fail "runner deve persistir a unit template exata"
  [[ ! -s "$dir/svc.log" ]] || fail "svc.sh user-writable jamais pode ser executado como root no caminho autorizado"
  [[ "$(cat "$dir/state")" == inactive ]] || fail "runner provisionado deve terminar ocioso"
  grep -F -- "-n $helper check" "$dir/sudo.log" >/dev/null || fail "migrate deve provar autorizacao"
  grep -F -- "-n $helper start $expected_unit" "$dir/sudo.log" >/dev/null || fail "start deve usar helper limitado"
  grep -F -- "-n $helper stop $expected_unit" "$dir/sudo.log" >/dev/null || fail "stop deve usar helper limitado"
  if grep -Ev "^-n $helper (check|start|stop|enable|disable)( |$)" "$dir/sudo.log" | grep -q .; then
    cat "$dir/sudo.log" >&2
    fail "provisioning autorizado nao pode abrir sudo arbitrario"
  fi

  pass "provisioning autorizado usa unit template sem executar codigo user-writable como root"
}

main() {
  test_active_ensure_is_noop_without_sudo
  test_inactive_authorized_uses_noninteractive_helper
  test_inactive_without_authorization_fails_fast
  test_unknown_lifecycle_never_mutates
  test_privileged_helper_rejects_scope_escape
  test_authorized_template_migration_never_executes_user_script_as_root
  printf '\nTodos os contratos de runtime nao-interativo passaram.\n'
}

main "$@"
