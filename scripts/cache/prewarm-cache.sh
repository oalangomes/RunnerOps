#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_ENV_PATH="$BASE_DIR/runner-cache-env.sh"

usage() {
  cat <<'EOF'
Uso:
  ./prewarm-cache.sh [stack|all]

Stacks:
  node      valida Node/npm/corepack e aquece caches npm/pnpm quando disponivel
  python    valida python/pip e mostra cache pip
  flutter   valida Flutter/Java e executa flutter precache --android
  android   alias de flutter
  java      valida Java/Gradle/Maven quando disponivel
  go        valida Go
  dotnet    valida .NET/NuGet
  all       executa todos os stacks seguros

Exemplos:
  ./prewarm-cache.sh node
  ./prewarm-cache.sh flutter
  ./prewarm-cache.sh all
EOF
}

run_if_exists() {
  local cmd="$1"
  shift

  if command -v "$cmd" >/dev/null 2>&1; then
    echo "[RUN] $cmd $*"
    "$cmd" "$@" || echo "[WARN] comando falhou: $cmd $*"
  else
    echo "[SKIP] $cmd nao encontrado"
  fi
}

source_cache_env() {
  local profile="$1"

  [[ -f "$CACHE_ENV_PATH" ]] || {
    echo "ERRO: runner-cache-env.sh nao encontrado: $CACHE_ENV_PATH" >&2
    exit 1
  }

  export LOCAL_RUNNER_PROFILE="$profile"
  export RUNNER_CACHE_PROFILE="$profile"
  # shellcheck source=/dev/null
  source "$CACHE_ENV_PATH"

  echo "Cache profile: $RUNNER_CACHE_PROFILE"
  echo "Stack cache:   $RUNNER_STACK_CACHE_ROOT"
  echo "Tool cache:    $RUNNER_TOOL_CACHE"
}

prewarm_node() {
  echo
  echo "==> Node"
  source_cache_env node
  run_if_exists node --version
  run_if_exists npm --version
  run_if_exists npm config get cache
  run_if_exists corepack --version
  run_if_exists pnpm --version
  run_if_exists yarn --version
}

prewarm_python() {
  echo
  echo "==> Python"
  source_cache_env python
  run_if_exists python3 --version
  run_if_exists pip3 --version
  run_if_exists pip3 cache dir
}

prewarm_flutter() {
  echo
  echo "==> Flutter/Android"
  source_cache_env flutter
  run_if_exists java -version
  run_if_exists flutter --version
  run_if_exists flutter doctor
  run_if_exists flutter precache --android
  echo "PUB_CACHE=$PUB_CACHE"
}

prewarm_java() {
  echo
  echo "==> Java"
  source_cache_env java
  run_if_exists java -version
  run_if_exists gradle --version
  run_if_exists mvn --version
  echo "GRADLE_USER_HOME=$GRADLE_USER_HOME"
}

prewarm_go() {
  echo
  echo "==> Go"
  source_cache_env go
  run_if_exists go version
  echo "GOMODCACHE=$GOMODCACHE"
  echo "GOCACHE=$GOCACHE"
}

prewarm_dotnet() {
  echo
  echo "==> .NET"
  source_cache_env dotnet
  run_if_exists dotnet --info
  echo "NUGET_PACKAGES=$NUGET_PACKAGES"
}

STACK="${1:-all}"

if [[ "$STACK" == "help" || "$STACK" == "-h" || "$STACK" == "--help" ]]; then
  usage
  exit 0
fi

case "$STACK" in
  node)
    prewarm_node
    ;;
  python)
    prewarm_python
    ;;
  flutter|android)
    prewarm_flutter
    ;;
  java)
    prewarm_java
    ;;
  go)
    prewarm_go
    ;;
  dotnet)
    prewarm_dotnet
    ;;
  all)
    prewarm_node
    prewarm_python
    prewarm_flutter
    prewarm_java
    prewarm_go
    prewarm_dotnet
    ;;
  *)
    echo "ERRO: stack desconhecida: $STACK" >&2
    usage >&2
    exit 1
    ;;
esac
