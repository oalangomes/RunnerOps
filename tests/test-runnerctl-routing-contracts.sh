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

assert_eq() {
  local expected="$1" actual="$2" message="$3"
  [[ "$actual" == "$expected" ]] || {
    printf 'expected: %s\nactual:   %s\n' "$expected" "$actual" >&2
    fail "$message"
  }
}

make_fake_platform() {
  local platform="$1"
  mkdir -p "$platform"
  cp "$ROOT/runner-runtime-env.sh" "$platform/runner-runtime-env.sh"

  cat > "$platform/runners.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'runner:%s\n' "$*" >> "${TEST_CALL_LOG:?}"
EOF

  cat > "$platform/runner-services.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'service:%s\n' "$*" >> "${TEST_CALL_LOG:?}"
EOF

  chmod +x "$platform/runners.sh" "$platform/runner-services.sh"
}

run_ctl() {
  local platform="$1" log="$2"
  shift 2
  TEST_CALL_LOG="$log" \
    ACTIONS_RUNNERS_HOME="$platform" \
    XDG_CONFIG_HOME="$TMP_ROOT/config" \
    "$ROOT/runnerctl" "$@"
}

test_exact_runner_and_group_routing() {
  local platform="$TMP_ROOT/routing-platform"
  local log="$TMP_ROOT/routing.log"

  make_fake_platform "$platform"
  : > "$log"

  run_ctl "$platform" "$log" status alpha
  run_ctl "$platform" "$log" start group:backend
  run_ctl "$platform" "$log" restart alpha
  run_ctl "$platform" "$log" logs alpha

  assert_eq \
    $'runner:status alpha\nrunner:start group:backend\nrunner:restart alpha\nrunner:logs alpha' \
    "$(cat "$log")" \
    "runnerctl deve preservar targets exatos e grupos no boundary público"

  pass "targets exatos e group:* são encaminhados sem expansão indevida"
}

test_boot_policy_routing() {
  local platform="$TMP_ROOT/policy-platform"
  local log="$TMP_ROOT/policy.log"

  make_fake_platform "$platform"
  : > "$log"

  run_ctl "$platform" "$log" on-demand alpha
  run_ctl "$platform" "$log" on-demand group:backend
  run_ctl "$platform" "$log" autostart alpha

  assert_eq \
    $'service:on-demand alpha\nservice:on-demand group:backend\nservice:autostart alpha' \
    "$(cat "$log")" \
    "runnerctl deve separar on-demand/autostart e manter o target"

  pass "on-demand e autostart preservam ação e target"
}

test_default_targets_are_explicit() {
  local platform="$TMP_ROOT/default-platform"
  local log="$TMP_ROOT/default.log"

  make_fake_platform "$platform"
  : > "$log"

  run_ctl "$platform" "$log" status
  run_ctl "$platform" "$log" health
  run_ctl "$platform" "$log" plan

  assert_eq \
    $'runner:status all\nrunner:health all\nservice:plan all' \
    "$(cat "$log")" \
    "defaults públicos devem continuar explícitos como all"

  pass "defaults de status/health/plan continuam estáveis"
}

main() {
  test_exact_runner_and_group_routing
  test_boot_policy_routing
  test_default_targets_are_explicit
  printf '\nContratos de routing/lifecycle passaram.\n'
}

main "$@"
