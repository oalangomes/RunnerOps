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

assert_not_contains() {
  local haystack="$1" needle="$2" message="$3"
  [[ "$haystack" != *"$needle"* ]] || {
    printf 'unexpected: %s\noutput:\n%s\n' "$needle" "$haystack" >&2
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

[[ "${1:-}" == "api" ]] || exit 1

request="${2:-}"
scenario="${TEST_SCENARIO:-success}"

if [[ "$request" == "repos/example/project/pulls/42" ]]; then
  printf '%s\n' 'abababababababababababababababababababab'
  exit 0
fi

if [[ "$request" == *"/actions/runs?"* ]]; then
  case "$scenario" in
    success|pr-success)
      printf '101\tCI\tcompleted\tsuccess\thttps://github.com/example/project/actions/runs/101\t1\n'
      printf '102\tLint\tcompleted\tskipped\thttps://github.com/example/project/actions/runs/102\t1\n'
      ;;
    rerun-success)
      count=0
      [[ -f "${TEST_GH_STATE:?}" ]] && count="$(cat "$TEST_GH_STATE")"
      count=$((count + 1))
      printf '%s\n' "$count" > "$TEST_GH_STATE"
      if [[ "$count" -eq 1 ]]; then
        printf '701\tCI\tin_progress\t\thttps://github.com/example/project/actions/runs/701\t2\n'
      else
        printf '701\tCI\tcompleted\tsuccess\thttps://github.com/example/project/actions/runs/701\t2\n'
      fi
      ;;
    rerun-failure)
      printf '702\tCI\tcompleted\tfailure\thttps://github.com/example/project/actions/runs/702\t2\n'
      ;;
    failure)
      printf '201\tCI\tcompleted\tfailure\thttps://github.com/example/project/actions/runs/201\t1\n'
      ;;
    startup-failure)
      printf '202\tCI\tcompleted\tstartup_failure\thttps://github.com/example/project/actions/runs/202\t1\n'
      ;;
    cancelled)
      printf '301\tCI\tcompleted\tcancelled\thttps://github.com/example/project/actions/runs/301\t1\n'
      ;;
    self-hosted-offline)
      printf '501\tCI\tqueued\t\thttps://github.com/example/project/actions/runs/501\t1\n'
      ;;
    self-hosted-busy)
      printf '601\tCI\tqueued\t\thttps://github.com/example/project/actions/runs/601\t1\n'
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

if [[ "$request" == *"/actions/runs/201/attempts/1/jobs?"* ]]; then
  printf '9001\tunit-tests\tfailure\tlocal-runner-1\tDefault\tself-hosted,Linux,X64,project\tRun tests\tfailure\n'
  exit 0
fi

if [[ "$request" == *"/actions/runs/202/attempts/1/jobs?"* ]]; then
  exit 0
fi

if [[ "$request" == *"/actions/runs/501/attempts/1/jobs?"* ]]; then
  printf '9501\tbuild\tqueued\t-\tDefault\tself-hosted,Linux,X64,project\n'
  exit 0
fi

if [[ "$request" == *"/actions/runs/601/attempts/1/jobs?"* ]]; then
  printf '9601\tbuild\tqueued\t-\tDefault\tself-hosted,Linux,X64,project\n'
  exit 0
fi

if [[ "$request" == *"/actions/runs/702/attempts/2/jobs?"* ]]; then
  printf '9702\tunit-tests\tfailure\tlocal-runner-2\tDefault\tself-hosted,Linux,X64,project\tRun tests on rerun\tfailure\n'
  exit 0
fi

if [[ "$request" == *"/actions/runs/702/attempts/1/jobs?"* || "$request" == *"/actions/runs/702/jobs?"* ]]; then
  printf '9701\tunit-tests\tfailure\tlocal-runner-1\tDefault\tself-hosted,Linux,X64,project\tStale first attempt\tfailure\n'
  exit 0
fi

if [[ "$request" == *"/actions/runners?"* ]]; then
  case "$scenario" in
    self-hosted-offline)
      printf '77\tlocal-runner-1\toffline\tfalse\tself-hosted,Linux,X64,project\n'
      ;;
    self-hosted-busy)
      printf '78\tlocal-runner-2\tonline\ttrue\tself-hosted,Linux,X64,project\n'
      ;;
    *)
      ;;
  esac
  exit 0
fi

exit 0
EOF

  chmod +x "$fake_bin/gh"
  printf '%s\n' "$fake_bin"
}

run_helper() {
  local scenario="$1"
  shift
  local fake_bin
  fake_bin="$(make_fake_gh)"
  rm -f "$TMP_ROOT/gh-state"
  TEST_SCENARIO="$scenario" \
  TEST_GH_LOG="$TMP_ROOT/gh.log" \
  TEST_GH_STATE="$TMP_ROOT/gh-state" \
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
    TEST_GH_STATE="$TMP_ROOT/gh-state" \
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

test_pr_correlation_resolves_head_sha() {
  local fake_bin output log
  fake_bin="$(make_fake_gh)"
  : > "$TMP_ROOT/gh.log"
  rm -f "$TMP_ROOT/gh-state"

  output="$(
    TEST_SCENARIO=pr-success \
    TEST_GH_LOG="$TMP_ROOT/gh.log" \
    TEST_GH_STATE="$TMP_ROOT/gh-state" \
    PATH="$fake_bin:$PATH" \
    ACTIONS_RUNNERS_HOME="$ROOT" \
    XDG_CONFIG_HOME="$TMP_ROOT/config-pr" \
    "$ROOT/runnerctl" ci watch . --pr 42 --json --timeout 0 --interval 0 --settle-polls 1
  )"
  log="$(cat "$TMP_ROOT/gh.log")"

  assert_contains "$output" '"status":"success"' "watch por PR deve concluir normalmente"
  assert_contains "$output" '"pr_number":42' "payload deve preservar número da PR"
  assert_contains "$output" '"sha":"abababababababababababababababababababab"' "payload deve usar head SHA da PR"
  assert_contains "$log" "repos/example/project/pulls/42" "watch deve resolver a PR explicitamente"
  assert_contains "$log" "head_sha=abababababababababababababababababababab" "runs devem ser filtrados pelo head SHA resolvido"

  pass "runnerctl ci watch --pr correlaciona PR ao head SHA correto"
}

test_ci_failure_includes_job_step_and_runner() {
  local output rc
  : > "$TMP_ROOT/gh.log"

  set +e
  output="$(run_helper failure --repo example/project --sha aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa --json --timeout 0 --interval 0 --settle-polls 1 2>&1)"
  rc=$?
  set -e

  assert_status 1 "$rc" "failure de workflow deve retornar exit 1"
  assert_contains "$output" '"status":"failure"' "payload deve indicar failure"
  assert_contains "$output" '"kind":"ci"' "failure de teste não pode virar infra failure"
  assert_contains "$output" '"workflow":"CI"' "payload deve identificar workflow"
  assert_contains "$output" '"job":"unit-tests"' "payload deve identificar job"
  assert_contains "$output" '"step":"Run tests"' "payload deve identificar step"
  assert_contains "$output" '"runner_name":"local-runner-1"' "payload deve identificar runner"
  assert_contains "$output" '"runner_group":"Default"' "payload deve identificar runner group"
  assert_contains "$output" '"runner_labels":"self-hosted,Linux,X64,project"' "payload deve expor labels do job"
  assert_contains "$output" '"run_id":201' "payload deve identificar run"
  assert_contains "$output" '"run_attempt":1' "payload deve identificar attempt observado"
  assert_contains "$(cat "$TMP_ROOT/gh.log")" "/actions/runs/201/attempts/1/jobs" "detalhes devem ser consultados no attempt exato"

  pass "CI failure é enriquecido com job/step/runner sem mudar classificação"
}

test_rerun_uses_latest_attempt_without_stale_failure() {
  local output rc log

  : > "$TMP_ROOT/gh.log"
  set +e
  output="$(run_helper rerun-success --repo example/project --sha 7777777777777777777777777777777777777777 --json --timeout 5 --interval 0 --settle-polls 1 2>&1)"
  rc=$?
  set -e

  assert_status 0 "$rc" "rerun ativo que conclui verde deve retornar success"
  assert_contains "$output" '"status":"success"' "rerun concluído deve retornar success"

  : > "$TMP_ROOT/gh.log"
  set +e
  output="$(run_helper rerun-failure --repo example/project --sha 8888888888888888888888888888888888888888 --json --timeout 0 --interval 0 --settle-polls 1 2>&1)"
  rc=$?
  set -e
  log="$(cat "$TMP_ROOT/gh.log")"

  assert_status 1 "$rc" "falha no segundo attempt continua sendo CI failure"
  assert_contains "$output" '"run_id":702' "payload deve preservar run id do rerun"
  assert_contains "$output" '"run_attempt":2' "payload deve identificar o segundo attempt"
  assert_contains "$output" '"step":"Run tests on rerun"' "detalhes devem vir do attempt atual"
  assert_not_contains "$output" 'Stale first attempt' "payload não pode reutilizar falha do attempt anterior"
  assert_contains "$log" "/actions/runs/702/attempts/2/jobs" "watch deve consultar jobs do attempt 2"
  assert_not_contains "$log" "/actions/runs/702/attempts/1/jobs" "watch não deve consultar jobs do attempt antigo"

  pass "rerun/run_attempt não confunde resultado ou detalhes de tentativa anterior"
}

test_startup_failure_is_infra() {
  local output rc

  set +e
  output="$(run_helper startup-failure --repo example/project --sha ffffffffffffffffffffffffffffffffffffffff --json --timeout 0 --interval 0 --settle-polls 1 2>&1)"
  rc=$?
  set -e

  assert_status 2 "$rc" "startup_failure deve retornar exit 2"
  assert_contains "$output" '"status":"failure"' "startup failure deve permanecer failure conclusivo"
  assert_contains "$output" '"kind":"infra"' "startup failure deve ser infraestrutura"
  assert_contains "$output" '"diagnosis":"workflow_startup_failure"' "diagnóstico deve ser explícito"

  pass "workflow startup_failure é separado de falha de código"
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

  pass "cancelled/no-runs permanecem inconclusivos"
}

test_self_hosted_unavailable_is_infra() {
  local output rc

  set +e
  output="$(run_helper self-hosted-offline --repo example/project --sha 1111111111111111111111111111111111111111 --json --timeout 0 --interval 0 --settle-polls 1 2>&1)"
  rc=$?
  set -e

  assert_status 2 "$rc" "job queued sem runner online compatível deve retornar exit 2"
  assert_contains "$output" '"status":"error"' "runner indisponível deve ser erro de infra"
  assert_contains "$output" '"kind":"infra"' "runner indisponível deve ser infraestrutura"
  assert_contains "$output" '"job":"build"' "diagnóstico deve apontar job aguardando"
  assert_contains "$output" '"runner_labels":"self-hosted,Linux,X64,project"' "diagnóstico deve apontar labels requeridas"
  assert_contains "$output" '"diagnosis":"no_matching_online_self_hosted_runner"' "diagnóstico deve identificar ausência de runner online"
  assert_contains "$output" 'runnerctl doctor/health' "mensagem deve orientar diagnóstico sem mutação automática"

  pass "self-hosted queued sem capacidade online é infra explícita"
}

test_self_hosted_busy_remains_inconclusive() {
  local output rc

  set +e
  output="$(run_helper self-hosted-busy --repo example/project --sha 2222222222222222222222222222222222222222 --json --timeout 0 --interval 0 --settle-polls 1 2>&1)"
  rc=$?
  set -e

  assert_status 3 "$rc" "runner compatível ocupado não deve virar infra failure"
  assert_contains "$output" '"status":"timeout"' "capacidade ocupada deve continuar timeout"
  assert_contains "$output" '"kind":"inconclusive"' "capacidade ocupada deve ser inconclusiva"
  assert_contains "$output" '"diagnosis":"matching_self_hosted_runners_busy"' "diagnóstico deve distinguir busy de offline"

  pass "runner compatível busy não é confundido com falha de infraestrutura"
}

test_access_failures_are_distinct() {
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

  pass "falhas de acesso ao GitHub são distintas de falhas do CI"
}

main() {
  test_runnerctl_success_and_sha_correlation
  test_pr_correlation_resolves_head_sha
  test_ci_failure_includes_job_step_and_runner
  test_rerun_uses_latest_attempt_without_stale_failure
  test_startup_failure_is_infra
  test_cancelled_and_timeout_are_inconclusive
  test_self_hosted_unavailable_is_infra
  test_self_hosted_busy_remains_inconclusive
  test_access_failures_are_distinct
  printf '\nContratos de ci watch passaram.\n'
}

main "$@"
