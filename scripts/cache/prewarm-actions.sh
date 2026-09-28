#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$BASE_DIR/runner-runtime-env.sh"
CONFIG_PATH="$RUNNERS_CONFIG"
FORCE=0
TARGET="all"
ACTIONS=(
  "actions/checkout@v7"
  "actions/setup-node@v6"
  "actions/setup-python@v6"
  "actions/upload-artifact@v7"
  "actions/github-script@v9"
  "actions/cache@v6"
  "DavidAnson/markdownlint-cli2-action@8de2aa07cae85fd17c0b35642db70cf5495f1d25"
  "lycheeverse/lychee-action@e7477775783ea5526144ba13e8db5eec57747ce8"
)

usage() {
  cat <<'EOF'
Uso:
  ./prewarm-actions.sh [runner|group:<grupo>|all] [opcoes]

Opcoes:
  --action owner/repo@ref   adiciona uma action para aquecer
  --only owner/repo@ref     usa apenas a action informada; pode repetir
  --force                   baixa e extrai novamente mesmo se ja existir

Exemplos:
  ./prewarm-actions.sh group:my-team
  ./prewarm-actions.sh my-api --action actions/setup-node@v6
  ./prewarm-actions.sh all --only actions/checkout@v7 --only actions/setup-node@v6
EOF
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

read_config() {
  [[ -f "$CONFIG_PATH" ]] || die "arquivo de configuracao nao encontrado: $CONFIG_PATH"

  RUNNER_NAMES=()
  RUNNER_PATHS=()
  RUNNER_GROUPS=()
  RUNNER_ENABLED=()

  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    local line name path _profile repo enabled group
    line="${raw_line//$'\r'/}"
    line="$(trim "$line")"

    [[ -z "$line" || "${line:0:1}" == "#" ]] && continue
    [[ "$line" == *"|"* ]] || die "linha invalida no runners.conf: $line"

    IFS='|' read -r name path _profile repo enabled group _ <<< "$line"
    name="$(trim "${name:-}")"
    path="$(trim "${path:-}")"
    repo="$(trim "${repo:-}")"
    enabled="$(trim "${enabled:-true}")"
    group="$(trim "${group:-}")"
    [[ -n "$group" ]] || group="$(infer_group "$name" "$repo")"
    group="$(normalize_slug "$group")"

    [[ -n "$name" && -n "$path" ]] || continue
    case "${enabled,,}" in
      true|1|yes|y|sim) enabled="true" ;;
      *) enabled="false" ;;
    esac

    RUNNER_NAMES+=("$name")
    RUNNER_PATHS+=("$path")
    RUNNER_GROUPS+=("$group")
    RUNNER_ENABLED+=("$enabled")
  done < "$CONFIG_PATH"
}

target_indexes() {
  local target="$1"
  local group_target i matched=0

  if [[ "$target" == "all" ]]; then
    for i in "${!RUNNER_NAMES[@]}"; do
      printf '%s\n' "$i"
    done
    return 0
  fi

  if [[ "$target" == group:* ]]; then
    group_target="$(normalize_slug "${target#group:}")"
    for i in "${!RUNNER_NAMES[@]}"; do
      if [[ "${RUNNER_GROUPS[$i]}" == "$group_target" ]]; then
        printf '%s\n' "$i"
        matched=1
      fi
    done
    [[ "$matched" -eq 1 ]] || die "grupo desconhecido ou vazio: $group_target"
    return 0
  fi

  for i in "${!RUNNER_NAMES[@]}"; do
    if [[ "${RUNNER_NAMES[$i]}" == "$target" ]]; then
      printf '%s\n' "$i"
      return 0
    fi
  done
  die "runner desconhecido: $target"
}

parse_action() {
  local spec="$1"
  [[ "$spec" == *@* && "$spec" == */* ]] || die "action invalida: $spec"
  ACTION_OWNER="${spec%%/*}"
  local rest="${spec#*/}"
  ACTION_REPO="${rest%@*}"
  ACTION_REF="${rest##*@}"
  [[ -n "$ACTION_OWNER" && -n "$ACTION_REPO" && -n "$ACTION_REF" ]] || die "action invalida: $spec"
}

download_action() {
  local runner_name="$1"
  local runner_path="$2"
  local spec="$3"
  local actions_root temp_dir tarball dest url

  parse_action "$spec"
  actions_root="$runner_path/_work/_actions"
  dest="$actions_root/$ACTION_OWNER/$ACTION_REPO/$ACTION_REF"

  if [[ "$FORCE" -eq 0 ]] && { [[ -f "$dest/action.yml" ]] || [[ -f "$dest/action.yaml" ]]; }; then
    echo "[OK] $runner_name $spec ja aquecida em $dest"
    return 0
  fi

  if ! command -v curl >/dev/null 2>&1 || ! command -v tar >/dev/null 2>&1; then
    die "curl e tar sao obrigatorios para prewarm de actions"
  fi

  temp_dir="$(mktemp -d)"
  tarball="$temp_dir/action.tar.gz"
  url="https://codeload.github.com/$ACTION_OWNER/$ACTION_REPO/tar.gz/$ACTION_REF"

  echo "[GET] $runner_name $spec"
  mkdir -p "$dest"
  curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 20 --max-time 180 "$url" -o "$tarball"
  tar -xzf "$tarball" -C "$dest" --strip-components=1
  rm -rf "$temp_dir"

  if [[ ! -f "$dest/action.yml" && ! -f "$dest/action.yaml" ]]; then
    echo "[WARN] $runner_name $spec extraida, mas action.yml/action.yaml nao encontrado em $dest"
  else
    echo "[OK] $runner_name $spec aquecida em $dest"
  fi
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" || "${1:-}" == "help" ]]; then
  usage
  exit 0
fi

if [[ $# -gt 0 && "$1" != --* ]]; then
  TARGET="$1"
  shift
fi

ONLY_ACTIONS=()
while (($#)); do
  case "$1" in
    --action)
      [[ -n "${2:-}" ]] || die "--action exige owner/repo@ref"
      ACTIONS+=("$2")
      shift 2
      ;;
    --only)
      [[ -n "${2:-}" ]] || die "--only exige owner/repo@ref"
      ONLY_ACTIONS+=("$2")
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    *)
      die "opcao desconhecida: $1"
      ;;
  esac
done

if [[ "${#ONLY_ACTIONS[@]}" -gt 0 ]]; then
  ACTIONS=("${ONLY_ACTIONS[@]}")
fi

read_config
mapfile -t INDEXES < <(target_indexes "$TARGET")

for i in "${INDEXES[@]}"; do
  if [[ "${RUNNER_ENABLED[$i]}" != "true" ]]; then
    echo "[SKIP] ${RUNNER_NAMES[$i]} desabilitado"
    continue
  fi
  if [[ ! -d "${RUNNER_PATHS[$i]}" ]]; then
    echo "[SKIP] ${RUNNER_NAMES[$i]} diretorio nao existe: ${RUNNER_PATHS[$i]}"
    continue
  fi
  for action in "${ACTIONS[@]}"; do
    download_action "${RUNNER_NAMES[$i]}" "${RUNNER_PATHS[$i]}" "$action"
  done
done
