#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$BASE_DIR/runner-runtime-env.sh"
RUNNER_ROOT="$RUNNER_DATA_ROOT"
GITHUB_LINE=""
NAME=""
LABELS="local-runner"
RUNNER_TAR="actions-runner-linux-x64-2.335.1.tar.gz"
EXPECTED_SHA256="4ef2f25285f0ae4477f1fe1e346db76d2f3ebf03824e2ddd1973a2819bf6c8cf"
WORK_FOLDER="_work"
PROFILE="auto"
GROUP="auto"
ENABLED="true"
REPLACE=0

usage() {
  cat <<'USAGE'
Uso:
  ./configure-runner.sh --github-line "<./config.sh --url ... --token ...>" [opcoes]

Opcoes:
  --github-line VALUE   linha copiada do GitHub com --url e --token
  --name VALUE          nome base local do runner/pasta
  --labels VALUE        labels base do runner; o identificador final da instancia e adicionado automaticamente
  --profile VALUE       perfil tecnico: auto, generic, node, python, flutter, android, java, dotnet, go
  --group VALUE         grupo operacional; auto usa o slug do repositorio
  --enabled VALUE       true/false no runners.conf
  --base-dir VALUE      diretorio da instalacao/arquivos da plataforma
  --runner-root VALUE    diretorio local onde novas instancias de runner serao criadas
  --runner-tar VALUE    arquivo .tar.gz do GitHub Actions Runner
  --expected-sha256 V   checksum esperado do tarball
  --work-folder VALUE   pasta de work do runner
  --replace             recria runner existente e usa --replace no config.sh
  -h, --help            mostra ajuda

Exemplo:
  ./configure-runner.sh \
    --github-line "./config.sh --url https://github.com/example/my-api --token TOKEN" \
    --labels "python,my-api,local-runner"

Se my-api ja existir, uma nova execucao sem --replace cria my-api-2 e adiciona a label my-api-2.
USAGE
}

die() {
  echo "ERRO: $*" >&2
  exit 1
}

step() {
  printf '\n==> %s\n' "$1"
}

trim() {
  local value="$1"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s' "$value"
}

repo_name() {
  local repo_url="${1%/}"
  basename "$repo_url" | tr '[:upper:]' '[:lower:]'
}

repo_full_name() {
  local repo_url="${1%/}"
  local path_part

  path_part="${repo_url#https://github.com/}"
  path_part="${path_part#http://github.com/}"
  path_part="${path_part#git@github.com:}"
  path_part="${path_part%.git}"

  if [[ "$path_part" == "$repo_url" || "$path_part" != */* ]]; then
    printf '%s\n' ""
    return 0
  fi

  IFS='/' read -r owner repo _ <<< "$path_part"
  if [[ -n "${owner:-}" && -n "${repo:-}" ]]; then
    printf '%s/%s\n' "$owner" "$repo"
  fi
}

get_arg_value() {
  local key="$1"
  shift

  while (($#)); do
    if [[ "$1" == "$key" && $# -ge 2 ]]; then
      printf '%s\n' "$2"
      return 0
    fi
    shift
  done

  return 1
}

normalize_bool() {
  local value="${1,,}"
  case "$value" in
    true|1|yes|y|sim) echo "true" ;;
    false|0|no|n|nao|não) echo "false" ;;
    *) die "valor booleano invalido: $1" ;;
  esac
}

normalize_slug() {
  local value="${1,,}"
  value="$(printf '%s' "$value" | tr -c 'a-z0-9._-' '-')"
  value="${value#-}"
  value="${value%-}"
  [[ -n "$value" ]] || value="generic"
  printf '%s\n' "$value"
}

append_csv_label() {
  local current_labels="$1"
  local required_label="$2"
  local result=""
  local item
  local found=0
  local -a label_items=()

  IFS=',' read -r -a label_items <<< "$current_labels"
  for item in "${label_items[@]}"; do
    item="$(trim "$item")"
    [[ -n "$item" ]] || continue
    if [[ "${item,,}" == "${required_label,,}" ]]; then
      found=1
    fi
    result+="${result:+,}$item"
  done

  if [[ "$found" -eq 0 ]]; then
    result+="${result:+,}$required_label"
  fi

  printf '%s\n' "$result"
}

infer_profile() {
  local name="${1,,}"
  local labels="${2,,}"

  case "$labels,$name" in
    *flutter*|*android*) echo "flutter" ;;
    *node*|*npm*|*pnpm*|*yarn*|*web*) echo "node" ;;
    *python*|*pytest*|*pip*) echo "python" ;;
    *java*|*maven*|*gradle*) echo "java" ;;
    *dotnet*|*.net*|*nuget*) echo "dotnet" ;;
    *golang*|*go*) echo "go" ;;
    *) echo "generic" ;;
  esac
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

validate_profile() {
  case "$1" in
    auto|generic|node|python|flutter|android|java|dotnet|go) ;;
    *) die "profile invalido: $1" ;;
  esac
}

runner_name_exists() {
  local config_path="$1"
  local base_dir="$2"
  local runner_name="$3"

  if [[ -f "$config_path" ]] && awk -F '|' -v runner_name="$runner_name" '$1 == runner_name { found=1 } END { exit(found ? 0 : 1) }' "$config_path"; then
    return 0
  fi

  [[ -e "$base_dir/$runner_name" ]]
}

next_available_runner_name() {
  local config_path="$1"
  local base_dir="$2"
  local requested_name="$3"
  local candidate="$requested_name"
  local index=2

  if ! runner_name_exists "$config_path" "$base_dir" "$candidate"; then
    printf '%s\n' "$candidate"
    return 0
  fi

  while runner_name_exists "$config_path" "$base_dir" "${requested_name}-${index}"; do
    index=$((index + 1))
  done

  printf '%s\n' "${requested_name}-${index}"
}

update_runners_conf() {
  local config_path="$1"
  local runner_name="$2"
  local runner_dir="$3"
  local profile="$4"
  local repo="$5"
  local enabled="$6"
  local group="$7"
  local tmp

  mkdir -p "$(dirname "$config_path")"
  [[ -f "$config_path" ]] || printf '# name|path|profile|repo|enabled|group\n' > "$config_path"

  tmp="$(mktemp)"
  awk -F '|' -v runner_name="$runner_name" '$1 != runner_name' "$config_path" > "$tmp"
  printf '%s|%s|%s|%s|%s|%s\n' "$runner_name" "$runner_dir" "$profile" "$repo" "$enabled" "$group" >> "$tmp"
  mv "$tmp" "$config_path"
}

while (($#)); do
  case "$1" in
    --github-line)
      GITHUB_LINE="${2:-}"
      shift 2
      ;;
    --name)
      NAME="${2:-}"
      shift 2
      ;;
    --labels)
      LABELS="${2:-}"
      shift 2
      ;;
    --profile)
      PROFILE="${2:-}"
      shift 2
      ;;
    --group)
      GROUP="${2:-}"
      shift 2
      ;;
    --enabled)
      ENABLED="${2:-}"
      shift 2
      ;;
    --base-dir)
      BASE_DIR="${2:-}"
      shift 2
      ;;
    --runner-root)
      RUNNER_ROOT="${2:-}"
      shift 2
      ;;
    --runner-tar)
      RUNNER_TAR="${2:-}"
      shift 2
      ;;
    --expected-sha256)
      EXPECTED_SHA256="${2:-}"
      shift 2
      ;;
    --work-folder)
      WORK_FOLDER="${2:-}"
      shift 2
      ;;
    --replace)
      REPLACE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "opcao desconhecida: $1"
      ;;
  esac
done

[[ -n "$GITHUB_LINE" ]] || die "--github-line e obrigatorio"
validate_profile "$PROFILE"
ENABLED="$(normalize_bool "$ENABLED")"

step "Lendo linha copiada do GitHub"

clean_line="${GITHUB_LINE#"${GITHUB_LINE%%[![:space:]]*}"}"
clean_line="${clean_line#./config.sh }"
clean_line="${clean_line#config.sh }"

read -r -a parts <<< "$clean_line"
REPO_URL="$(get_arg_value --url "${parts[@]}" || true)"
TOKEN="$(get_arg_value --token "${parts[@]}" || true)"

[[ -n "$REPO_URL" ]] || die "nao encontrei --url na linha informada"
[[ -n "$TOKEN" ]] || die "nao encontrei --token na linha informada"

[[ -n "$NAME" ]] || NAME="$(repo_name "$REPO_URL")"
REQUESTED_NAME="$(normalize_slug "$NAME")"
NAME="$REQUESTED_NAME"
REPO_FULL_NAME="$(repo_full_name "$REPO_URL")"
[[ -n "$REPO_FULL_NAME" ]] || REPO_FULL_NAME="$REPO_URL"

if [[ "$PROFILE" == "auto" ]]; then
  PROFILE="$(infer_profile "$REQUESTED_NAME" "$LABELS")"
fi
if [[ "$GROUP" == "auto" || -z "$GROUP" ]]; then
  GROUP="$(infer_group "$REQUESTED_NAME" "$REPO_FULL_NAME")"
else
  GROUP="$(normalize_slug "$GROUP")"
fi

BASE_DIR="$(realpath -m "$BASE_DIR")"
RUNNER_ROOT="$(realpath -m "$RUNNER_ROOT")"
TAR_PATH="$BASE_DIR/$RUNNER_TAR"
CONFIG_PATH="$(realpath -m "$RUNNERS_CONFIG")"

if [[ "$REPLACE" -eq 0 ]]; then
  NAME="$(next_available_runner_name "$CONFIG_PATH" "$RUNNER_ROOT" "$REQUESTED_NAME")"
fi

# A label exclusiva da instancia acompanha exatamente o identificador/pasta final.
# Ex.: my-api-2 recebe automaticamente a label my-api-2.
INSTANCE_LABEL="$NAME"
LABELS="$(append_csv_label "$LABELS" "$INSTANCE_LABEL")"

RUNNER_DIR="$RUNNER_ROOT/$NAME"
MACHINE_NAME="$(hostname -s 2>/dev/null || hostname 2>/dev/null || true)"
MACHINE_NAME="${MACHINE_NAME//[[:space:]]/-}"
[[ -n "$MACHINE_NAME" ]] || die "nao foi possivel identificar o nome da maquina"
RUNNER_GITHUB_NAME="$MACHINE_NAME-$NAME"

echo "Repo: $REPO_URL"
echo "Repo full name: $REPO_FULL_NAME"
echo "Runner base: $REQUESTED_NAME"
if [[ "$NAME" != "$REQUESTED_NAME" ]]; then
  echo "Runner local: $NAME (auto-incrementado; $REQUESTED_NAME ja existia)"
else
  echo "Runner local: $NAME"
fi
echo "Runner GitHub: $RUNNER_GITHUB_NAME"
echo "Instance label: $INSTANCE_LABEL"
echo "Profile: $PROFILE"
echo "Group: $GROUP"
echo "Enabled: $ENABLED"
echo "Pasta: $RUNNER_DIR"
echo "Runner root: $RUNNER_ROOT"
echo "Registry: $CONFIG_PATH"
echo "Tarball: $TAR_PATH"
echo "Token: ****"

step "Validando tarball Linux"

[[ -f "$TAR_PATH" ]] || die "tarball nao encontrado em: $TAR_PATH"
actual_sha="$(sha256sum "$TAR_PATH" | awk '{print $1}')"
[[ "${actual_sha,,}" == "${EXPECTED_SHA256,,}" ]] || die "checksum diferente. esperado: $EXPECTED_SHA256 | obtido: $actual_sha"
echo "Checksum OK"

step "Criando pasta do runner"

mkdir -p "$RUNNER_ROOT"
if [[ -d "$RUNNER_DIR" && "$REPLACE" -eq 1 ]]; then
  echo "Removendo pasta existente: $RUNNER_DIR"
  rm -rf "$RUNNER_DIR"
fi
mkdir -p "$RUNNER_DIR"

step "Extraindo tarball"

if [[ -x "$RUNNER_DIR/config.sh" ]]; then
  echo "Runner ja esta extraido em: $RUNNER_DIR"
else
  tar -xzf "$TAR_PATH" -C "$RUNNER_DIR"
  [[ -x "$RUNNER_DIR/config.sh" ]] || die "extracao falhou: config.sh nao encontrado em $RUNNER_DIR"
  echo "Extraido com sucesso em: $RUNNER_DIR"
fi

step "Atualizando runners.conf"

update_runners_conf "$CONFIG_PATH" "$NAME" "$RUNNER_DIR" "$PROFILE" "$REPO_FULL_NAME" "$ENABLED" "$GROUP"
echo "Atualizado: $CONFIG_PATH"
echo "$NAME|$RUNNER_DIR|$PROFILE|$REPO_FULL_NAME|$ENABLED|$GROUP"

step "Executando config.sh"

args=(
  --url "$REPO_URL"
  --token "$TOKEN"
  --name "$RUNNER_GITHUB_NAME"
  --labels "$LABELS"
  --work "$WORK_FOLDER"
  --unattended
)

if [[ "$REPLACE" -eq 1 ]]; then
  args+=(--replace)
fi

(cd "$RUNNER_DIR" && ./config.sh "${args[@]}")

step "Pronto"

echo "Runner configurado com sucesso."
echo "Nome no GitHub: $RUNNER_GITHUB_NAME"
echo "Nome local: $NAME"
echo "Grupo: $GROUP"
echo "Labels: $LABELS"
echo "Profile: $PROFILE"
echo
echo "Para subir esse runner:"
echo "  cd $BASE_DIR"
echo "  ./runners.sh start $NAME"
echo "  ./runners.sh start group:$GROUP"
