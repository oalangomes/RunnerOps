#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
ISOLATED_ACTIONS_RUNNERS_ENV="$TMP_ROOT/missing-actions-runners.env"
EXPECTED_RUNNERCTL_VERSION="0.2.1"
trap 'rm -rf "$TMP_ROOT"' EXIT

pass() {
  printf '[PASS] %s\n' "$1"
}

fail() {
  printf '[FAIL] %s\n' "$1" >&2
  exit 1
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

make_fake_platform() {
  local platform="$1"
  mkdir -p "$platform"
  cp "$ROOT/runner-runtime-env.sh" "$platform/runner-runtime-env.sh"

  cat > "$platform/runners.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${TEST_CALL_LOG:?}"
EOF

  cat > "$platform/runner-services.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'service:%s\n' "$*" >> "${TEST_CALL_LOG:?}"
EOF

  chmod +x "$platform/runners.sh" "$platform/runner-services.sh"
}

test_version_is_platform_independent() {
  local isolated="$TMP_ROOT/version-only"
  local output

  mkdir -p "$isolated"
  cp "$ROOT/runnerctl" "$isolated/runnerctl"
  chmod +x "$isolated/runnerctl"

  output="$(
    HOME="$TMP_ROOT/version-home" \
    XDG_CONFIG_HOME="$TMP_ROOT/version-config" \
    "$isolated/runnerctl" --version
  )"

  assert_eq "runnerctl $EXPECTED_RUNNERCTL_VERSION" "$output" "--version deve funcionar sem platform-home"
  pass "--version independe do checkout/plataforma instalada"
}

test_repo_resolution() {
  local fake_bin="$TMP_ROOT/repo-bin"
  local output
  mkdir -p "$fake_bin"

  cat > "$fake_bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "repo" && "${2:-}" == "view" ]]; then
  printf '%s\n' 'Example/Project'
  exit 0
fi
exit 1
EOF
  chmod +x "$fake_bin/gh"

  output="$(
    PATH="$fake_bin:$PATH" \
    ACTIONS_RUNNERS_HOME="$ROOT" \
    XDG_CONFIG_HOME="$TMP_ROOT/repo-config" \
    "$ROOT/runnerctl" repo .
  )"

  assert_eq "example/project" "$output" "repo . deve normalizar owner/repo"
  pass "repo . resolve e normaliza owner/repo"
}

test_ensure_is_repo_scoped() {
  local platform="$TMP_ROOT/ensure-platform"
  local registry="$TMP_ROOT/ensure-runners.conf"
  local log="$TMP_ROOT/ensure-calls.log"
  local output expected_calls actual_calls

  make_fake_platform "$platform"
  : > "$log"

  cat > "$registry" <<EOF
# name|path|profile|repo|enabled|group
current-a|$TMP_ROOT/current-a|generic|example/project|true|current
disabled|$TMP_ROOT/disabled|generic|example/project|false|current
other|$TMP_ROOT/other|generic|example/other|true|other
current-b|$TMP_ROOT/current-b|generic|EXAMPLE/PROJECT|true|current
EOF

  output="$(
    TEST_CALL_LOG="$log" \
    ACTIONS_RUNNERS_ENV="$ISOLATED_ACTIONS_RUNNERS_ENV" \
    ACTIONS_RUNNERS_HOME="$platform" \
    RUNNERS_CONFIG="$registry" \
    "$ROOT/runnerctl" ensure example/project
  )"

  assert_contains "$output" "Repo: example/project" "ensure deve reportar o repo resolvido"

  expected_calls=$'start current-a\nstart current-b\nstatus current-a\nhealth current-a\nstatus current-b\nhealth current-b'
  actual_calls="$(cat "$log")"
  assert_eq "$expected_calls" "$actual_calls" "ensure deve tocar somente runners habilitados do repo atual"
  pass "ensure é estritamente scoped ao repositório"
}

test_remove_contracts() {
  local platform="$TMP_ROOT/remove-platform"
  local runner_dir="$TMP_ROOT/remove-runner"
  local registry="$TMP_ROOT/remove-runners.conf"
  local log="$TMP_ROOT/remove-calls.log"
  local before after output rejected

  make_fake_platform "$platform"
  : > "$log"
  mkdir -p "$runner_dir"

  printf '%s\n' '{"agentId":123456,"agentName":"ci-remove"}' > "$runner_dir/.runner"
  printf '%s\n' 'actions.runner.example.ci-remove.service' > "$runner_dir/.service"

  cat > "$registry" <<EOF
# name|path|profile|repo|enabled|group
ci-remove|$runner_dir|generic|example/example|true|example
EOF

  before="$(sha256sum "$registry" | awk '{print $1}')"
  output="$(
    TEST_CALL_LOG="$log" \
    ACTIONS_RUNNERS_ENV="$ISOLATED_ACTIONS_RUNNERS_ENV" \
    ACTIONS_RUNNERS_HOME="$platform" \
    RUNNERS_CONFIG="$registry" \
    "$ROOT/runnerctl" remove ci-remove --plan
  )"
  after="$(sha256sum "$registry" | awk '{print $1}')"

  assert_eq "$before" "$after" "remove --plan não pode mutar registry"
  assert_eq "" "$(cat "$log")" "remove --plan não pode chamar lifecycle"
  assert_contains "$output" "- runner: ci-remove" "plano deve nomear runner exato"
  assert_contains "$output" "- GitHub registration: REMOVE" "plano deve declarar remoção remota"
  assert_contains "$output" "- local directory: KEEP" "diretório local deve ser preservado por padrão"

  if TEST_CALL_LOG="$log" ACTIONS_RUNNERS_ENV="$ISOLATED_ACTIONS_RUNNERS_ENV" ACTIONS_RUNNERS_HOME="$platform" RUNNERS_CONFIG="$registry" \
      "$ROOT/runnerctl" remove all --plan >/dev/null 2>&1; then
    fail "remove all deve ser rejeitado"
  fi

  if TEST_CALL_LOG="$log" ACTIONS_RUNNERS_ENV="$ISOLATED_ACTIONS_RUNNERS_ENV" ACTIONS_RUNNERS_HOME="$platform" RUNNERS_CONFIG="$registry" \
      "$ROOT/runnerctl" remove group:example --plan >/dev/null 2>&1; then
    fail "remove group:* deve ser rejeitado"
  fi

  rejected="$(
    TEST_CALL_LOG="$log" \
    ACTIONS_RUNNERS_ENV="$ISOLATED_ACTIONS_RUNNERS_ENV" \
    ACTIONS_RUNNERS_HOME="$platform" \
    RUNNERS_CONFIG="$registry" \
    "$ROOT/runnerctl" remove all --plan 2>&1 || true
  )"
  assert_contains "$rejected" "all/group não são permitidos" "erro destrutivo deve ser explícito"
  pass "remove --plan e guards destrutivos permanecem seguros"
}

test_delete_dir_guards() {
  local platform="$TMP_ROOT/delete-platform"
  local data_root="$TMP_ROOT/data-root"
  local registry="$TMP_ROOT/delete-runners.conf"
  local log="$TMP_ROOT/delete-calls.log"
  local output

  make_fake_platform "$platform"
  : > "$log"
  mkdir -p "$data_root"

  cat > "$registry" <<EOF
# name|path|profile|repo|enabled|group
danger|$data_root|generic|example/example|true|example
EOF

  output="$(
    TEST_CALL_LOG="$log" \
    ACTIONS_RUNNERS_ENV="$ISOLATED_ACTIONS_RUNNERS_ENV" \
    ACTIONS_RUNNERS_HOME="$platform" \
    RUNNERS_CONFIG="$registry" \
    RUNNER_DATA_ROOT="$data_root" \
    "$ROOT/runnerctl" remove danger --plan --delete-dir 2>&1 || true
  )"

  assert_contains "$output" "recusando apagar RUNNER_DATA_ROOT" "--delete-dir deve proteger o data root"
  assert_eq "" "$(cat "$log")" "guard de path deve falhar antes do lifecycle"
  pass "--delete-dir protege RUNNER_DATA_ROOT"
}

test_install_and_xdg_from_arbitrary_checkout() {
  local platform="$TMP_ROOT/arbitrary/path/runnerctl-source"
  local config="$TMP_ROOT/xdg/config"
  local data="$TMP_ROOT/xdg/data"
  local cache="$TMP_ROOT/xdg/cache"
  local state="$TMP_ROOT/xdg/state"
  local bin="$TMP_ROOT/xdg/bin"
  local before_status after_status installed_home

  mkdir -p "$platform"
  cp \
    "$ROOT/runnerctl" \
    "$ROOT/runner-runtime-env.sh" \
    "$ROOT/init-machine-config.sh" \
    "$ROOT/runners.conf.example" \
    "$ROOT/sync-local-git-excludes.sh" \
    "$ROOT/runners.sh" \
    "$ROOT/runner-services.sh" \
    "$ROOT/install.sh" \
    "$platform/"
  chmod +x \
    "$platform/runnerctl" \
    "$platform/init-machine-config.sh" \
    "$platform/sync-local-git-excludes.sh" \
    "$platform/runners.sh" \
    "$platform/runner-services.sh" \
    "$platform/install.sh"

  before_status="$(git -C "$ROOT" status --porcelain --untracked-files=all)"

  mkdir -p "$bin"
  cat > "$bin/runnerctl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' 'runnerctl 0.1.0'
EOF
  chmod +x "$bin/runnerctl"
  assert_eq "runnerctl 0.1.0" "$("$bin/runnerctl" --version)" "fixture deve simular CLI instalada antiga"

  XDG_CONFIG_HOME="$config" RUNNERCTL_BIN_DIR="$bin" "$platform/install.sh" >/dev/null

  assert_eq "runnerctl $EXPECTED_RUNNERCTL_VERSION" "$("$bin/runnerctl" --version)" "reinstall deve substituir CLI antiga pela versão atual"

  installed_home="$(
    XDG_CONFIG_HOME="$config" \
    XDG_DATA_HOME="$data" \
    XDG_CACHE_HOME="$cache" \
    XDG_STATE_HOME="$state" \
    "$bin/runnerctl" platform-home
  )"
  assert_eq "$platform" "$installed_home" "install deve preservar checkout arbitrário como platform-home"

  XDG_CONFIG_HOME="$config" \
  XDG_DATA_HOME="$data" \
  XDG_CACHE_HOME="$cache" \
  XDG_STATE_HOME="$state" \
  "$bin/runnerctl" init >/dev/null

  [[ -f "$config/actions-runners/config.env" ]] || fail "config.env XDG não foi criado"
  [[ -f "$config/actions-runners/runners.conf" ]] || fail "runners.conf XDG não foi criado"
  [[ -d "$data/actions-runners/runners" ]] || fail "RUNNER_DATA_ROOT XDG não foi criado"
  [[ -d "$cache/actions-runners" ]] || fail "RUNNER_CACHE_ROOT XDG não foi criado"
  [[ -d "$state/actions-runners" ]] || fail "RUNNER_STATE_ROOT XDG não foi criado"

  after_status="$(git -C "$ROOT" status --porcelain --untracked-files=all)"
  assert_eq "$before_status" "$after_status" "install/init não devem sujar o checkout original"
  pass "install/init funcionam com checkout arbitrário e estado XDG"
}

main() {
  test_version_is_platform_independent
  test_repo_resolution
  test_ensure_is_repo_scoped
  test_remove_contracts
  test_delete_dir_guards
  test_install_and_xdg_from_arbitrary_checkout
  printf '\nTodos os contratos runnerctl deste slice passaram.\n'
}

main "$@"
