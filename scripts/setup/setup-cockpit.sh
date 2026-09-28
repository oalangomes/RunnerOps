#!/usr/bin/env bash
set -euo pipefail

action="${1:-status}"

die() {
  echo "ERRO: $*" >&2
  exit 1
}

is_wsl() {
  grep -qi microsoft /proc/version 2>/dev/null
}

systemd_ready() {
  command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]
}

install_cockpit() {
  systemd_ready || {
    if is_wsl; then
      cat >&2 <<'EOF'
ERRO: systemd nao esta ativo no WSL.

No WSL, habilite:
  /etc/wsl.conf

[boot]
systemd=true

Depois execute no PowerShell:
  wsl --shutdown

Abra novamente a distro e rode este script.
EOF
      exit 1
    fi
    die "systemd nao esta ativo neste host"
  }

  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y cockpit
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y cockpit
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y cockpit
  else
    die "gerenciador de pacotes nao suportado automaticamente; instale Cockpit manualmente"
  fi

  sudo systemctl enable --now cockpit.socket
  echo
  echo "Cockpit instalado e cockpit.socket habilitado."
  echo "Acesse: https://127.0.0.1:9090"
  echo "Use seu usuario Linux e eleve privilegios apenas quando necessario."
  echo "Nao exponha a porta 9090 diretamente na internet."
}

status_cockpit() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl: indisponivel"
    return 1
  fi

  echo "systemd: $(systemctl is-system-running 2>/dev/null || true)"
  echo "cockpit.socket: $(systemctl is-active cockpit.socket 2>/dev/null || echo 'nao instalado/inativo')"
  if systemctl cat cockpit.socket >/dev/null 2>&1; then
    echo "URL local esperada: https://127.0.0.1:9090"
  fi
}

case "$action" in
  install)
    install_cockpit
    ;;
  status)
    status_cockpit
    ;;
  *)
    echo "Uso: ./setup-cockpit.sh [install|status]"
    exit 1
    ;;
esac
