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
printf 'runners:%s\n' "$*" >> "${TEST_PLATFORM_LOG:?}"
case "${1:-}" in
  doctor)
    if [[ "${TEST_DOCTOR_FAIL:-0}" == "1" ]]; then
      exit 1
    fi
    ;;
esac
EOF

  cat > "$platform/runner-services.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'services:%s\n' "$*" >> "${TEST_PLATFORM_LOG:?}"
if [[ "${1:-}" == "migrate" && "${TEST_MIGRATE_FAIL:-0}" == "1" ]]; then
  exit 1
fi
EOF

  cat > "$platform/configure-runner.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
token=""
IFS= read -r token || true
printf 'configure:%s\n' "$*" >> "${TEST_PLATFORM_LOG:?}"
printf 'token:%s\n' "$token" >> "${TEST_PLATFORM_LOG:?}"
printf '%s\n' 'Runner local: projectcase'
if [[ "${TEST_CONFIGURE_FAIL:-0}" == "1" ]]; then
  printf '%s\n' 'simulated configure failure' >&2
  exit 1
fi
printf '%s\n' 'Runner configurado com sucesso.'
printf '%s\n' 'Nome local: projectcase'
EOF

  chmod +x "$platform/runners.sh" "$platform/runner-services.sh" "$platform/configure-runner.sh"
}

make_fake_commands() {
  local bin="$1"
  mkdir -p "$bin"

  cat > "$bin/systemctl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF

  cat > "$bin/sudo" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'sudo:%s\n' "$*" >> "${TEST_SUDO_LOG:?}"
if [[ "${TEST_SUDO_OK:-0}" == "1" ]]; then
  exit 0
fi
exit 1
EOF

  cat > "$bin/gh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'gh:%s\n' "$*" >> "${TEST_GH_LOG:?}"

if [[ "${1:-}" == "auth" && "${2:-}" == "status" ]]; then
  exit 0
fi

if [[ "${1:-}" == "repo" && "${2:-}" == "view" ]]; then
  printf '%s\n' 'Example/ProjectCase'
  exit 0
fi

if [[ "${1:-}" == "api" ]]; then
  if [[ "$*" == *"registration-token"* ]]; then
    printf '%s\n' 'test-registration-token'
    exit 0
  fi
fi

exit 1
EOF

  chmod +x "$bin/systemctl" "$bin/sudo" "$bin/gh"
}

run_add() {
  local platform="$1"
  shift
  PATH="$TMP_ROOT/bin:$PATH" \
    ACTIONS_RUNNERS_HOME="$platform" \
    ACTIONS_RUNNERS_ENV="$TMP_ROOT/missing.env" \
    RUNNER_SYSTEMD_RUNTIME_DIR="$TMP_ROOT/systemd-runtime" \
    TEST_GH_LOG="$TMP_ROOT/gh.log" \
    TEST_SUDO_LOG="$TMP_ROOT/sudo.log" \
    TEST_PLATFORM_LOG="$TMP_ROOT/platform.log" \
    "$ROOT/runnerctl" add example/projectcase --profile generic "$@"
}

reset_logs() {
  : > "$TMP_ROOT/gh.log"
  : > "$TMP_ROOT/sudo.log"
  : > "$TMP_ROOT/platform.log"
}

test_admin_preflight_blocks_before_registration_token() {
  local platform="$TMP_ROOT/preflight-platform"
  local output rc=0

  make_fake_platform "$platform"
  reset_logs

  set +e
  output="$(TEST_SUDO_OK=0 run_add "$platform" 2>&1)"
  rc=$?
  set -e

  [[ "$rc" -ne 0 ]] || fail "add deve falhar quando sudo não está disponível de forma não interativa"
  assert_contains "$output" "sudo exige autenticação interativa" "erro deve explicar preflight administrativo"
  assert_contains "$output" "sudo -v" "erro deve orientar autenticação local mínima"

  if grep -Fq 'registration-token' "$TMP_ROOT/gh.log"; then
    fail "preflight deve falhar antes de solicitar registration token"
  fi
  [[ ! -s "$TMP_ROOT/platform.log" ]] || fail "configure/migrate não podem rodar após preflight falhar"

  pass "preflight administrativo falha antes de qualquer registration token"
}

test_add_uses_canonical_repository_identity() {
  local platform="$TMP_ROOT/canonical-platform"
  local output

  make_fake_platform "$platform"
  reset_logs

  output="$(TEST_SUDO_OK=1 run_add "$platform")"

  assert_contains "$output" "Registrando runner para Example/ProjectCase" "add deve expor nome canônico"
  assert_contains "$output" "[OK] runner=projectcase repo=Example/ProjectCase" "conclusão deve preservar nome canônico"
  assert_contains "$(cat "$TMP_ROOT/platform.log")" "--repo-url https://github.com/Example/ProjectCase" "configure deve receber URL canônica"
  assert_contains "$(cat "$TMP_ROOT/gh.log")" "repos/Example/ProjectCase/actions/runners/registration-token" "API deve usar identidade canônica"

  pass "runnerctl add preserva nameWithOwner canônico até o configure"
}

test_migrate_failure_reports_partial_recovery() {
  local platform="$TMP_ROOT/partial-platform"
  local output rc=0

  make_fake_platform "$platform"
  reset_logs

  set +e
  output="$(TEST_SUDO_OK=1 TEST_MIGRATE_FAIL=1 run_add "$platform" 2>&1)"
  rc=$?
  set -e

  [[ "$rc" -ne 0 ]] || fail "add deve falhar quando migrate falha após registro"
  assert_contains "$output" "[PARTIAL] runner=projectcase repo=Example/ProjectCase phase=systemd-migrate" "estado parcial deve ser explícito"
  assert_contains "$output" "[RECOVERY] runnerctl doctor projectcase" "estado parcial deve orientar doctor"
  assert_contains "$output" "[RECOVERY] runnerctl migrate projectcase" "estado parcial deve orientar migrate"

  pass "falha pós-registro em migrate gera recuperação explícita sem repetir add"
}

test_configure_failure_is_marked_inconclusive() {
  local platform="$TMP_ROOT/inconclusive-platform"
  local output rc=0

  make_fake_platform "$platform"
  reset_logs

  set +e
  output="$(TEST_SUDO_OK=1 TEST_CONFIGURE_FAIL=1 run_add "$platform" 2>&1)"
  rc=$?
  set -e

  [[ "$rc" -ne 0 ]] || fail "add deve falhar quando configure falha"
  assert_contains "$output" "[INCONCLUSIVE] runner=projectcase repo=Example/ProjectCase phase=configure remote-registration=unknown" "configure failure deve preservar incerteza"
  assert_contains "$output" "[RECOVERY] runnerctl doctor projectcase" "configure failure conhecido deve orientar doctor"
  assert_contains "$output" "verifique o registro remoto antes de repetir runnerctl add" "não deve orientar retry cego"

  pass "falha de configure não assume sucesso nem incentiva add duplicado"
}

test_configure_persists_canonical_repo_case() {
  local fixture="$TMP_ROOT/tar-fixture"
  local tarball="$TMP_ROOT/fake-runner.tar.gz"
  local runner_root="$TMP_ROOT/runner-data"
  local registry="$TMP_ROOT/canonical-runners.conf"
  local sha output row repo_field

  mkdir -p "$fixture"
  cat > "$fixture/config.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' '{"agentId":42,"agentName":"fake"}' > .runner
exit 0
EOF
  chmod +x "$fixture/config.sh"
  tar -czf "$tarball" -C "$fixture" .
  sha="$(sha256sum "$tarball" | awk '{print $1}')"

  output="$(
    printf '%s\n' token |
      ACTIONS_RUNNERS_ENV="$TMP_ROOT/missing.env" \
      RUNNERS_CONFIG="$registry" \
      RUNNER_DATA_ROOT="$runner_root" \
      RUNNER_CACHE_ROOT="$TMP_ROOT/configure-cache" \
      "$ROOT/configure-runner.sh" \
        --repo-url https://github.com/Example/ProjectCase \
        --token-stdin \
        --name projectcase \
        --profile generic \
        --group projectcase \
        --runner-tar "$tarball" \
        --expected-sha256 "$sha"
  )"

  row="$(grep '^projectcase|' "$registry")"
  repo_field="$(printf '%s\n' "$row" | awk -F'|' '{print $4}')"
  assert_eq "Example/ProjectCase" "$repo_field" "registry deve persistir nameWithOwner com capitalização canônica"
  assert_contains "$output" "Repo full name: Example/ProjectCase" "configure deve reportar identidade canônica"

  pass "configure-runner persiste capitalização canônica no registry"
}

main() {
  mkdir -p "$TMP_ROOT/systemd-runtime"
  make_fake_commands "$TMP_ROOT/bin"
  test_admin_preflight_blocks_before_registration_token
  test_add_uses_canonical_repository_identity
  test_migrate_failure_reports_partial_recovery
  test_configure_failure_is_marked_inconclusive
  test_configure_persists_canonical_repo_case
  printf '\nContratos de provisioning seguro passaram.\n'
}

main "$@"
