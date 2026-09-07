#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}/actions-runners"
BIN_DIR="${RUNNERCTL_BIN_DIR:-$HOME/.local/bin}"
TARGET="$BIN_DIR/runnerctl"

mkdir -p "$CONFIG_HOME" "$BIN_DIR"
printf '%s\n' "$BASE_DIR" > "$CONFIG_HOME/platform-home"
chmod 600 "$CONFIG_HOME/platform-home"

install -m 0755 "$BASE_DIR/runnerctl" "$TARGET"

echo "[OK] runnerctl instalado em: $TARGET"
echo "[OK] platform home: $BASE_DIR"
echo "[OK] config: $CONFIG_HOME/platform-home"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    echo "[WARN] $BIN_DIR não está no PATH desta shell."
    echo "       adicione ao shell profile: export PATH=\"$BIN_DIR:\$PATH\""
    ;;
esac

if [[ ! -f "${RUNNERS_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/actions-runners/runners.conf}" ]]; then
  echo
  echo "Próximo passo:"
  echo "  runnerctl init"
fi
