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

make_fixture() {
  local name="$1"
  local runner_dir="$TMP_ROOT/$name-runner"
  local registry="$TMP_ROOT/$name-runners.conf"

  mkdir -p "$runner_dir"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$runner_dir/run.sh"
  chmod +x "$runner_dir/run.sh"
  printf '%s\n' '{"agentId":1,"agentName":"legacy"}' > "$runner_dir/.runner"

  printf '%s\n' '# name|path|profile|repo|enabled|group' > "$registry"
  printf '%s|%s|generic|example/project|true|example\n' "$name" "$runner_dir" >> "$registry"

  printf '%s|%s\n' "$runner_dir" "$registry"
}

run_logs() {
  local registry="$1" name="$2"

  ACTIONS_RUNNERS_ENV="$TMP_ROOT/missing.env" \
    RUNNERS_CONFIG="$registry" \
    RUNNER_STATE_ROOT="$TMP_ROOT/state" \
    RUNNER_CACHE_ROOT="$TMP_ROOT/cache" \
    RUNNER_SYSTEMD_RUNTIME_DIR="$TMP_ROOT/no-systemd" \
    RUNNER_LOG_LINES=2 \
    "$ROOT/runners.sh" logs "$name"
}

test_legacy_log_content_and_latest_diag() {
  local fixture runner_dir registry output log_file diag_dir

  fixture="$(make_fixture useful)"
  IFS='|' read -r runner_dir registry <<< "$fixture"

  log_file="$TMP_ROOT/state/legacy-logs/useful.log"
  diag_dir="$runner_dir/_diag"
  mkdir -p "$(dirname "$log_file")" "$diag_dir"

  printf '%s\n' old-line keep-one keep-two > "$log_file"

  printf '%s\n' older-diag > "$diag_dir/Runner_old.log"
  sleep 0.1
  printf '%s\n' newest-one newest-two newest-three > "$diag_dir/Runner_new.log"

  output="$(run_logs "$registry" useful)"

  assert_contains "$output" "===== useful group=example backend=legacy =====" "logs legado deve ter header útil"
  assert_contains "$output" "log: $log_file" "logs legado deve informar path"
  assert_contains "$output" "keep-one" "logs legado deve mostrar conteúdo bounded"
  assert_contains "$output" "keep-two" "logs legado deve mostrar últimas linhas"
  assert_not_contains "$output" "old-line" "logs legado deve respeitar RUNNER_LOG_LINES"
  assert_contains "$output" "===== latest _diag =====" "logs legado deve incluir _diag"
  assert_contains "$output" "diag: $diag_dir/Runner_new.log" "deve escolher Runner_*.log mais recente"
  assert_contains "$output" "newest-two" "deve mostrar conteúdo do _diag mais recente"
  assert_contains "$output" "newest-three" "deve mostrar tail do _diag"
  assert_not_contains "$output" "older-diag" "não deve misturar _diag antigo"

  pass "legacy logs mostram conteúdo bounded e o _diag mais recente"
}

test_empty_and_missing_logs_are_explicit() {
  local fixture runner_dir registry output log_file

  fixture="$(make_fixture empty)"
  IFS='|' read -r runner_dir registry <<< "$fixture"

  log_file="$TMP_ROOT/state/legacy-logs/empty.log"
  mkdir -p "$(dirname "$log_file")"
  : > "$log_file"

  output="$(run_logs "$registry" empty)"

  assert_contains "$output" "[INFO] legacy log is empty" "log vazio deve ser explícito"
  assert_contains "$output" "[INFO] no _diag log found" "ausência de _diag deve ser explícita"

  rm -f "$log_file"
  output="$(run_logs "$registry" empty)"
  assert_contains "$output" "[INFO] legacy log not found" "log ausente deve ser explícito"

  pass "legacy logs distinguem vazio, ausente e _diag inexistente"
}

main() {
  test_legacy_log_content_and_latest_diag
  test_empty_and_missing_logs_are_explicit
  printf '\nContratos de legacy diagnostics passaram.\n'
}

main "$@"
