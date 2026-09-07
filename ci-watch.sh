#!/usr/bin/env bash
set -euo pipefail

REPO=""
SHA=""
JSON_OUTPUT=0
TIMEOUT_SECONDS=600
INTERVAL_SECONDS=5
SETTLE_POLLS=2

usage() {
  cat <<'EOF'
Uso:
  ./ci-watch.sh --repo owner/repo --sha SHA [--json] [--timeout SEGUNDOS]
                [--interval SEGUNDOS] [--settle-polls N]

Exit codes:
  0  CI concluído com sucesso
  1  CI/workflow falhou
  2  infraestrutura/acesso ao GitHub falhou
  3  timeout, cancelamento ou resultado inconclusivo
EOF
}

die_usage() {
  echo "ERRO: $*" >&2
  usage >&2
  exit 2
}

json_escape() {
  local value="${1:-}"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  value="${value//$'\t'/\\t}"
  printf '%s' "$value"
}

emit_json() {
  local status="$1" kind="$2" conclusion="$3" workflow="$4"
  local run_id="$5" url="$6" run_count="$7" message="$8"

  printf '{"status":"%s","kind":"%s","repo":"%s","sha":"%s","conclusion":"%s","workflow":"%s","run_id":%s,"url":"%s","run_count":%s,"message":"%s"}\n' \
    "$(json_escape "$status")" \
    "$(json_escape "$kind")" \
    "$(json_escape "$REPO")" \
    "$(json_escape "$SHA")" \
    "$(json_escape "$conclusion")" \
    "$(json_escape "$workflow")" \
    "${run_id:-null}" \
    "$(json_escape "$url")" \
    "${run_count:-0}" \
    "$(json_escape "$message")"
}

emit_result() {
  local status="$1" kind="$2" conclusion="$3" workflow="$4"
  local run_id="$5" url="$6" run_count="$7" message="$8"

  if [[ "$JSON_OUTPUT" -eq 1 ]]; then
    emit_json "$status" "$kind" "$conclusion" "$workflow" "$run_id" "$url" "$run_count" "$message"
    return
  fi

  case "$status" in
    success)
      echo "[OK] CI success repo=$REPO sha=$SHA runs=$run_count"
      ;;
    failure)
      echo "[FAIL] CI workflow=$workflow conclusion=$conclusion repo=$REPO sha=$SHA run_id=$run_id"
      [[ -n "$url" ]] && echo "       $url"
      ;;
    cancelled)
      echo "[WAIT] CI inconclusivo: workflow cancelado=$workflow repo=$REPO sha=$SHA"
      [[ -n "$url" ]] && echo "       $url"
      ;;
    timeout)
      echo "[WAIT] timeout aguardando CI repo=$REPO sha=$SHA runs=$run_count"
      ;;
    error)
      echo "[ERR] $message repo=$REPO sha=$SHA" >&2
      ;;
  esac
}

is_non_negative_integer() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

query_runs() {
  gh api "repos/$REPO/actions/runs?head_sha=$SHA&per_page=100" \
    --jq '.workflow_runs[] | [.id, .name, .status, (.conclusion // ""), .html_url, (.run_attempt // 1)] | @tsv'
}

while (($#)); do
  case "$1" in
    --repo)
      REPO="${2:-}"
      shift 2
      ;;
    --sha)
      SHA="${2:-}"
      shift 2
      ;;
    --json)
      JSON_OUTPUT=1
      shift
      ;;
    --timeout)
      TIMEOUT_SECONDS="${2:-}"
      shift 2
      ;;
    --interval)
      INTERVAL_SECONDS="${2:-}"
      shift 2
      ;;
    --settle-polls)
      SETTLE_POLLS="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die_usage "opção desconhecida: $1"
      ;;
  esac
done

[[ "$REPO" == */* ]] || die_usage "--repo owner/repo é obrigatório"
[[ "$SHA" =~ ^[0-9A-Fa-f]{7,64}$ ]] || die_usage "--sha válido é obrigatório"
is_non_negative_integer "$TIMEOUT_SECONDS" || die_usage "--timeout deve ser inteiro >= 0"
is_non_negative_integer "$INTERVAL_SECONDS" || die_usage "--interval deve ser inteiro >= 0"
is_non_negative_integer "$SETTLE_POLLS" || die_usage "--settle-polls deve ser inteiro >= 1"
[[ "$SETTLE_POLLS" -ge 1 ]] || die_usage "--settle-polls deve ser inteiro >= 1"

if ! command -v gh >/dev/null 2>&1; then
  emit_result error infra "" "" "" "" 0 "GitHub CLI (gh) não encontrado"
  exit 2
fi

if ! gh auth status >/dev/null 2>&1; then
  emit_result error infra "" "" "" "" 0 "GitHub CLI não autenticado"
  exit 2
fi

started_at="$(date +%s)"
last_terminal_signature=""
stable_terminal_polls=0
last_wait_signature=""

while true; do
  rows=""
  if ! rows="$(query_runs 2>/dev/null)"; then
    emit_result error infra "" "" "" "" 0 "falha ao consultar GitHub Actions"
    exit 2
  fi

  run_count=0
  active_count=0
  failure_conclusion=""
  failure_workflow=""
  failure_run_id=""
  failure_url=""
  cancelled_workflow=""
  cancelled_run_id=""
  cancelled_url=""
  terminal_signature=""

  while IFS=$'\t' read -r run_id workflow status conclusion url attempt; do
    [[ -n "${run_id:-}" ]] || continue
    run_count=$((run_count + 1))
    terminal_signature+="${run_id}:${attempt}:${status}:${conclusion}|"

    if [[ "$status" != "completed" ]]; then
      active_count=$((active_count + 1))
      continue
    fi

    case "$conclusion" in
      success|neutral|skipped)
        ;;
      failure|timed_out|action_required|startup_failure)
        if [[ -z "$failure_run_id" ]]; then
          failure_conclusion="$conclusion"
          failure_workflow="$workflow"
          failure_run_id="$run_id"
          failure_url="$url"
        fi
        ;;
      cancelled)
        if [[ -z "$cancelled_run_id" ]]; then
          cancelled_workflow="$workflow"
          cancelled_run_id="$run_id"
          cancelled_url="$url"
        fi
        ;;
      *)
        active_count=$((active_count + 1))
        ;;
    esac
  done <<< "$rows"

  if [[ -n "$failure_run_id" ]]; then
    emit_result failure ci "$failure_conclusion" "$failure_workflow" "$failure_run_id" "$failure_url" "$run_count" "workflow concluído com falha"
    exit 1
  fi

  if [[ "$run_count" -gt 0 && "$active_count" -eq 0 ]]; then
    if [[ -n "$cancelled_run_id" ]]; then
      emit_result cancelled inconclusive cancelled "$cancelled_workflow" "$cancelled_run_id" "$cancelled_url" "$run_count" "workflow cancelado"
      exit 3
    fi

    if [[ "$terminal_signature" == "$last_terminal_signature" ]]; then
      stable_terminal_polls=$((stable_terminal_polls + 1))
    else
      last_terminal_signature="$terminal_signature"
      stable_terminal_polls=1
    fi

    if [[ "$stable_terminal_polls" -ge "$SETTLE_POLLS" ]]; then
      emit_result success ci success "" "" "" "$run_count" "todos os workflows observados concluíram com sucesso"
      exit 0
    fi
  else
    stable_terminal_polls=0
    last_terminal_signature=""
  fi

  now="$(date +%s)"
  elapsed=$((now - started_at))
  if [[ "$elapsed" -ge "$TIMEOUT_SECONDS" ]]; then
    emit_result timeout inconclusive "" "" "" "" "$run_count" "timeout aguardando conclusão do CI"
    exit 3
  fi

  if [[ "$JSON_OUTPUT" -eq 0 ]]; then
    wait_signature="$run_count:$active_count:$stable_terminal_polls"
    if [[ "$wait_signature" != "$last_wait_signature" ]]; then
      echo "[WAIT] repo=$REPO sha=$SHA runs=$run_count active=$active_count"
      last_wait_signature="$wait_signature"
    fi
  fi

  sleep "$INTERVAL_SECONDS"
done
