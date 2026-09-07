#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

fail() {
  printf '[FAIL] %s\n' "$1" >&2
  exit 1
}

pass() {
  printf '[PASS] %s\n' "$1"
}

assert_eq() {
  local expected="$1" actual="$2" message="$3"
  [[ "$actual" == "$expected" ]] || {
    printf 'expected: %s\nactual:   %s\n' "$expected" "$actual" >&2
    fail "$message"
  }
}

assert_file_sha() {
  local path="$1" expected="$2" message="$3"
  local actual
  actual="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$actual" == "$expected" ]] || fail "$message"
}

test_package_cache_contract() {
  local fake_bin="$TMP_ROOT/bin"
  local cache="$TMP_ROOT/cache"
  local log="$TMP_ROOT/gh.log"
  local payload="$TMP_ROOT/payload"
  local digest asset path first second third downloads

  mkdir -p "$fake_bin" "$cache"
  printf 'fake-runner-package\n' > "$payload"
  digest="$(sha256sum "$payload" | awk '{print $1}')"
  asset="actions-runner-linux-x64-9.9.9.tar.gz"

  cat > "$fake_bin/gh" <<EOF
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$log"

if [[ "${1:-}" != "api" ]]; then
  exit 1
fi

case "$*" in
  *"repos/actions/runner/releases/tags/v9.9.9"*)
    printf '777\t%s\tsha256:%s\n' "$asset" "$digest"
    ;;
  *"repos/actions/runner/releases/assets/777"*)
    printf 'asset-download\n' >> "$log"
    cat "$payload"
    ;;
  *)
    exit 1
    ;;
esac
EOF
  chmod +x "$fake_bin/gh"

  first="$(
    PATH="$fake_bin:$PATH" \
    RUNNER_PACKAGE_CACHE="$cache" \
    "$ROOT/runner-package.sh" ensure --version 9.9.9 --arch x64
  )"

  path="$cache/$asset"
  assert_eq "$path" "$first" "ensure deve retornar o path estável do cache"
  [[ -f "$path" ]] || fail "pacote não foi criado no cache"
  assert_file_sha "$path" "$digest" "download deve ser validado por SHA-256"

  second="$(
    PATH="$fake_bin:$PATH" \
    RUNNER_PACKAGE_CACHE="$cache" \
    "$ROOT/runner-package.sh" ensure --version 9.9.9 --arch x64
  )"
  assert_eq "$path" "$second" "cache hit deve retornar o mesmo path"

  downloads="$(grep -c '^asset-download$' "$log" || true)"
  assert_eq "1" "$downloads" "cache válido não deve baixar o asset novamente"

  printf 'corrupt\n' > "$path"

  third="$(
    PATH="$fake_bin:$PATH" \
    RUNNER_PACKAGE_CACHE="$cache" \
    "$ROOT/runner-package.sh" ensure --version 9.9.9 --arch x64
  )"
  assert_eq "$path" "$third" "cache inválido deve ser reconstruído no mesmo path"
  assert_file_sha "$path" "$digest" "cache corrompido deve ser substituído por pacote válido"

  downloads="$(grep -c '^asset-download$' "$log" || true)"
  assert_eq "2" "$downloads" "cache corrompido deve forçar novo download"

  pass "package ensure valida digest, reutiliza cache válido e recupera cache corrompido"
}

test_detect_contract() {
  local detected
  detected="$("$ROOT/runner-package.sh" detect)"

  case "$detected" in
    linux\|x64|linux\|arm64) ;;
    *) fail "detect retornou plataforma não suportada: $detected" ;;
  esac

  pass "package detect permanece restrito a Linux x64/arm64"
}

main() {
  test_package_cache_contract
  test_detect_contract
  printf '\nContratos de package/cache passaram.\n'
}

main "$@"
