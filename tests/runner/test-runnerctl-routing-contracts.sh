#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
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

make_fake_platform() {
  local platform="$1"
  mkdir -p "$platform/scripts/runner" "$platform/src/runnerops/autoscale" "$platform/src/runnerops/operational"
  cp "$ROOT/runnerctl" "$platform/runnerctl"
  cp "$ROOT/scripts/runner/runtime-env.sh" "$platform/scripts/runner/runtime-env.sh"
  touch "$platform/src/runnerops/__init__.py" "$platform/src/runnerops/autoscale/__init__.py" "$platform/src/runnerops/operational/__init__.py"

  cat > "$platform/scripts/runner/lifecycle.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'runner:%s\n' "$*" >> "${TEST_CALL_LOG:?}"
EOF

  cat > "$platform/scripts/runner/services.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'service:%s\n' "$*" >> "${TEST_CALL_LOG:?}"
EOF

  cat > "$platform/src/runnerops/autoscale/planner.py" <<'EOF'
#!/usr/bin/env python3
import os
import sys
with open(os.environ["TEST_CALL_LOG"], "a", encoding="utf-8") as log:
    log.write("autoscale-plan:" + " ".join(sys.argv[1:]) + "\n")
EOF

  cat > "$platform/src/runnerops/autoscale/controller.py" <<'EOF'
#!/usr/bin/env python3
import os
import sys
with open(os.environ["TEST_CALL_LOG"], "a", encoding="utf-8") as log:
    log.write("autoscale-run-once:" + " ".join(sys.argv[1:]) + "\n")
EOF

  cat > "$platform/src/runnerops/autoscale/scheduler.py" <<'EOF'
#!/usr/bin/env python3
import os
import sys
with open(os.environ["TEST_CALL_LOG"], "a", encoding="utf-8") as log:
    log.write("autoscale-scheduler:" + " ".join(sys.argv[1:]) + "\n")
EOF

  cat > "$platform/src/runnerops/operational/review.py" <<'EOF'
#!/usr/bin/env python3
import os
import sys
with open(os.environ["TEST_CALL_LOG"], "a", encoding="utf-8") as log:
    log.write("review:" + " ".join(sys.argv[1:]) + "\n")
EOF

  chmod +x "$platform/runnerctl" "$platform/scripts/runner/lifecycle.sh" "$platform/scripts/runner/services.sh"
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
  run_ctl "$platform" "$log" logs alpha --lines 50 --since 10m

  assert_eq \
    $'runner:status alpha\nrunner:start group:backend\nrunner:restart alpha\nrunner:logs alpha' \
    "$(cat "$log")" \
    "runnerctl deve preservar targets exatos e grupos no boundary público"

  pass "targets exatos e group:* são encaminhados sem expansão indevida"
}

test_logs_follow_requires_exact_runner() {
  local platform="$TMP_ROOT/log-follow-platform"
  local log="$TMP_ROOT/log-follow.log"
  local output

  make_fake_platform "$platform"
  : > "$log"

  run_ctl "$platform" "$log" logs alpha --follow
  assert_eq \
    'runner:logs alpha' \
    "$(cat "$log")" \
    "logs --follow deve encaminhar runner exato ao backend"

  : > "$log"
  if output="$(run_ctl "$platform" "$log" logs group:backend --follow 2>&1)"; then
    fail "logs group:* --follow deve ser rejeitado"
  fi
  assert_contains "$output" "--follow exige um runner exato" "group:* --follow deve falhar com razão explícita"
  assert_eq "" "$(cat "$log")" "group:* --follow deve falhar antes do backend"

  : > "$log"
  if output="$(run_ctl "$platform" "$log" logs all --follow 2>&1)"; then
    fail "logs all --follow deve ser rejeitado"
  fi
  assert_contains "$output" "--follow exige um runner exato" "all --follow deve falhar com razão explícita"
  assert_eq "" "$(cat "$log")" "all --follow deve falhar antes do backend"

  pass "logs --follow exige runner exato e não tenta multiplexar group/all"
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

test_lifecycle_mutation_summaries() {
  local platform="$TMP_ROOT/lifecycle-summary-platform"
  local log="$TMP_ROOT/lifecycle-summary.log"
  local output

  make_fake_platform "$platform"
  : > "$log"

  output="$(run_ctl "$platform" "$log" start alpha)"
  assert_contains "$output" "[SUMMARY] status=success operation=start target=alpha" "start deve fechar com resumo de sucesso"
  assert_contains "$output" "[NEXT] runnerctl status alpha && runnerctl health alpha" "start deve orientar verificação limitada"

  output="$(run_ctl "$platform" "$log" restart alpha)"
  assert_contains "$output" "[SUMMARY] status=success operation=restart target=alpha" "restart deve fechar com resumo de sucesso"
  assert_contains "$output" "[NEXT] runnerctl status alpha && runnerctl health alpha" "restart deve orientar verificação limitada"

  assert_eq \
    $'runner:start alpha\nrunner:restart alpha' \
    "$(cat "$log")" \
    "summaries não podem trocar o boundary público de lifecycle"

  pass "start/restart preservam boundary e emitem resumo acionável"
}

test_failed_lifecycle_mutation_summary_and_next_are_suppressed() {
  local platform="$TMP_ROOT/lifecycle-failure-platform"
  local log="$TMP_ROOT/lifecycle-failure.log"
  local output

  make_fake_platform "$platform"
  cat > "$platform/scripts/runner/lifecycle.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'ERRO: runner desconhecido: runnerops\n' >&2
exit 1
EOF
  chmod +x "$platform/scripts/runner/lifecycle.sh"
  : > "$log"

  if output="$(run_ctl "$platform" "$log" start runnerops 2>&1)"; then
    fail "start de runner desconhecido deve falhar"
  fi

  assert_contains "$output" "ERRO: runner desconhecido: runnerops" "falha operacional deve preservar a mensagem de erro"
  assert_contains "$output" "[SUMMARY] status=failure operation=start target=runnerops" "resumo deve refletir falha operacional"
  assert_not_contains "$output" "[SUMMARY] status=success" "resumo não deve afirmar success após falha"
  assert_not_contains "$output" "[NEXT]" "next action deve ser suprimida após falha"

  pass "falhas de lifecycle propagam status sem resumo de sucesso ou next action"
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

test_autoscale_plan_routes_to_read_only_planner() {
  local platform="$TMP_ROOT/autoscale-platform"
  local log="$TMP_ROOT/autoscale.log"

  make_fake_platform "$platform"
  : > "$log"

  run_ctl "$platform" "$log" autoscale plan example/repo --json

  assert_eq \
    'autoscale-plan:example/repo --json' \
    "$(cat "$log")" \
    "runnerctl autoscale plan deve encaminhar somente ao planner read-only"

  pass "autoscale plan preserva repo/flags e usa boundary próprio"
}

test_autoscale_run_once_routes_to_governed_controller() {
  local platform="$TMP_ROOT/autoscale-controller-platform"
  local log="$TMP_ROOT/autoscale-controller.log"
  make_fake_platform "$platform"
  : > "$log"
  run_ctl "$platform" "$log" autoscale run-once example/repo --json
  assert_eq \
    'autoscale-run-once:example/repo --json' \
    "$(cat "$log")" \
    "runnerctl autoscale run-once deve encaminhar somente ao controller governado"
  pass "autoscale run-once preserva repo/flags e usa boundary mutável isolado"
}

test_autoscale_scheduler_routes_to_scheduler_boundary() {
  local platform="$TMP_ROOT/autoscale-scheduler-platform"
  local log="$TMP_ROOT/autoscale-scheduler.log"
  make_fake_platform "$platform"
  : > "$log"

  run_ctl "$platform" "$log" autoscale enable example/repo
  run_ctl "$platform" "$log" autoscale status example/repo --json
  run_ctl "$platform" "$log" autoscale disable example/repo

  assert_eq \
    $'autoscale-scheduler:enable example/repo\nautoscale-scheduler:status example/repo --json\nautoscale-scheduler:disable example/repo' \
    "$(cat "$log")" \
    "autoscale enable/status/disable devem usar apenas o boundary do scheduler"
  pass "autoscale scheduler preserva repo/flags e não toca lifecycle diretamente"
}

test_review_routes_to_read_only_review_boundary() {
  local platform="$TMP_ROOT/review-platform"
  local log="$TMP_ROOT/review.log"
  make_fake_platform "$platform"
  : > "$log"

  run_ctl "$platform" "$log" review --evidence evidence.json --provider ollama --model fixture --json

  assert_eq \
    'review:--evidence evidence.json --provider ollama --model fixture --json' \
    "$(cat "$log")" \
    "runnerctl review deve preservar argumentos e usar boundary AI read-only próprio"
  pass "review usa boundary próprio sem rotear para planner/controller/lifecycle"
}

main() {
  test_exact_runner_and_group_routing
  test_logs_follow_requires_exact_runner
  test_boot_policy_routing
  test_lifecycle_mutation_summaries
  test_failed_lifecycle_mutation_summary_and_next_are_suppressed
  test_default_targets_are_explicit
  test_autoscale_plan_routes_to_read_only_planner
  test_autoscale_run_once_routes_to_governed_controller
  test_autoscale_scheduler_routes_to_scheduler_boundary
  test_review_routes_to_read_only_review_boundary
  printf '\nContratos de routing/lifecycle passaram.\n'
}

main "$@"
