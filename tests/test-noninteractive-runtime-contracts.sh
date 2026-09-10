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
"$@"
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

main() {
  test_active_ensure_is_noop_without_sudo
  test_inactive_authorized_uses_noninteractive_helper
  test_inactive_without_authorization_fails_fast
  test_unknown_lifecycle_never_mutates
  test_privileged_helper_rejects_scope_escape
  printf '\nTodos os contratos de runtime nao-interativo passaram.\n'
}

main "$@"
