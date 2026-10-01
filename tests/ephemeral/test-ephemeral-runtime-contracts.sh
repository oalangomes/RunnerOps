#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_ROOT="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

fail() {
  printf '[FAIL] %s\n' "$1" >&2
  exit 1
}

assert_contains() {
  local haystack="$1" needle="$2" message="$3"
  [[ "$haystack" == *"$needle"* ]] || fail "$message (missing: $needle)"
}

for duration in nan NaN inf +inf -inf infinity 0 -1; do
  runtime_error="$(
    ACTIONS_RUNNERS_ENV="$TMP_ROOT/missing.env" \
    RUNNER_EPHEMERAL_REGISTRATION_ABSENCE_CONFIRM_SECONDS="$duration" \
      bash -c 'source "$1"' _ "$ROOT/scripts/runner/runtime-env.sh" 2>&1
  )" && fail "runtime-env deve rejeitar duração inválida: $duration"
  assert_contains "$runtime_error" "duração finita e positiva" \
    "runtime-env deve explicar duração inválida"
  [[ "$runtime_error" != *"Traceback"* ]] || fail "configuração inválida não pode vazar traceback"
done

for duration in 0.1 1 3 30.5; do
  observed="$(
    ACTIONS_RUNNERS_ENV="$TMP_ROOT/missing.env" \
    RUNNER_EPHEMERAL_REGISTRATION_ABSENCE_CONFIRM_SECONDS="$duration" \
      bash -c 'source "$1"; printf "%s" "$RUNNER_EPHEMERAL_REGISTRATION_ABSENCE_CONFIRM_SECONDS"' \
      _ "$ROOT/scripts/runner/runtime-env.sh"
  )"
  [[ "$observed" == "$duration" ]] || fail "runtime-env deve aceitar duração finita: $duration"
done

cli_error="$(
  PYTHONPATH="$ROOT/src" RUNNER_EPHEMERAL_COMMAND_TIMEOUT_SECONDS=inf \
    python3 -B -m runnerops.ephemeral.cli status 33333333333333333333333333333333 2>&1
)" && fail "CLI ephemeral deve rejeitar timeout infinito"
assert_contains "$cli_error" "RUNNER_EPHEMERAL_COMMAND_TIMEOUT_SECONDS must be a finite positive duration" \
  "CLI deve expor erro de configuração estreito"
[[ "$cli_error" != *"Traceback"* ]] || fail "CLI não pode vazar traceback de float inválido"

action_id="33333333333333333333333333333333"
identity="runnerops-ephemeral-$(printf '%s' "$action_id" | sha256sum | cut -c1-16)"
runner_root="$TMP_ROOT/ephemeral/$action_id"
fixture="$TMP_ROOT/fixture"
package="$TMP_ROOT/actions-runner.tar.gz"
registry="$TMP_ROOT/runners.conf"
config_log="$TMP_ROOT/config.log"
systemctl_log="$TMP_ROOT/systemctl.log"
service_user="$(id -un)"
unit="actions.runner.runnerops-ephemeral-${service_user}@${action_id}.service"

mkdir -p "$runner_root" "$fixture"
printf '%s\n' "persistent|$TMP_ROOT/persistent|generic|Example/Repo|true|repo" > "$registry"
registry_before="$(sha256sum "$registry" | awk '{print $1}')"
printf '%s\n' \
  '{"action_id": "33333333333333333333333333333333", "disposable_root": "'"$runner_root"'", "kind": "RunnerOpsEphemeralOwnership", "runner_identity": "'"$identity"'", "schema_version": 1}' \
  > "$runner_root/.runnerops-ephemeral-owner.json"

cat > "$fixture/config.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'args:%s\n' "$*" > "${TEST_CONFIG_LOG:?}"
printf 'material:%s\n' "${ACTIONS_RUNNER_INPUT_TOKEN:-missing}" >> "${TEST_CONFIG_LOG:?}"
runner_name=""
while (($#)); do
  if [[ "$1" == "--name" ]]; then runner_name="${2:-}"; break; fi
  shift
done
printf '{"agentId":42,"agentName":"%s"}\n' "$runner_name" > .runner
EOF
cat > "$fixture/run.sh" <<'EOF'
#!/usr/bin/env bash
sleep 30
EOF
chmod +x "$fixture/config.sh" "$fixture/run.sh"
tar -czf "$package" -C "$fixture" .

mkdir -p "$TMP_ROOT/bin" "$TMP_ROOT/systemd-runtime"
cat > "$TMP_ROOT/bin/systemctl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'systemctl:%s\n' "$*" >> "${TEST_SYSTEMCTL_LOG:?}"
case "${1:-}" in
  cat) exit 0 ;;
  show)
    if [[ "$*" == *"--property=ActiveState --value"* ]]; then
      printf '%s\n' active
    else
      printf '%s\n' 'LoadState=loaded' 'ActiveState=active' 'SubState=running' 'Result=success' 'MainPID=4242' 'ExecMainStartTimestampMonotonic=123456'
    fi
    ;;
  *) exit 1 ;;
esac
EOF
cat > "$TMP_ROOT/bin/runnerops-systemctl" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf 'helper:%s\n' "$*" >> "${TEST_SYSTEMCTL_LOG:?}"
EOF
cat > "$TMP_ROOT/bin/sudo" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == "-n" ]] && shift
exec "$@"
EOF
chmod +x "$TMP_ROOT/bin/systemctl" "$TMP_ROOT/bin/runnerops-systemctl" "$TMP_ROOT/bin/sudo"

RUNNERS_CONFIG="$registry" TEST_CONFIG_LOG="$config_log" \
  "$ROOT/scripts/runner/ephemeral.sh" prepare \
    --root "$runner_root" --action-id "$action_id" --identity "$identity" --package "$package"

printf '%s\n' 'one-use-registration-material' | \
  RUNNERS_CONFIG="$registry" TEST_CONFIG_LOG="$config_log" \
  "$ROOT/scripts/runner/ephemeral.sh" configure \
    --root "$runner_root" --action-id "$action_id" --identity "$identity" \
    --repo-url https://github.com/Example/Repo --labels repo,ephemeral,"$identity"

log="$(cat "$config_log")"
assert_contains "$log" "--ephemeral" "configuração deve habilitar ephemeral explicitamente"
assert_contains "$log" "--unattended" "configuração deve ser não interativa"
assert_contains "$log" "--disableupdate" "runner descartável deve reutilizar pacote sem self-update"
assert_contains "$log" "--name $identity" "configuração deve usar identidade exata"
assert_contains "$log" "material:one-use-registration-material" "material deve chegar apenas ao processo de configuração"
[[ "$log" != *"--token one-use-registration-material"* ]] || fail "material não pode aparecer em argv"

runner_unit="$(
  PATH="$TMP_ROOT/bin:$PATH" RUNNERS_CONFIG="$registry" TEST_CONFIG_LOG="$config_log" \
    TEST_SYSTEMCTL_LOG="$systemctl_log" RUNNER_SYSTEMD_RUNTIME_DIR="$TMP_ROOT/systemd-runtime" \
    RUNNEROPS_SYSTEMCTL_HELPER="$TMP_ROOT/bin/runnerops-systemctl" \
    "$ROOT/scripts/runner/ephemeral.sh" start \
      --root "$runner_root" --action-id "$action_id" --identity "$identity" --unit "$unit"
)"
[[ "$runner_unit" == "$unit" ]] || fail "helper deve iniciar somente a unit exata"
assert_contains "$(cat "$systemctl_log")" "helper:start $unit" "start deve usar helper systemd autorizado"

authorize_source="$(cat "$ROOT/scripts/setup/authorize-runtime.sh")"
assert_contains "$authorize_source" 'actions.runner.runnerops-ephemeral-${invoking_user}@.service' \
  "platform-authorize deve instalar template ephemeral exato"
assert_contains "$authorize_source" 'KillMode=control-group' \
  "template ephemeral deve governar todo o process group via systemd"

workflow_source="$(cat "$ROOT/.github/workflows/validate.yml")"
assert_contains "$workflow_source" 'ephemeral_runner_label:' \
  "workflow publicado deve aceitar a identidade ephemeral exata"
assert_contains "$workflow_source" '${{ inputs.ephemeral_runner_label }}' \
  "qualification job deve selecionar a label ephemeral exata"
assert_contains "$workflow_source" "inputs.ephemeral_runner_label == ''" \
  "dogfood genérico não pode disputar o runner durante qualification"
assert_contains "$workflow_source" 'test "$RUNNER_NAME" = "$EXPECTED_RUNNER"' \
  "qualification job deve provar a identidade consumidora"
assert_contains "$workflow_source" 'expected_suffix="$(printf '\''%s'\'' "$EXPECTED_ACTION" | sha256sum | cut -c1-16)"' \
  "qualification deve derivar a identidade exata do action id"
assert_contains "$workflow_source" 'test "$EXPECTED_RUNNER" = "runnerops-ephemeral-$expected_suffix"' \
  "qualification deve correlacionar action id e label antes do job"
[[ "$(grep -Fc 'ACTIONS_RUNNERS_HOME="$platform_home" "$current_runnerctl" status "$local_runner_name"' \
  "$ROOT/.github/workflows/validate.yml")" -eq 1 ]] ||
  fail "dogfood deve executar o bloco status/health/plan/list uma única vez"

registry_after="$(sha256sum "$registry" | awk '{print $1}')"
[[ "$registry_before" == "$registry_after" ]] || fail "lifecycle ephemeral não pode alterar registry persistente"

printf '[PASS] helper configura one-job explicitamente sem contaminar registry persistente\n'
