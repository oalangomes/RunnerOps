#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

mkdir -p "$TMP_ROOT/home" "$TMP_ROOT/config"
runtime_env="$ROOT/scripts/runner/runtime-env.sh"
src_root="$ROOT/src"
config_file="$TMP_ROOT/config.env"

fail() {
  printf '[FAIL] %s\n' "$1" >&2
  exit 1
}

assert_eq() {
  [[ "$1" == "$2" ]] || fail "$3: esperado '$2', recebido '$1'"
}

load_pythonpath() {
  env -i \
    PATH="$PATH" \
    HOME="$TMP_ROOT/home" \
    XDG_CONFIG_HOME="$TMP_ROOT/config" \
    ACTIONS_RUNNERS_ENV="$config_file" \
    RUNNEROPS_PLATFORM_HOME="$ROOT" \
    bash -c 'source "$1"; printf "%s" "$PYTHONPATH"' _ "$runtime_env"
}

printf '\n' > "$config_file"
actual="$(load_pythonpath)"
assert_eq "$actual" "$src_root" "sem PYTHONPATH configurado, src deve permanecer disponível"

printf 'PYTHONPATH=%q\n' /tmp/custom-python > "$config_file"
actual="$(load_pythonpath)"
assert_eq "$actual" "$src_root:/tmp/custom-python" "config.env deve preservar o caminho do usuário"

printf 'PYTHONPATH=%q\n' "/tmp/custom-python:$src_root:/opt/another-python" > "$config_file"
actual="$(load_pythonpath)"
assert_eq "$actual" "$src_root:/tmp/custom-python:/opt/another-python" "src duplicado deve ser removido sem perder os demais caminhos"

printf '[PASS] runtime-env preserva e normaliza PYTHONPATH após config.env\n'