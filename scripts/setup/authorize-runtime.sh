#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNEROPS_PLATFORM_HOME="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=/dev/null
source "$RUNNEROPS_PLATFORM_HOME/scripts/runner/runtime-env.sh"
SOURCE_HELPER="$RUNNEROPS_PLATFORM_HOME/scripts/systemd/runnerops-systemctl"
TARGET_HELPER="/usr/local/libexec/runnerops-systemctl"

die() {
  echo "ERRO: $*" >&2
  exit 1
}

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

invoking_user="${SUDO_USER:-$(id -un)}"
[[ "$invoking_user" =~ ^[A-Za-z0-9_.-]+$ ]] ||
  die "usuario local nao suportado para sudoers: $invoking_user"

# The autoscale template is installed once under human authorization. Its
# process always runs as the invoking non-root operator; no user-writable script
# is ever executed as root by the NOPASSWD helper.
[[ "$RUNNER_DATA_ROOT" == /* ]] || die "RUNNER_DATA_ROOT deve ser absoluto"
[[ "$RUNNER_STATE_ROOT" == /* ]] || die "RUNNER_STATE_ROOT deve ser absoluto"
[[ "$RUNNER_EPHEMERAL_ROOT" == /* ]] || die "RUNNER_EPHEMERAL_ROOT deve ser absoluto"
[[ "$RUNNER_DATA_ROOT" != *$'\n'* && "$RUNNER_DATA_ROOT" != *$'\r'* ]] ||
  die "RUNNER_DATA_ROOT contém newline não suportado"
[[ "$RUNNER_STATE_ROOT" != *$'\n'* && "$RUNNER_STATE_ROOT" != *$'\r'* ]] ||
  die "RUNNER_STATE_ROOT contém newline não suportado"
[[ "$RUNNER_EPHEMERAL_ROOT" != *$'\n'* && "$RUNNER_EPHEMERAL_ROOT" != *$'\r'* ]] ||
  die "RUNNER_EPHEMERAL_ROOT contém newline não suportado"

template_unit="actions.runner.runnerops-${invoking_user}@.service"
template_path="/etc/systemd/system/$template_unit"
ephemeral_template_unit="actions.runner.runnerops-ephemeral-${invoking_user}@.service"
ephemeral_template_path="/etc/systemd/system/$ephemeral_template_unit"

template_tmp="$(mktemp)"
ephemeral_template_tmp="$(mktemp)"
sudoers_file="/etc/sudoers.d/runnerops-${invoking_user}"
tmp="$(mktemp)"
trap 'rm -f "$tmp" "$template_tmp" "$ephemeral_template_tmp"' EXIT

[[ -f "$SOURCE_HELPER" ]] || die "helper ausente: $SOURCE_HELPER"
command -v sudo >/dev/null 2>&1 || [[ "$(id -u)" -eq 0 ]] ||
  die "sudo e obrigatorio para instalar a autorizacao de runtime"

# /usr/bin/true and /bin/true are harmless bounded commands used only by the
# existing runnerctl add administrative preflight (`sudo -n true`). Actual
# systemd mutations remain restricted to the root-owned helper below.
printf '%s ALL=(root) NOPASSWD: %s, /usr/bin/true, /bin/true\n' \
  "$invoking_user" "$TARGET_HELPER" > "$tmp"
chmod 0440 "$tmp"

cat > "$template_tmp" <<EOF
[Unit]
Description=RunnerOps GitHub Actions Runner (%i)
After=network-online.target

[Service]
ExecStart=$RUNNER_DATA_ROOT/%i/bin/runsvc.sh
User=$invoking_user
WorkingDirectory=$RUNNER_DATA_ROOT/%i
EnvironmentFile=-$RUNNER_STATE_ROOT/service-env/%i.env
KillMode=process
KillSignal=SIGTERM
TimeoutStopSec=5min

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$template_tmp"

cat > "$ephemeral_template_tmp" <<EOF
[Unit]
Description=RunnerOps Ephemeral GitHub Actions Runner (%i)
After=network-online.target

[Service]
ExecStart=$RUNNER_EPHEMERAL_ROOT/%i/run.sh
User=$invoking_user
WorkingDirectory=$RUNNER_EPHEMERAL_ROOT/%i
KillMode=control-group
KillSignal=SIGTERM
TimeoutStopSec=2min

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$ephemeral_template_tmp"

echo "[AUTH] instalando autorizacao de runtime do RunnerOps para user=$invoking_user"
as_root install -d -o root -g root -m 0755 "$(dirname "$TARGET_HELPER")"
as_root install -o root -g root -m 0755 "$SOURCE_HELPER" "$TARGET_HELPER"
as_root install -o root -g root -m 0644 "$template_tmp" "$template_path"
as_root install -o root -g root -m 0644 "$ephemeral_template_tmp" "$ephemeral_template_path"

if [[ "$(id -u)" -eq 0 ]]; then
  visudo -cf "$tmp" >/dev/null
  install -o root -g root -m 0440 "$tmp" "$sudoers_file"
  visudo -cf "$sudoers_file" >/dev/null
  systemctl daemon-reload
else
  sudo visudo -cf "$tmp" >/dev/null
  sudo install -o root -g root -m 0440 "$tmp" "$sudoers_file"
  sudo visudo -cf "$sudoers_file" >/dev/null
  sudo systemctl daemon-reload
fi

if [[ "$(id -u)" -eq 0 ]]; then
  "$TARGET_HELPER" check
else
  sudo -n "$TARGET_HELPER" check
  sudo -n true >/dev/null
fi
systemctl cat "$template_unit" >/dev/null
systemctl cat "$ephemeral_template_unit" >/dev/null

echo "[OK] runtime_privileges=non-interactive"
echo "[OK] helper=$TARGET_HELPER"
echo "[OK] sudoers=$sudoers_file"
echo "[OK] autoscale_service_template=$template_unit"
echo "[OK] ephemeral_service_template=$ephemeral_template_unit"
