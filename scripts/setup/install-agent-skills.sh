#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$BASE_DIR/skills"

TOOL="all"
SCOPE="user"
PROJECT_DIR="$(pwd)"
DRY_RUN=0
SELECTED_SKILL=""

usage() {
  cat <<'EOF'
Uso:
  ./install-agent-skills.sh [opcoes]

Opcoes:
  --tool TOOL          codex, copilot, claude, agents ou all (default: all)
                       all instala Codex + Copilot + Claude; agents e explicito
  --scope SCOPE        user ou project (default: user)
  --project-dir PATH   raiz do projeto para --scope project
  --skill NAME         instala apenas uma skill
  --dry-run            mostra destinos sem escrever
  --list               lista skills canonicas
  -h, --help           mostra ajuda

Destinos user:
  codex    ~/.codex/skills
  copilot  ~/.copilot/skills
  claude   ~/.claude/skills
  agents   ~/.agents/skills

Destinos project:
  codex    <repo>/.codex/skills
  copilot  <repo>/.github/skills
  claude   <repo>/.claude/skills
  agents   <repo>/.agents/skills

Exemplos:
  ./install-agent-skills.sh --tool all
  ./install-agent-skills.sh --tool claude
  ./install-agent-skills.sh --tool copilot --scope project --project-dir ~/projetos/meu-repo
  ./install-agent-skills.sh --tool agents --skill runnerops-manage-runners
EOF
}

die() {
  echo "ERRO: $*" >&2
  exit 1
}

list_skills() {
  local dir
  for dir in "$SKILLS_DIR"/*; do
    [[ -d "$dir" && -f "$dir/SKILL.md" ]] || continue
    basename "$dir"
  done
}

validate_skill() {
  local dir="$1"
  local expected actual
  expected="$(basename "$dir")"
  actual="$(
    awk '
      NR == 1 && $0 == "---" { frontmatter=1; next }
      frontmatter && $0 == "---" { exit }
      frontmatter && /^name:[[:space:]]*/ {
        sub(/^name:[[:space:]]*/, "")
        gsub(/^["'\'' ]+|["'\'' ]+$/, "")
        print
        exit
      }
    ' "$dir/SKILL.md"
  )"

  [[ -n "$actual" ]] || die "$expected: frontmatter sem name"
  [[ "$actual" == "$expected" ]] ||
    die "$expected: name do frontmatter ('$actual') difere do diretorio"
}

destination_for() {
  local tool="$1" scope="$2"

  if [[ "$scope" == "user" ]]; then
    case "$tool" in
      codex)   printf '%s\n' "$HOME/.codex/skills" ;;
      copilot) printf '%s\n' "$HOME/.copilot/skills" ;;
      claude)  printf '%s\n' "$HOME/.claude/skills" ;;
      agents)  printf '%s\n' "$HOME/.agents/skills" ;;
      *) die "tool invalida: $tool" ;;
    esac
  else
    case "$tool" in
      codex)   printf '%s\n' "$PROJECT_DIR/.codex/skills" ;;
      copilot) printf '%s\n' "$PROJECT_DIR/.github/skills" ;;
      claude)  printf '%s\n' "$PROJECT_DIR/.claude/skills" ;;
      agents)  printf '%s\n' "$PROJECT_DIR/.agents/skills" ;;
      *) die "tool invalida: $tool" ;;
    esac
  fi
}

legacy_name_for() {
  case "$1" in
    runnerops-pr-validation) printf '%s\n' "start-project-runners-before-pr" ;;
    runnerops-manage-runners) printf '%s\n' "manage-local-github-runners" ;;
    runnerops-ci-performance) printf '%s\n' "analyze-ci-workflow-performance" ;;
    *) return 0 ;;
  esac
}

install_one_skill() {
  local source_dir="$1" destination_root="$2" tool="$3"
  local skill target legacy legacy_target

  skill="$(basename "$source_dir")"
  target="$destination_root/$skill"
  legacy="$(legacy_name_for "$skill")"
  legacy_target=""

  if [[ -n "$legacy" ]]; then
    legacy_target="$destination_root/$legacy"
  fi

  if [[ "$DRY_RUN" -eq 1 ]]; then
    if [[ -n "$legacy_target" && -d "$legacy_target" ]]; then
      echo "[DRY] $tool: migrate $legacy -> $skill"
    fi
    echo "[DRY] $tool: $skill -> $target"
    return 0
  fi

  mkdir -p "$destination_root"

  if [[ -n "$legacy_target" && -d "$legacy_target" ]]; then
    rm -rf "$legacy_target"
    echo "[MIGRATE] $tool: $legacy -> $skill"
  fi

  rm -rf "$target"
  mkdir -p "$target"
  cp -a "$source_dir/." "$target/"
  echo "[OK] $tool: $skill -> $target"
}

install_for_tool() {
  local tool="$1"
  local destination source_dir installed=0

  destination="$(destination_for "$tool" "$SCOPE")"

  for source_dir in "$SKILLS_DIR"/*; do
    [[ -d "$source_dir" && -f "$source_dir/SKILL.md" ]] || continue

    if [[ -n "$SELECTED_SKILL" && "$(basename "$source_dir")" != "$SELECTED_SKILL" ]]; then
      continue
    fi

    validate_skill "$source_dir"
    install_one_skill "$source_dir" "$destination" "$tool"
    installed=$((installed + 1))
  done

  [[ "$installed" -gt 0 ]] || die "nenhuma skill encontrada para instalar"
}

while (($#)); do
  case "$1" in
    --tool)
      TOOL="${2:-}"
      shift 2
      ;;
    --scope)
      SCOPE="${2:-}"
      shift 2
      ;;
    --project-dir)
      PROJECT_DIR="${2:-}"
      shift 2
      ;;
    --skill)
      SELECTED_SKILL="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --list)
      list_skills
      exit 0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      die "opcao desconhecida: $1"
      ;;
  esac
done

[[ -d "$SKILLS_DIR" ]] || die "diretorio de skills nao encontrado: $SKILLS_DIR"

case "$SCOPE" in
  user|project) ;;
  *) die "scope invalido: $SCOPE" ;;
esac

if [[ "$SCOPE" == "project" ]]; then
  PROJECT_DIR="$(realpath -m "$PROJECT_DIR")"
  [[ -d "$PROJECT_DIR" ]] || die "project-dir nao existe: $PROJECT_DIR"
fi

case "$TOOL" in
  codex|copilot|claude|agents)
    install_for_tool "$TOOL"
    ;;
  all)
    if [[ "$SCOPE" == "project" ]]; then
      die "--tool all com --scope project pode duplicar descoberta entre agentes; escolha codex, copilot, claude ou agents explicitamente"
    fi
    for tool in codex copilot claude; do
      install_for_tool "$tool"
    done
    ;;
  *)
    die "tool invalida: $TOOL"
    ;;
esac
