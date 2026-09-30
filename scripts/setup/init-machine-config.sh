#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNNEROPS_PLATFORM_HOME="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}/actions-runners"
TARGET_CONFIG="${RUNNERS_CONFIG_TARGET:-$CONFIG_HOME/runners.conf}"
ENV_FILE="${ACTIONS_RUNNERS_ENV_TARGET:-$CONFIG_HOME/config.env}"
BOOT_POLICY="${RUNNER_BOOT_POLICY:-on-demand}"
DATA_ROOT="${RUNNER_DATA_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/actions-runners/runners}"
CACHE_ROOT="${RUNNER_CACHE_ROOT:-${XDG_CACHE_HOME:-$HOME/.cache}/actions-runners}"
STATE_ROOT="${RUNNER_STATE_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/actions-runners}"
EPHEMERAL_ROOT="${RUNNER_EPHEMERAL_ROOT:-$DATA_ROOT/.ephemeral}"

die() {
  echo "ERRO: $*" >&2
  exit 1
}

[[ "$BOOT_POLICY" == "on-demand" || "$BOOT_POLICY" == "auto" ]] ||
  die "RUNNER_BOOT_POLICY invalido: $BOOT_POLICY"

mkdir -p "$CONFIG_HOME" "$(dirname "$TARGET_CONFIG")" "$DATA_ROOT" "$CACHE_ROOT" "$STATE_ROOT" "$EPHEMERAL_ROOT"

# Machine-local state may contain audit evidence and diagnostics. Keep the
# canonical state root private even when mkdir inherited a permissive umask,
# and tighten existing installs so the autoscale audit writer can use the same
# path safely.
chmod 700 "$STATE_ROOT" || die "não foi possível proteger RUNNER_STATE_ROOT: $STATE_ROOT"
chmod 700 "$EPHEMERAL_ROOT" || die "não foi possível proteger RUNNER_EPHEMERAL_ROOT: $EPHEMERAL_ROOT"

if [[ -f "$TARGET_CONFIG" ]]; then
  echo "[KEEP] registry local ja existe: $TARGET_CONFIG"
else
  cp "$RUNNEROPS_PLATFORM_HOME/runners.conf.example" "$TARGET_CONFIG"
  chmod 600 "$TARGET_CONFIG"
  echo "[OK] registry local criado a partir do exemplo: $TARGET_CONFIG"
fi

cat > "$ENV_FILE" <<EOF
ACTIONS_RUNNERS_HOME="$RUNNEROPS_PLATFORM_HOME"
RUNNERS_CONFIG="$TARGET_CONFIG"
RUNNER_DATA_ROOT="$DATA_ROOT"
RUNNER_CACHE_ROOT="$CACHE_ROOT"
RUNNER_STATE_ROOT="$STATE_ROOT"
RUNNER_EPHEMERAL_ROOT="$EPHEMERAL_ROOT"
RUNNER_BOOT_POLICY="$BOOT_POLICY"
EOF
chmod 600 "$ENV_FILE"

echo "[OK] config env: $ENV_FILE"
echo
if [[ -x "$SCRIPT_DIR/sync-local-git-excludes.sh" ]]; then
  "$SCRIPT_DIR/sync-local-git-excludes.sh" || true
fi

echo "Próximos passos:"
echo "  runnerctl list"
echo "  runnerctl health all"
echo "  runnerctl plan all"
