#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_CONFIG="${RUNNERS_CONFIG_TARGET:-${XDG_CONFIG_HOME:-$HOME/.config}/actions-runners/runners.conf}"
ENV_FILE="${ACTIONS_RUNNERS_ENV:-$BASE_DIR/.env.local}"
BOOT_POLICY="${RUNNER_BOOT_POLICY:-on-demand}"
DATA_ROOT="${RUNNER_DATA_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/actions-runners/runners}"

die() {
  echo "ERRO: $*" >&2
  exit 1
}

[[ "$BOOT_POLICY" == "on-demand" || "$BOOT_POLICY" == "auto" ]] ||
  die "RUNNER_BOOT_POLICY invalido: $BOOT_POLICY"

mkdir -p "$(dirname "$TARGET_CONFIG")"

if [[ -f "$TARGET_CONFIG" ]]; then
  echo "[KEEP] registry local ja existe: $TARGET_CONFIG"
else
  cp "$BASE_DIR/runners.conf.example" "$TARGET_CONFIG"
  chmod 600 "$TARGET_CONFIG"
  echo "[OK] registry local criado a partir do exemplo: $TARGET_CONFIG"
fi

cat > "$ENV_FILE" <<EOF
ACTIONS_RUNNERS_HOME="$BASE_DIR"
RUNNERS_CONFIG="$TARGET_CONFIG"
RUNNER_DATA_ROOT="$DATA_ROOT"
RUNNER_BOOT_POLICY="$BOOT_POLICY"
EOF
chmod 600 "$ENV_FILE"

echo "[OK] env local: $ENV_FILE"
echo
if [[ -x "$BASE_DIR/sync-local-git-excludes.sh" ]]; then
  "$BASE_DIR/sync-local-git-excludes.sh" || true
fi

echo "Próximos passos:"
echo "  ./runners.sh list"
echo "  ./runners.sh health all"
echo "  ./runner-services.sh plan all"
