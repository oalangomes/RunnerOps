#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

sudoers_file="/etc/sudoers.d/runnerops-${invoking_user}"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

[[ -f "$SOURCE_HELPER" ]] || die "helper ausente: $SOURCE_HELPER"
command -v sudo >/dev/null 2>&1 || [[ "$(id -u)" -eq 0 ]] ||
  die "sudo e obrigatorio para instalar a autorizacao de runtime"

printf '%s ALL=(root) NOPASSWD: %s\n' "$invoking_user" "$TARGET_HELPER" > "$tmp"
chmod 0440 "$tmp"

echo "[AUTH] instalando autorizacao de runtime do RunnerOps para user=$invoking_user"
as_root install -d -o root -g root -m 0755 "$(dirname "$TARGET_HELPER")"
as_root install -o root -g root -m 0755 "$SOURCE_HELPER" "$TARGET_HELPER"

if [[ "$(id -u)" -eq 0 ]]; then
  visudo -cf "$tmp" >/dev/null
  install -o root -g root -m 0440 "$tmp" "$sudoers_file"
  visudo -cf "$sudoers_file" >/dev/null
else
  sudo visudo -cf "$tmp" >/dev/null
  sudo install -o root -g root -m 0440 "$tmp" "$sudoers_file"
  sudo visudo -cf "$sudoers_file" >/dev/null
fi

if [[ "$(id -u)" -eq 0 ]]; then
  "$TARGET_HELPER" check
else
  sudo -n "$TARGET_HELPER" check
fi

echo "[OK] runtime_privileges=non-interactive"
echo "[OK] helper=$TARGET_HELPER"
echo "[OK] sudoers=$sudoers_file"
