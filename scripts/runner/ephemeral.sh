#!/usr/bin/env bash
set -euo pipefail

# Narrow adapter around the official GitHub Actions runner scripts. Lifecycle,
# evidence, reconciliation and cleanup policy remain in runnerops.ephemeral.

die() {
  echo "ERRO: $*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Internal helper. Public interface: runnerctl ephemeral <create|status|reconcile|cleanup>.

Commands:
  ephemeral.sh check --action-id ID --identity NAME --unit UNIT
  ephemeral.sh prepare --root PATH --action-id ID --identity NAME --package TAR
  ephemeral.sh configure --root PATH --action-id ID --identity NAME --repo-url URL --labels CSV
  ephemeral.sh start --root PATH --action-id ID --identity NAME --unit UNIT
  ephemeral.sh stop --root PATH --action-id ID --identity NAME --unit UNIT
EOF
}

ROOT=""
ACTION_ID=""
IDENTITY=""
PACKAGE=""
REPO_URL=""
LABELS=""
UNIT=""
RUNNEROPS_SYSTEMCTL_HELPER="${RUNNEROPS_SYSTEMCTL_HELPER:-/usr/local/libexec/runnerops-systemctl}"
RUNNER_SYSTEMD_RUNTIME_DIR="${RUNNER_SYSTEMD_RUNTIME_DIR:-/run/systemd/system}"

parse_args() {
  while (($#)); do
    case "$1" in
      --root) ROOT="${2:-}"; shift 2 ;;
      --action-id) ACTION_ID="${2:-}"; shift 2 ;;
      --identity) IDENTITY="${2:-}"; shift 2 ;;
      --package) PACKAGE="${2:-}"; shift 2 ;;
      --repo-url) REPO_URL="${2:-}"; shift 2 ;;
      --labels) LABELS="${2:-}"; shift 2 ;;
      --unit) UNIT="${2:-}"; shift 2 ;;
      *) die "opção desconhecida: $1" ;;
    esac
  done
}

validate_action_identity() {
  local expected_identity
  [[ -n "$ACTION_ID" && -n "$IDENTITY" ]] || die "action/identity obrigatórios"
  [[ "$ACTION_ID" =~ ^[a-f0-9]{32}$ ]] || die "action id inválido"
  [[ "$IDENTITY" =~ ^runnerops-ephemeral-[a-f0-9]{16}$ ]] || die "identity inválida"
  command -v sha256sum >/dev/null 2>&1 || die "sha256sum não encontrado"
  expected_identity="runnerops-ephemeral-$(printf '%s' "$ACTION_ID" | sha256sum | cut -c1-16)"
  [[ "$IDENTITY" == "$expected_identity" ]] || die "identity não corresponde à action exata"
}

validate_unit() {
  local service_user expected_unit
  validate_action_identity
  service_user="$(id -un)"
  [[ "$service_user" =~ ^[A-Za-z0-9_.-]+$ ]] || die "usuário local não suportado"
  expected_unit="actions.runner.runnerops-ephemeral-${service_user}@${ACTION_ID}.service"
  [[ "$UNIT" == "$expected_unit" ]] || die "unit ephemeral não corresponde à action exata"
}

authorized_systemctl() {
  local action="$1"
  if [[ "$(id -u)" -eq 0 ]]; then
    "$RUNNEROPS_SYSTEMCTL_HELPER" "$action" "$UNIT"
  else
    command -v sudo >/dev/null 2>&1 || die "sudo ausente para lifecycle systemd"
    sudo -n "$RUNNEROPS_SYSTEMCTL_HELPER" "$action" "$UNIT"
  fi
}

check_systemd_authority() {
  local template
  validate_unit
  command -v systemctl >/dev/null 2>&1 || die "systemctl não encontrado"
  [[ -d "$RUNNER_SYSTEMD_RUNTIME_DIR" ]] || die "systemd não está ativo"
  [[ -x "$RUNNEROPS_SYSTEMCTL_HELPER" ]] || die "helper systemd autorizado ausente; rode runnerctl platform-authorize"
  template="${UNIT%@*}@.service"
  systemctl cat "$template" >/dev/null 2>&1 || die "template ephemeral ausente; rode runnerctl platform-authorize"
  if [[ "$(id -u)" -eq 0 ]]; then
    "$RUNNEROPS_SYSTEMCTL_HELPER" check >/dev/null
  else
    sudo -n "$RUNNEROPS_SYSTEMCTL_HELPER" check >/dev/null ||
      die "autorização systemd indisponível; rode runnerctl platform-authorize"
  fi
}

validate_owned_root() {
  local marker
  validate_action_identity
  [[ -n "$ROOT" && -n "$ACTION_ID" && -n "$IDENTITY" ]] || die "root/action/identity obrigatórios"
  [[ -d "$ROOT" && ! -L "$ROOT" ]] || die "ephemeral root ausente ou inseguro"
  marker="$ROOT/.runnerops-ephemeral-owner.json"
  [[ -f "$marker" && ! -L "$marker" ]] || die "ownership marker ausente ou inseguro"
  grep -Fq "\"action_id\": \"$ACTION_ID\"" "$marker" || die "ownership action não confere"
  grep -Fq "\"runner_identity\": \"$IDENTITY\"" "$marker" || die "ownership identity não confere"
}

command="${1:-help}"
shift || true
parse_args "$@"

case "$command" in
  check)
    check_systemd_authority
    ;;
  prepare)
    validate_owned_root
    [[ -n "$PACKAGE" && -f "$PACKAGE" && ! -L "$PACKAGE" ]] || die "pacote verificado ausente ou inseguro"
    if [[ ! -x "$ROOT/config.sh" ]]; then
      tar -xzf "$PACKAGE" --no-same-owner -C "$ROOT"
    fi
    [[ -x "$ROOT/config.sh" && -x "$ROOT/run.sh" ]] || die "pacote do runner incompleto"
    ;;
  configure)
    validate_owned_root
    [[ -n "$REPO_URL" && "$REPO_URL" == https://github.com/*/* ]] || die "repo URL inválida"
    [[ -n "$LABELS" ]] || die "labels obrigatórias"
    [[ -x "$ROOT/config.sh" ]] || die "runner não foi preparado"
    registration_material=""
    IFS= read -r registration_material || true
    [[ -n "$registration_material" ]] || die "registration material ausente"
    (
      cd "$ROOT"
      ACTIONS_RUNNER_INPUT_TOKEN="$registration_material" \
        ./config.sh \
          --url "$REPO_URL" \
          --name "$IDENTITY" \
          --labels "$LABELS" \
          --work _work \
          --unattended \
          --ephemeral \
          --disableupdate
    )
    unset registration_material
    ;;
  start)
    validate_owned_root
    check_systemd_authority
    [[ -x "$ROOT/run.sh" && -f "$ROOT/.runner" ]] || die "runner ephemeral não configurado"
    authorized_systemctl start
    state="$(systemctl show "$UNIT" --property=ActiveState --value 2>/dev/null || true)"
    [[ "$state" == "active" || "$state" == "activating" ]] || die "unit ephemeral não ficou ativa"
    printf '%s\n' "$UNIT"
    ;;
  stop)
    validate_owned_root
    check_systemd_authority
    authorized_systemctl stop
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    die "comando desconhecido: $command"
    ;;
esac
