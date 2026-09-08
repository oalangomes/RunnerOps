#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() {
  printf '[FAIL] %s\n' "$1" >&2
  exit 1
}

pass() {
  printf '[PASS] %s\n' "$1"
}

assert_contains() {
  local haystack="$1" needle="$2" message="$3"
  [[ "$haystack" == *"$needle"* ]] || {
    printf 'missing: %s\noutput:\n%s\n' "$needle" "$haystack" >&2
    fail "$message"
  }
}

assert_not_contains() {
  local haystack="$1" needle="$2" message="$3"
  [[ "$haystack" != *"$needle"* ]] || {
    printf 'unexpected: %s\noutput:\n%s\n' "$needle" "$haystack" >&2
    fail "$message"
  }
}

make_fake_systemctl() {
  local bin="$1"
  mkdir -p "$bin"
  cat > "$bin/systemctl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

mode="${FAKE_SYSTEMCTL_MODE:-idle}"
action="${1:-}"

case "$action" in
  show)
    printf '%s\n' loaded
    ;;
  is-active)
    case "$mode" in
      query-error) exit 1 ;;
      idle) printf '%s\n' inactive; exit 3 ;;
      active) printf '%s\n' active ;;
      failed) printf '%s\n' failed; exit 3 ;;
      *) printf '%s\n' "$mode"; exit 3 ;;
    esac
    ;;
  is-enabled)
    case "$mode" in
      query-error) printf '%s\n' disabled; exit 1 ;;
      *) printf '%s\n' disabled; exit 1 ;;
    esac
    ;;
  start|stop)
    exit 0
    ;;
  list-unit-files)
    exit 0
    ;;
  *)
    exit 1
    ;;
esac
EOF
  chmod +x "$bin/systemctl"
}

make_systemd_fixture() {
  local name="$1"
  local runner_dir="$TMP_ROOT/$name-runner"
  local registry="$TMP_ROOT/$name-runners.conf"
  mkdir -p "$runner_dir" "$TMP_ROOT/systemd-runtime"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$runner_dir/run.sh"
  chmod +x "$runner_dir/run.sh"
  printf '%s\n' "actions.runner.example.$name.service" > "$runner_dir/.service"
  printf '%s\n' '{"agentId":1,"agentName":"example"}' > "$runner_dir/.runner"
  printf '%s\n' '# name|path|profile|repo|enabled|group' > "$registry"
  printf '%s|%s|generic|example/project|true|example\n' "$name" "$runner_dir" >> "$registry"
  printf '%s|%s\n' "$runner_dir" "$registry"
}

run_systemd_action() {
  local mode="$1" registry="$2"
  shift 2
  FAKE_SYSTEMCTL_MODE="$mode" \
    PATH="$TMP_ROOT/bin:$PATH" \
    ACTIONS_RUNNERS_ENV="$TMP_ROOT/missing.env" \
    RUNNERS_CONFIG="$registry" \
    RUNNER_STATE_ROOT="$TMP_ROOT/state" \
    RUNNER_CACHE_ROOT="$TMP_ROOT/cache" \
    RUNNER_SYSTEMD_RUNTIME_DIR="$TMP_ROOT/systemd-runtime" \
    RUNNER_BOOT_POLICY=on-demand \
    "$ROOT/runners.sh" "$@"
}

test_query_failure_is_not_healthy() {
  local fixture runner_dir registry status_output health_output doctor_output doctor_rc=0
  fixture="$(make_systemd_fixture queryfail)"
  IFS='|' read -r runner_dir registry <<< "$fixture"

  status_output="$(run_systemd_action query-error "$registry" status queryfail 2>&1)"
  assert_contains "$status_output" "[WARN]" "status deve sinalizar observação desconhecida"
  assert_contains "$status_output" "state=unknown" "status deve usar state=unknown"
  assert_contains "$status_output" "observation=query-error" "status deve explicar falha de observação"
  assert_not_contains "$status_output" "[STOP]" "falha de consulta não pode virar STOP"
  assert_not_contains "$status_output" "[IDLE]" "falha de consulta não pode virar IDLE"

  health_output="$(run_systemd_action query-error "$registry" health queryfail 2>&1)"
  assert_contains "$health_output" "[WARN]" "health deve sinalizar observação desconhecida"
  assert_contains "$health_output" "state=unknown" "health deve usar state=unknown"
  assert_not_contains "$health_output" "[OK]" "falha de consulta não pode virar health OK"
  assert_not_contains "$health_output" "idle=true" "falha de consulta não pode virar idle=true"

  set +e
  doctor_output="$(run_systemd_action query-error "$registry" doctor queryfail 2>&1)"
  doctor_rc=$?
  set -e
  [[ "$doctor_rc" -ne 0 ]] || fail "doctor deve retornar non-zero quando lifecycle não é observável"
  assert_contains "$doctor_output" "[ERR] backend=systemd" "doctor deve tornar query failure explícita"
  assert_contains "$doctor_output" "state=unknown" "doctor deve usar state=unknown"

  pass "falha de consulta systemd permanece UNKNOWN/WARN e doctor falha"
}

test_inactive_on_demand_remains_healthy_idle() {
  local fixture runner_dir registry status_output health_output doctor_output
  fixture="$(make_systemd_fixture idleok)"
  IFS='|' read -r runner_dir registry <<< "$fixture"

  status_output="$(run_systemd_action idle "$registry" status idleok)"
  assert_contains "$status_output" "[IDLE]" "inactive on-demand deve continuar IDLE"
  assert_contains "$status_output" "state=inactive" "status deve preservar inactive"
  assert_contains "$status_output" "boot=disabled" "status deve preservar boot disabled"

  health_output="$(run_systemd_action idle "$registry" health idleok)"
  assert_contains "$health_output" "[OK]" "inactive on-demand deve continuar saudável"
  assert_contains "$health_output" "idle=true" "inactive on-demand deve continuar idle=true"

  doctor_output="$(run_systemd_action idle "$registry" doctor idleok)"
  assert_contains "$doctor_output" "[OK] backend=systemd" "doctor deve aceitar observação válida"
  assert_contains "$doctor_output" "state=inactive" "doctor deve reportar inactive"

  pass "inactive + boot disabled preserva semântica saudável on-demand"
}

test_legacy_start_failure_cleans_stale_pid() {
  local runner_dir="$TMP_ROOT/dead-runner"
  local registry="$TMP_ROOT/dead-runners.conf"
  local output rc=0 pid_file="$TMP_ROOT/legacy-state/legacy-pids/dead.pid"

  mkdir -p "$runner_dir"
  cat > "$runner_dir/run.sh" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$runner_dir/run.sh"
  printf '%s\n' '{"agentId":2,"agentName":"dead"}' > "$runner_dir/.runner"
  printf '%s\n' '# name|path|profile|repo|enabled|group' > "$registry"
  printf 'dead|%s|generic|example/project|true|example\n' "$runner_dir" >> "$registry"

  set +e
  output="$(
    PATH="$TMP_ROOT/bin:$PATH" \
      ACTIONS_RUNNERS_ENV="$TMP_ROOT/missing.env" \
      RUNNERS_CONFIG="$registry" \
      RUNNER_STATE_ROOT="$TMP_ROOT/legacy-state" \
      RUNNER_CACHE_ROOT="$TMP_ROOT/legacy-cache" \
      RUNNER_SYSTEMD_RUNTIME_DIR="$TMP_ROOT/no-systemd" \
      RUNNER_LEGACY_START_SETTLE_SECONDS=0.2 \
      "$ROOT/runners.sh" start dead 2>&1
  )"
  rc=$?
  set -e

  [[ "$rc" -ne 0 ]] || fail "legacy start deve falhar se processo morrer durante settle"
  assert_contains "$output" "[ERR] dead nao permaneceu ativo backend=legacy" "falha de ativação deve ser explícita"
  [[ ! -e "$pid_file" ]] || fail "PID stale deve ser removido após falha de start"

  pass "legacy start valida settle e remove PID stale ao falhar"
}

main() {
  make_fake_systemctl "$TMP_ROOT/bin"
  test_query_failure_is_not_healthy
  test_inactive_on_demand_remains_healthy_idle
  test_legacy_start_failure_cleans_stale_pid
  printf '\nContratos de lifecycle truthfulness passaram.\n'
}

main "$@"
