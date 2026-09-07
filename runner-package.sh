#!/usr/bin/env bash
set -euo pipefail

CACHE_ROOT="${RUNNER_PACKAGE_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/actions-runners/packages}"
VERSION="${RUNNER_VERSION:-latest}"
ARCH="${RUNNER_ARCH:-auto}"

die() {
  echo "ERRO: $*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Uso:
  ./runner-package.sh detect
  ./runner-package.sh ensure [--version VERSION|latest] [--arch auto|x64|arm64]

Variáveis:
  RUNNER_VERSION
  RUNNER_ARCH
  RUNNER_PACKAGE_CACHE
EOF
}

detect_os() {
  case "$(uname -s)" in
    Linux) echo linux ;;
    *) die "host não suportado neste release: $(uname -s); esperado Linux" ;;
  esac
}

detect_arch() {
  local raw
  raw="$(uname -m)"
  case "$raw" in
    x86_64|amd64) echo x64 ;;
    aarch64|arm64) echo arm64 ;;
    *) die "arquitetura não suportada: $raw; esperado x64 ou arm64" ;;
  esac
}

resolve_version() {
  local requested="$1"
  local tag

  if [[ "$requested" != "latest" ]]; then
    printf '%s\n' "${requested#v}"
    return 0
  fi

  command -v gh >/dev/null 2>&1 || die "gh é obrigatório para resolver a versão latest"
  tag="$(gh api repos/actions/runner/releases/latest --jq .tag_name)"
  [[ -n "$tag" ]] || die "não foi possível resolver a release latest de actions/runner"
  printf '%s\n' "${tag#v}"
}

ensure_package() {
  local os="$1" arch="$2" version="$3"
  local asset_name asset_id digest expected path tmp actual row

  command -v gh >/dev/null 2>&1 || die "gh é obrigatório para download automático do runner"
  command -v sha256sum >/dev/null 2>&1 || die "sha256sum não encontrado"

  asset_name="actions-runner-${os}-${arch}-${version}.tar.gz"

  row="$(
    gh api "repos/actions/runner/releases/tags/v$version"       --jq '.assets[] | [.id, .name, (.digest // "")] | @tsv' |
      awk -F '\t' -v wanted="$asset_name" '$2 == wanted { print; exit }'
  )"
  [[ -n "$row" ]] || die "asset oficial não encontrado: $asset_name"

  IFS=$'\t' read -r asset_id _name digest <<< "$row"
  [[ -n "$asset_id" ]] || die "asset id ausente para $asset_name"
  [[ "$digest" == sha256:* ]] || die "GitHub não forneceu digest SHA-256 para $asset_name"

  expected="${digest#sha256:}"
  mkdir -p "$CACHE_ROOT"
  path="$CACHE_ROOT/$asset_name"

  if [[ -f "$path" ]]; then
    actual="$(sha256sum "$path" | awk '{print $1}')"
    if [[ "${actual,,}" == "${expected,,}" ]]; then
      echo "[CACHE] $asset_name" >&2
      printf '%s\n' "$path"
      return 0
    fi
    echo "[WARN] cache inválido removido: $path" >&2
    rm -f "$path"
  fi

  tmp="$path.tmp.$$"
  trap 'rm -f "$tmp"' EXIT

  echo "[GET] actions/runner v$version $os/$arch" >&2
  gh api     -H "Accept: application/octet-stream"     "repos/actions/runner/releases/assets/$asset_id" > "$tmp"

  actual="$(sha256sum "$tmp" | awk '{print $1}')"
  [[ "${actual,,}" == "${expected,,}" ]] ||
    die "checksum inválido para $asset_name: esperado=$expected obtido=$actual"

  mv "$tmp" "$path"
  trap - EXIT

  echo "[OK] pacote verificado: $path" >&2
  printf '%s\n' "$path"
}

cmd="${1:-help}"
shift || true

case "$cmd" in
  detect)
    printf '%s|%s\n' "$(detect_os)" "$(detect_arch)"
    ;;
  ensure)
    while (($#)); do
      case "$1" in
        --version)
          VERSION="${2:-}"
          shift 2
          ;;
        --arch)
          ARCH="${2:-}"
          shift 2
          ;;
        -h|--help)
          usage
          exit 0
          ;;
        *)
          die "opção desconhecida: $1"
          ;;
      esac
    done

    os="$(detect_os)"
    [[ "$ARCH" == auto ]] && ARCH="$(detect_arch)"
    case "$ARCH" in
      x64|arm64) ;;
      *) die "arch inválida: $ARCH" ;;
    esac
    version="$(resolve_version "$VERSION")"
    ensure_package "$os" "$ARCH" "$version"
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    die "comando desconhecido: $cmd"
    ;;
esac
