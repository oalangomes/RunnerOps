#!/usr/bin/env bash

# Shared machine-local runtime configuration for the actions-runners scripts.
# This file is versioned; machine-specific values are not.
RUNNER_RUNTIME_BASE_DIR="${BASE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
RUNNER_CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}/actions-runners"

if [[ -z "${ACTIONS_RUNNERS_ENV:-}" ]]; then
  if [[ -f "$RUNNER_CONFIG_HOME/config.env" ]]; then
    ACTIONS_RUNNERS_ENV="$RUNNER_CONFIG_HOME/config.env"
  elif [[ -f "$RUNNER_RUNTIME_BASE_DIR/.env.local" ]]; then
    # Backward-compatible read path for installations created before XDG config.
    ACTIONS_RUNNERS_ENV="$RUNNER_RUNTIME_BASE_DIR/.env.local"
  else
    ACTIONS_RUNNERS_ENV="$RUNNER_CONFIG_HOME/config.env"
  fi
fi

if [[ -f "$ACTIONS_RUNNERS_ENV" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ACTIONS_RUNNERS_ENV"
  set +a
fi

ACTIONS_RUNNERS_HOME="${ACTIONS_RUNNERS_HOME:-$RUNNER_RUNTIME_BASE_DIR}"

RUNNERS_CONFIG="${RUNNERS_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/actions-runners/runners.conf}"

RUNNER_DATA_ROOT="${RUNNER_DATA_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/actions-runners/runners}"
RUNNER_CACHE_ROOT="${RUNNER_CACHE_ROOT:-${XDG_CACHE_HOME:-$HOME/.cache}/actions-runners}"
RUNNER_STATE_ROOT="${RUNNER_STATE_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/actions-runners}"
RUNNER_BOOT_POLICY="${RUNNER_BOOT_POLICY:-on-demand}"

# Managed autoscale snapshots are written by `runnerctl autoscale enable` and
# intentionally load after general machine config so scheduled ticks reconstruct
# the exact normalized policy captured at enable time.
if [[ -n "${RUNNEROPS_AUTOSCALE_POLICY_FILE:-}" ]]; then
  case "$RUNNEROPS_AUTOSCALE_POLICY_FILE" in
    "$RUNNER_STATE_ROOT"/autoscale-scheduler/*.env) ;;
    *)
      echo "ERRO: RUNNEROPS_AUTOSCALE_POLICY_FILE fora de RUNNER_STATE_ROOT" >&2
      return 1 2>/dev/null || exit 1
      ;;
  esac
  [[ -f "$RUNNEROPS_AUTOSCALE_POLICY_FILE" && ! -L "$RUNNEROPS_AUTOSCALE_POLICY_FILE" ]] || {
    echo "ERRO: autoscale scheduler policy ausente ou insegura: $RUNNEROPS_AUTOSCALE_POLICY_FILE" >&2
    return 1 2>/dev/null || exit 1
  }
  set -a
  # shellcheck source=/dev/null
  source "$RUNNEROPS_AUTOSCALE_POLICY_FILE"
  set +a
fi

case "$RUNNER_BOOT_POLICY" in
  on-demand|auto) ;;
  *)
    echo "ERRO: RUNNER_BOOT_POLICY invalido: $RUNNER_BOOT_POLICY (use on-demand ou auto)" >&2
    return 1 2>/dev/null || exit 1
    ;;
esac

export ACTIONS_RUNNERS_ENV ACTIONS_RUNNERS_HOME RUNNERS_CONFIG RUNNER_DATA_ROOT RUNNER_CACHE_ROOT RUNNER_STATE_ROOT RUNNER_BOOT_POLICY RUNNEROPS_AUTOSCALE_POLICY_FILE
