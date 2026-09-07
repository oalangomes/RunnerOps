#!/usr/bin/env bash

# Shared machine-local runtime configuration for the actions-runners scripts.
# This file is versioned; machine-specific values are not.
RUNNER_RUNTIME_BASE_DIR="${BASE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
ACTIONS_RUNNERS_ENV="${ACTIONS_RUNNERS_ENV:-$RUNNER_RUNTIME_BASE_DIR/.env.local}"

if [[ -f "$ACTIONS_RUNNERS_ENV" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ACTIONS_RUNNERS_ENV"
  set +a
fi

ACTIONS_RUNNERS_HOME="${ACTIONS_RUNNERS_HOME:-$RUNNER_RUNTIME_BASE_DIR}"

RUNNERS_CONFIG="${RUNNERS_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/actions-runners/runners.conf}"

RUNNER_DATA_ROOT="${RUNNER_DATA_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/actions-runners/runners}"
RUNNER_BOOT_POLICY="${RUNNER_BOOT_POLICY:-on-demand}"

case "$RUNNER_BOOT_POLICY" in
  on-demand|auto) ;;
  *)
    echo "ERRO: RUNNER_BOOT_POLICY invalido: $RUNNER_BOOT_POLICY (use on-demand ou auto)" >&2
    return 1 2>/dev/null || exit 1
    ;;
esac

export ACTIONS_RUNNERS_ENV ACTIONS_RUNNERS_HOME RUNNERS_CONFIG RUNNER_DATA_ROOT RUNNER_BOOT_POLICY
