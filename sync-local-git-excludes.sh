#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "$BASE_DIR/runner-runtime-env.sh"

git -C "$BASE_DIR" rev-parse --git-dir >/dev/null 2>&1 || exit 0
[[ -f "$RUNNERS_CONFIG" ]] || exit 0

exclude_path="$(git -C "$BASE_DIR" rev-parse --git-path info/exclude)"
[[ "$exclude_path" == /* ]] || exclude_path="$BASE_DIR/$exclude_path"
mkdir -p "$(dirname "$exclude_path")"
touch "$exclude_path"

added=0
while IFS='|' read -r name path _profile _repo _enabled _group _rest; do
  trimmed_name="${name#"${name%%[![:space:]]*}"}"
  [[ -n "$trimmed_name" ]] || continue
  [[ "${trimmed_name:0:1}" == "#" ]] && continue

  path="${path#"${path%%[![:space:]]*}"}"
  path="${path%"${path##*[![:space:]]}"}"
  [[ -n "$path" ]] || continue
  path="$(realpath -m "$path")"

  case "$path/" in
    "$BASE_DIR"/*)
      rel="${path#"$BASE_DIR/"}"
      pattern="/$rel/"
      if ! grep -Fxq "$pattern" "$exclude_path"; then
        printf '%s\n' "$pattern" >> "$exclude_path"
        echo "[ADD] local git exclude: $pattern"
        added=$((added + 1))
      fi
      ;;
  esac
done < "$RUNNERS_CONFIG"

[[ "$added" -gt 0 ]] || echo "[OK] nenhum runner local dentro do checkout precisa de git exclude"
