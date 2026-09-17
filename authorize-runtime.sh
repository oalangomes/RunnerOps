#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$BASE_DIR/runner-runtime-env.sh"
SOURCE_HELPER="$BASE_DIR/runnerops-systemctl"
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
[[ "$RUNNER_DATA_ROOT" != *$'\n'* && "$RUNNER_DATA_ROOT" != *$'\r'* ]] ||
  die "RUNNER_DATA_ROOT contém newline não suportado"
[[ "$RUNNER_STATE_ROOT" != *$'\n'* && "$RUNNER_STATE_ROOT" != *$'\r'* ]] ||
  die "RUNNER_STATE_ROOT contém newline não suportado"

template_unit="actions.runner.runnerops-${invoking_user}@.service"
template_path="/etc/systemd/system/$template_unit"

template_tmp="$(mktemp)"
sudoers_file="/etc/sudoers.d/runnerops-${invoking_user}"
tmp="$(mktemp)"
trap 'rm -f "$tmp" "$template_tmp"' EXIT

[[ -f "$SOURCE_HELPER" ]] || die "helper ausente: $SOURCE_HELPER"
command -v sudo >/dev/null 2>&1 || [[ "$(id -u)" -eq 0 ]] ||
  die "sudo e obrigatorio para instalar a autorizacao de runtime"

printf '%s ALL=(root) NOPASSWD: %s\n' "$invoking_user" "$TARGET_HELPER" > "$tmp"
chmod 0440 "$tmp"

cat > "$template_tmp" <<EOF
[Unit]
Description=RunnerOps GitHub Actions Runner (%i)
After=network-online.target

[Service]
ExecStart=$RUNNER_DATA_ROOT/%i/runsvc.sh
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

echo "[AUTH] instalando autorizacao de runtime do RunnerOps para user=$invoking_user"
as_root install -d -o root -g root -m 0755 "$(dirname "$TARGET_HELPER")"
as_root install -o root -g root -m 0755 "$SOURCE_HELPER" "$TARGET_HELPER"
as_root install -o root -g root -m 0644 "$template_tmp" "$template_path"

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
fi
systemctl cat "$template_unit" >/dev/null

echo "[OK] runtime_privileges=non-interactive"
echo "[OK] helper=$TARGET_HELPER"
echo "[OK] sudoers=$sudoers_file"
echo "[OK] autoscale_service_template=$template_unit"
