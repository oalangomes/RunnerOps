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

assert_status() {
  local expected="$1" actual="$2" message="$3"
  [[ "$actual" -eq "$expected" ]] || {
    printf 'expected status: %s\nactual status:   %s\n' "$expected" "$actual" >&2
    fail "$message"
  }
}

make_fake_gh() {
  local fake_bin="$TMP_ROOT/bin"
  mkdir -p "$fake_bin"

  cat > "$fake_bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' "$*" >> "${TEST_GH_LOG:?}"

if [[ "${1:-}" == "auth" && "${2:-}" == "status" ]]; then
  [[ "${TEST_SCENARIO:-success}" != "auth-fail" ]]
  exit
fi

if [[ "${1:-}" == "repo" && "${2:-}" == "view" ]]; then
  printf '%s\n' 'Example/Project'
  exit 0
fi

if [[ "${1:-}" == "api" ]]; then
  case "${TEST_SCENARIO:-success}" in
    success)
      printf '101\tCI\tcompleted\tsuccess\thttps://github.com/example/project/actions/runs/101\t1\n'
      printf '102\tLint\tcompleted\tskipped\thttps://github.com/example/project/actions/runs/102\t1\n'
      ;;
    failure)
      printf '201\tCI\tcompleted\tfailure\thttps://github.com/example/project/actions/runs/201\t1\n'
      ;;
    cancelled)
      printf '301\tCI\tcompleted\tcancelled\thttps://github.com/example/project/actions/runs/301\t1\n'
      ;;
    active)
      printf '401\tCI\tin_progress\t\thttps://github.com/example/project/actions/runs/401\t1\n'
      ;;
    no-runs)
      ;;
    api-fail)
      exit 1
      ;;
    *)
      exit 1
      ;;
  esac
  exit 0
fi

exit 1
EOF

  chmod +x "$fake_bin/gh"
  printf '%s\n' "$fake_bin"
}

run_helper() {
  local scenario="$1"
  shift
  local fake_bin
  fake_bin="$(make_fake_gh)"
  TEST_SCENARIO="$scenario" \
  TEST_GH_LOG="$TMP_ROOT/gh.log" \
  PATH="$fake_bin:$PATH" \
    bash "$ROOT/ci-watch.sh" "$@"
}

test_runnerctl_success_and_sha_correlation() {
  local fake_bin output current_sha
  fake_bin="$(make_fake_gh)"
  : > "$TMP_ROOT/gh.log"
  current_sha="$(git -C "$ROOT" rev-parse HEAD)"

  output="$(
    TEST_SCENARIO=success \
    TEST_GH_LOG="$TMP_ROOT/gh.log" \
    PATH="$fake_bin:$PATH" \
    ACTIONS_RUNNERS_HOME="$ROOT" \
    XDG_CONFIG_HOME="$TMP_ROOT/config" \
    "$ROOT/runnerctl" ci watch . --json --timeout 0 --interval 0 --settle-polls 1
  )"

  assert_contains "$output" '"status":"success"' "ci watch deve retornar success"
  assert_contains "$output" '"kind":"ci"' "success deve ser classificado como CI"
  assert_contains "$output" '"repo":"example/project"' "repo deve ser normalizado"
  assert_contains "$output" '"run_count":2' "todos os workflows do SHA devem ser contabilizados"
  assert_contains "$(cat "$TMP_ROOT/gh.log")" "head_sha=$current_sha" "consulta deve correlacionar o SHA atual"

  pass "runnerctl ci watch correlaciona repo+SHA e agrega workflows"
}

test_ci_failure_exit_code() {
  local output rc
  : > "$TMP_ROOT/gh.log"

  set +e
  output="$(run_helper failure --repo example/project --sha aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --json --timeout 0 --interval 0 --settle-polls 1 2>&1)"
  rc=$?
  set -e

  assert_status 1 "$rc" "failure de workflow deve retornar exit 1"
  assert_contains "$output" '"status":"failure"' "payload deve indicar failure"
  assert_contains "$output" '"kind":"ci"' "failure de workflow não pode virar infra failure"
  assert_contains "$output" '"workflow":"CI"' "payload deve identificar workflow"
  assert_contains "$output" '"run_id":201' "payload deve identificar run"

  pass "CI failure retorna payload estruturado e exit 1"
}

test_cancelled_and_timeout_are_inconclusive() {
  local output rc

  set +e
  output="$(run_helper cancelled --repo example/project --sha bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb --json --timeout 0 --interval 0 --settle-polls 1 2>&1)"
  rc=$?
  set -e
  assert_status 3 "$rc" "cancelled deve retornar exit 3"
  assert_contains "$output" '"status":"cancelled"' "cancelled deve ser explícito"
  assert_contains "$output" '"kind":"inconclusive"' "cancelled deve ser inconclusivo"

  set +e
  output="$(run_helper no-runs --repo example/project --sha cccccccccccccccccccccccccccccccccccccccc --json --timeout 0 --interval 0 --settle-polls 1 2>&1)"
  rc=$?
  set -e
  assert_status 3 "$rc" "timeout sem runs deve retornar exit 3"
  assert_contains "$output" '"status":"timeout"' "timeout deve ser explícito"

  pass "cancelled/no-runs permanecem inconclusivos e não viram CI failure"
}

test_infra_failures_are_distinct() {
  local output rc

  set +e
  output="$(run_helper auth-fail --repo example/project --sha dddddddddddddddddddddddddddddddddddddddd --json --timeout 0 --interval 0 --settle-polls 1 2>&1)"
  rc=$?
  set -e
  assert_status 2 "$rc" "auth failure deve retornar exit 2"
  assert_contains "$output" '"kind":"infra"' "auth failure deve ser infraestrutura"

  set +e
  output="$(run_helper api-fail --repo example/project --sha eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee --json --timeout 0 --interval 0 --settle-polls 1 2>&1)"
  rc=$?
  set -e
  assert_status 2 "$rc" "API failure deve retornar exit 2"
  assert_contains "$output" '"status":"error"' "API failure deve ser erro estruturado"

  pass "falhas de acesso/infra são distintas de falhas do CI"
}

main() {
  test_runnerctl_success_and_sha_correlation
  test_ci_failure_exit_code
  test_cancelled_and_timeout_are_inconclusive
  test_infra_failures_are_distinct
  printf '\nContratos de ci watch passaram.\n'
}

main "$@"
