#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$BASE_DIR/runner-runtime-env.sh"
CONFIG_PATH="$RUNNERS_CONFIG"
BOOT_POLICY="$RUNNER_BOOT_POLICY"
CACHE_ENV_PATH="$BASE_DIR/runner-cache-env.sh"
SERVICE_ENV_DIR="$BASE_DIR/.runner-service-env"
SERVICE_USER="${RUNNER_SERVICE_USER:-${SUDO_USER:-$USER}}"
LOG_LINES="${RUNNER_SERVICE_LOG_LINES:-200}"

usage() {
  cat <<'USAGE'
Uso:
  ./runner-services.sh <acao> [runner|group:<grupo>|all]

Acoes:
  list       lista runners e units systemd
  plan       mostra o que seria migrado/reativado sem alterar nada
  doctor     valida systemd e arquivos do runner
  migrate    instala svc.sh, preserva cache e aplica RUNNER_BOOT_POLICY
  on-demand  desabilita autostart e para runner(s)
  autostart  habilita autostart e inicia runner(s)
  uninstall  remove apenas o servico systemd
  start      inicia runner(s) ja migrado(s)
  stop       para runner(s) ja migrado(s)
  restart    reinicia runner(s) ja migrado(s)
  status     mostra estado do servico
  logs       mostra journal do servico

Exemplos:
  ./runner-services.sh list
  ./runner-services.sh plan all
  ./runner-services.sh migrate my-api
  ./runner-services.sh migrate group:my-team
  ./runner-services.sh on-demand all
  ./runner-services.sh autostart my-api
  ./runner-services.sh status all
USAGE
}

die() {
  echo "ERRO: $*" >&2
  exit 1
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

normalize_slug() {
  local value="${1,,}"
  value="$(printf '%s' "$value" | tr -c 'a-z0-9._-' '-')"
  value="${value#-}"
  value="${value%-}"
  [[ -n "$value" ]] || value="generic"
  printf '%s\n' "$value"
}

infer_group() {
  local name="$1"
  local repo="${2:-}"

  if [[ -n "$repo" ]]; then
    normalize_slug "${repo##*/}"
  else
    normalize_slug "$name"
  fi
}

require_systemd() {
  command -v systemctl >/dev/null 2>&1 || die "systemctl nao encontrado"
  [[ -d /run/systemd/system ]] || die "systemd nao esta ativo; no WSL habilite systemd em /etc/wsl.conf"
}

service_unit() {
  local path="$1"
  local unit working_dir

  if [[ -f "$path/.service" ]]; then
    unit="$(tr -d '[:space:]' < "$path/.service")"
    [[ -n "$unit" ]] && {
      printf '%s\n' "$unit"
      return 0
    }
  fi

  if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
    while read -r unit; do
      [[ -n "$unit" ]] || continue
      working_dir="$(systemctl show "$unit" --property=WorkingDirectory --value 2>/dev/null || true)"
      if [[ "$working_dir" == "$path" ]]; then
        printf '%s\n' "$unit"
        return 0
      fi
    done < <(
      systemctl list-unit-files 'actions.runner.*.service' --no-legend --no-pager 2>/dev/null |
        awk '{print $1}'
    )
  fi

  return 1
}

matches_target() {
  local target="$1" name="$2" group="$3"
  [[ "$target" == all || "$target" == "$name" || "$target" == "group:$group" ]]
}

registration_deleted() {
  local unit="$1"
  local log deleted_line healthy_line

  log="$(journalctl -u "$unit" -n 120 --no-pager 2>/dev/null || true)"
  [[ -n "$log" ]] || return 1

  deleted_line="$(
    printf '%s\n' "$log" |
      grep -nF 'runner registration has been deleted from the server' |
      tail -n 1 |
      cut -d: -f1 || true
  )"
  [[ -n "$deleted_line" ]] || return 1

  healthy_line="$(
    printf '%s\n' "$log" |
      grep -nE 'Listening for Jobs|Runner reconnected' |
      tail -n 1 |
      cut -d: -f1 || true
  )"

  [[ -z "$healthy_line" || "$deleted_line" -gt "$healthy_line" ]]
}

escape_env() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s' "$value"
}

render_cache_environment() {
  local name="$1" path="$2" profile="$3" repo="$4" group="$5"
  local env_file="$SERVICE_ENV_DIR/$name.env"
  local tmp_env tmp_path
  local -a keys=(
    RUNNER_CACHE_ROOT RUNNER_CACHE_PROFILE RUNNER_SHARED_CACHE_ROOT
    RUNNER_STACK_CACHE_ROOT RUNNER_TOOLS_CACHE_ROOT XDG_CACHE_HOME
    RUNNER_TOOL_CACHE AGENT_TOOLSDIRECTORY npm_config_cache NPM_CONFIG_CACHE
    npm_config_prefix NPM_CONFIG_PREFIX npm_config_prefer_offline
    NPM_CONFIG_PREFER_OFFLINE npm_config_audit NPM_CONFIG_AUDIT
    npm_config_fund NPM_CONFIG_FUND COREPACK_HOME PNPM_HOME PNPM_STORE_PATH
    YARN_CACHE_FOLDER GRADLE_USER_HOME MAVEN_OPTS PIP_CACHE_DIR PIPX_HOME
    PIPX_BIN_DIR PUB_CACHE CARGO_HOME GOPATH GOMODCACHE GOCACHE
    DOTNET_CLI_HOME NUGET_PACKAGES COMPOSER_CACHE_DIR PLAYWRIGHT_BROWSERS_PATH
  )

  [[ -f "$CACHE_ENV_PATH" ]] || return 0
  mkdir -p "$SERVICE_ENV_DIR"
  tmp_env="$(mktemp)"
  tmp_path="$(mktemp)"

  (
    export LOCAL_RUNNER_NAME="$name"
    export LOCAL_RUNNER_PROFILE="$profile"
    export LOCAL_RUNNER_REPO="$repo"
    export LOCAL_RUNNER_GROUP="$group"
    # shellcheck source=/dev/null
    source "$CACHE_ENV_PATH"

    local key value
    for key in "${keys[@]}"; do
      value="${!key:-}"
      [[ -n "$value" ]] || continue
      printf '%s="%s"\n' "$key" "$(escape_env "$value")"
    done
    printf '%s\n' "$PATH" > "$tmp_path"
  ) > "$tmp_env"

  install -m 0600 "$tmp_env" "$env_file"
  install -m 0644 "$tmp_path" "$path/.path"
  rm -f "$tmp_env" "$tmp_path"
}

install_cache_dropin() {
  local name="$1" path="$2" profile="$3" repo="$4" group="$5"
  local unit env_file tmp

  render_cache_environment "$name" "$path" "$profile" "$repo" "$group"
  env_file="$SERVICE_ENV_DIR/$name.env"
  [[ -f "$env_file" ]] || return 0

  unit="$(service_unit "$path")"
  tmp="$(mktemp)"
  cat > "$tmp" <<EOF
[Service]
EnvironmentFile=$env_file
EOF
  sudo mkdir -p "/etc/systemd/system/$unit.d"
  sudo install -m 0644 "$tmp" "/etc/systemd/system/$unit.d/10-actions-runners-cache.conf"
  rm -f "$tmp"
  sudo systemctl daemon-reload
}

migrate_runner() {
  local name="$1" path="$2" profile="$3" repo="$4" enabled="$5" group="$6"
  local unit

  [[ "$enabled" == true ]] || {
    echo "[SKIP] $name desabilitado"
    return 0
  }
  [[ -d "$path" ]] || die "$name: pasta ausente: $path"
  [[ -x "$path/svc.sh" ]] || die "$name: svc.sh ausente"
  [[ -f "$path/.runner" ]] || die "$name: .runner ausente"

  if [[ -x "$BASE_DIR/runners.sh" ]]; then
    "$BASE_DIR/runners.sh" stop "$name" || true
  fi

  if unit="$(service_unit "$path" 2>/dev/null)"; then
    if registration_deleted "$unit"; then
      die "$name: registro remoto do GitHub foi deletado; reconfigure o runner antes de migrar/iniciar"
    fi
  else
    echo "[MIGRATE] instalando $name como usuario $SERVICE_USER"
    (cd "$path" && sudo ./svc.sh install "$SERVICE_USER")
    unit="$(service_unit "$path")"
  fi

  install_cache_dropin "$name" "$path" "$profile" "$repo" "$group"

  if [[ "$BOOT_POLICY" == "on-demand" ]]; then
    sudo systemctl disable "$unit" >/dev/null 2>&1 || true
    sudo systemctl start "$unit"
    sleep "${RUNNER_SYSTEMD_START_SETTLE_SECONDS:-3}"
    if ! systemctl is-active --quiet "$unit"; then
      sudo systemctl status "$unit" --no-pager || true
      die "$name nao permaneceu ativo durante validacao on-demand"
    fi
    sudo systemctl stop "$unit"
    echo "[OK] $name -> $unit policy=on-demand boot=disabled state=idle"
  else
    sudo systemctl enable --now "$unit" >/dev/null
    if systemctl is-active --quiet "$unit"; then
      echo "[OK] $name -> $unit policy=auto"
    else
      sudo systemctl status "$unit" --no-pager || true
      die "$name nao ficou ativo"
    fi
  fi
}

set_boot_policy_runner() {
  local mode="$1" name="$2" path="$3"
  local unit

  unit="$(service_unit "$path" 2>/dev/null || true)"
  [[ -n "$unit" ]] || die "$name ainda nao foi migrado"

  case "$mode" in
    on-demand)
      sudo systemctl disable "$unit" >/dev/null 2>&1 || true
      sudo systemctl stop "$unit" >/dev/null 2>&1 || true
      echo "[OK] $name policy=on-demand state=idle boot=$(systemctl is-enabled "$unit" 2>/dev/null || true)"
      ;;
    autostart)
      sudo systemctl enable --now "$unit" >/dev/null
      sleep "${RUNNER_SYSTEMD_START_SETTLE_SECONDS:-3}"
      if systemctl is-active --quiet "$unit"; then
        echo "[OK] $name policy=auto state=active boot=enabled"
      else
        sudo systemctl status "$unit" --no-pager || true
        return 1
      fi
      ;;
  esac
}

uninstall_runner() {
  local name="$1" path="$2"
  local unit
  unit="$(service_unit "$path" 2>/dev/null || true)"
  [[ -n "$unit" ]] || {
    echo "[SKIP] $name ainda esta em modo legado"
    return 0
  }

  sudo systemctl stop "$unit" 2>/dev/null || true
  sudo systemctl disable "$unit" 2>/dev/null || true
  (cd "$path" && sudo ./svc.sh uninstall)
  rm -f "$SERVICE_ENV_DIR/$name.env"
  echo "[OK] $name removido do systemd; runner GitHub preservado"
}

operate_runner() {
  local action="$1" name="$2" path="$3"
  local unit
  unit="$(service_unit "$path" 2>/dev/null || true)"
  [[ -n "$unit" ]] || die "$name ainda nao foi migrado"

  case "$action" in
    start)
      sudo systemctl start "$unit"
      sleep "${RUNNER_SYSTEMD_START_SETTLE_SECONDS:-3}"
      if systemctl is-active --quiet "$unit"; then
        echo "[OK] $name state=active policy=$BOOT_POLICY unit=$unit"
      else
        echo "[ERR] $name nao permaneceu ativo unit=$unit" >&2
        return 1
      fi
      ;;
    stop|restart)
      sudo systemctl "$action" "$unit"
      ;;
    status)
      printf '%-24s %-12s %-10s %s\n'         "$name"         "$(systemctl is-active "$unit" 2>/dev/null || true)"         "$(systemctl is-enabled "$unit" 2>/dev/null || true)"         "$unit"
      ;;
    logs)
      sudo journalctl -u "$unit" -n "$LOG_LINES" --no-pager
      ;;
  esac
}

plan_runner() {
  local name="$1" path="$2" profile="$3" repo="$4" enabled="$5" group="$6"
  local unit state boot action reason

  if [[ "$enabled" != true ]]; then
    printf '%-24s %-14s %-12s %-12s %-14s %s\n' "$name" "$group" "$profile" "disabled" "-" "skip"
    return 0
  fi

  unit="$(service_unit "$path" 2>/dev/null || true)"
  if [[ -n "$unit" ]]; then
    state="$(systemctl is-active "$unit" 2>/dev/null || true)"
    boot="$(systemctl is-enabled "$unit" 2>/dev/null || true)"
    action="none"
    if [[ "$state" != "active" ]] && registration_deleted "$unit"; then
      action="reconfigure"
    elif [[ "$BOOT_POLICY" == "on-demand" ]]; then
      [[ "$boot" == "enabled" ]] && action="disable-boot"
      [[ "$state" == "failed" ]] && action="repair"
    elif [[ "$state" != "active" || "$boot" != "enabled" ]]; then
      action="repair/start"
    fi
    printf '%-24s %-14s %-12s %-12s %-14s %s\n' "$name" "$group" "$profile" "systemd:$state" "$boot" "$action"
    return 0
  fi

  reason=""
  [[ -d "$path" ]] || reason="missing-dir"
  [[ -n "$reason" || -x "$path/svc.sh" ]] || reason="missing-svc.sh"
  [[ -n "$reason" || -f "$path/.runner" ]] || reason="missing-.runner"

  if [[ -n "$reason" ]]; then
    printf '%-24s %-14s %-12s %-12s %-14s %s\n' "$name" "$group" "$profile" "legacy" "-" "blocked:$reason"
  else
    printf '%-24s %-14s %-12s %-12s %-14s %s\n' "$name" "$group" "$profile" "legacy" "-" "migrate"
  fi
}

doctor_runner() {
  local name="$1" path="$2"
  local unit
  printf '%-24s ' "$name"

  if [[ ! -d "$path" ]]; then
    echo "ERRO pasta ausente"
    return 1
  fi
  if [[ ! -x "$path/svc.sh" || ! -f "$path/.runner" ]]; then
    echo "ERRO runner incompleto"
    return 1
  fi

  unit="$(service_unit "$path" 2>/dev/null || true)"
  if [[ -n "$unit" ]]; then
    if registration_deleted "$unit"; then
      echo "ERRO systemd=$unit registration=deleted action=reconfigure"
      return 1
    fi
    echo "OK systemd=$unit state=$(systemctl is-active "$unit" 2>/dev/null || true) boot=$(systemctl is-enabled "$unit" 2>/dev/null || true) policy=$BOOT_POLICY"
  else
    echo "OK legacy"
  fi
}

process_config() {
  local action="$1" target="$2"
  local raw name path profile repo enabled group rest matched=0 failures=0

  while IFS= read -r raw || [[ -n "$raw" ]]; do
    raw="$(trim "${raw//$'\r'/}")"
    [[ -z "$raw" || "${raw:0:1}" == "#" ]] && continue

    IFS='|' read -r name path profile repo enabled group rest <<< "$raw"
    name="$(trim "${name:-}")"
    path="$(trim "${path:-}")"
    profile="$(trim "${profile:-generic}")"
    repo="$(trim "${repo:-}")"
    enabled="$(trim "${enabled:-true}")"
    group="$(trim "${group:-}")"
    [[ -n "$group" ]] || group="$(infer_group "$name" "$repo")"
    group="$(normalize_slug "$group")"

    matches_target "$target" "$name" "$group" || continue
    matched=1

    case "$action" in
      list)
        local unit state
        unit="$(service_unit "$path" 2>/dev/null || true)"
        state=legacy
        [[ -n "$unit" ]] && state="$(systemctl is-active "$unit" 2>/dev/null || true)"
        printf '%-24s %-14s %-12s %-10s %s\n' "$name" "$group" "$profile" "$state" "${unit:--}"
        ;;
      plan)
        plan_runner "$name" "$path" "$profile" "$repo" "$enabled" "$group"
        ;;
      doctor)
        doctor_runner "$name" "$path" || failures=$((failures + 1))
        ;;
      migrate)
        migrate_runner "$name" "$path" "$profile" "$repo" "$enabled" "$group"
        ;;
      on-demand|autostart)
        set_boot_policy_runner "$action" "$name" "$path"
        ;;
      uninstall)
        uninstall_runner "$name" "$path"
        ;;
      start|stop|restart|status|logs)
        operate_runner "$action" "$name" "$path"
        ;;
    esac
  done < "$CONFIG_PATH"

  [[ "$matched" -eq 1 ]] || die "target nao encontrado: $target"
  [[ "$failures" -eq 0 ]] || die "$failures runner(s) com problema"
}

main() {
  local action="${1:-help}"
  local target="${2:-all}"

  case "$action" in
    help|-h|--help)
      usage
      exit 0
      ;;
    list|plan|doctor|migrate|on-demand|autostart|uninstall|start|stop|restart|status|logs)
      ;;
    *)
      usage
      die "acao desconhecida: $action"
      ;;
  esac

  [[ -f "$CONFIG_PATH" ]] || die "runners.conf nao encontrado: $CONFIG_PATH"
  require_systemd

  if [[ "$action" == list ]]; then
    printf '%-24s %-14s %-12s %-10s %s\n' "RUNNER" "GROUP" "PROFILE" "STATE" "SYSTEMD UNIT"
  elif [[ "$action" == plan ]]; then
    printf '%-24s %-14s %-12s %-12s %-14s %s\n' "RUNNER" "GROUP" "PROFILE" "CURRENT" "BOOT" "PLAN"
  fi

  process_config "$action" "$target"
}

main "$@"
