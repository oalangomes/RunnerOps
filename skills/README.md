# Agent Skills portáveis

Estas são as skills canônicas para operação de runners self-hosted deste repositório.

Elas usam o formato portável `SKILL.md` de Agent Skills para que o mesmo fluxo não precise de uma implementação diferente para cada coding agent.

## Convenção de nomes

Todas as skills oficiais do produto usam o prefixo `runnerops-`. Isso mantém as skills agrupadas e fáceis de localizar manualmente em diretórios como `~/.agents/skills`, `~/.codex/skills`, `~/.copilot/skills` e `~/.claude/skills`.

Exemplo:

```bash
ls ~/.agents/skills | grep '^runnerops-'
```

## Skills incluídas

| Skill | Finalidade |
|---|---|
| `runnerops-pr-validation` | Antes da PR, garantir somente os runners do repositório atual; depois da publicação, consumir `runnerctl ci watch` quando a tarefa exigir aguardar o CI. |
| `runnerops-manage-runners` | Inventariar, iniciar/parar, diagnosticar, registrar, remover e validar runners, além de consumir feedback estruturado do CI por PR/SHA. |
| `runnerops-ci-performance` | Analisar DAG, critical path, repetição, cache, artifacts, triggers, fila e capacidade usando evidência STATIC / OBSERVED / ESTIMATED; read-only por padrão. |

## Instalação

Instale as skills nos principais destinos de usuário (Codex, Copilot e Claude):

```bash
runnerctl skills install all
```

Ou escolha uma ferramenta:

```bash
runnerctl skills install codex
runnerctl skills install copilot
runnerctl skills install claude
runnerctl skills install agents
```

Instale somente uma skill:

```bash
./install-agent-skills.sh \
  --tool claude \
  --skill runnerops-manage-runners
```

Visualize o que seria feito sem gravar:

```bash
./install-agent-skills.sh --tool all --dry-run
```

## Destinos no nível do usuário

| Destino | Caminho |
|---|---|
| Codex | `~/.codex/skills/<skill>/SKILL.md` |
| GitHub Copilot CLI | `~/.copilot/skills/<skill>/SKILL.md` |
| Claude Code | `~/.claude/skills/<skill>/SKILL.md` |
| Agent Skills genéricas | `~/.agents/skills/<skill>/SKILL.md` |

O destino genérico `~/.agents/skills` é útil para ferramentas compatíveis com a convenção compartilhada de Agent Skills. Ele só é instalado quando `--tool agents` é solicitado, evitando descoberta duplicada em clientes que também examinam seu próprio diretório específico.

## Instalação local por projeto

Para instalar dentro de outro repositório em vez do diretório home:

```bash
./install-agent-skills.sh \
  --tool copilot \
  --scope project \
  --project-dir ~/projects/example
```

`--tool all` é rejeitado de propósito com `--scope project`, pois alguns agentes descobrem mais de um diretório de skills no nível do projeto. Escolha um destino explícito para evitar descoberta duplicada.

Destinos por projeto:

| Destino | Caminho |
|---|---|
| Codex | `<repo>/.codex/skills` |
| GitHub Copilot | `<repo>/.github/skills` |
| Claude Code | `<repo>/.claude/skills` |
| Agent Skills genéricas | `<repo>/.agents/skills` |

## Contrato de configuração

As skills não contêm inventário de runners específico da máquina.

Elas esperam que a plataforma resolva seu estado local usando:

```bash
RUNNERS_CONFIG=~/.config/actions-runners/runners.conf
RUNNER_DATA_ROOT=~/.local/share/actions-runners/runners
RUNNER_CACHE_ROOT=~/.cache/actions-runners
RUNNER_STATE_ROOT=~/.local/state/actions-runners
RUNNER_BOOT_POLICY=on-demand
```

As skills dependem apenas do comando instalado `runnerctl`; elas não precisam conhecer o diretório onde a plataforma foi clonada.

Para feedback de CI, as skills usam somente a interface pública (`runnerctl ci watch`) e preservam a distinção entre falha de workflow e falha de runner/infraestrutura.

As skills nunca embutem nem persistem registration tokens do GitHub Runner.

## Migração de nomes anteriores

Ao instalar uma skill com o nome novo, o installer remove somente o diretório legado conhecido correspondente antes de gravar a versão `runnerops-*`. Ele não remove outras skills nem diretórios desconhecidos.

| Nome anterior | Nome atual |
|---|---|
| `start-project-runners-before-pr` | `runnerops-pr-validation` |
| `manage-local-github-runners` | `runnerops-manage-runners` |
| `analyze-ci-workflow-performance` | `runnerops-ci-performance` |

## Compatibilidade

As Agent Skills canônicas vivem somente em `skills/` e devem ser instaladas por `runnerctl skills install ...` ou `install-agent-skills.sh`.

Adaptadores específicos de provedor só devem ser introduzidos quando uma ferramenta exigir comportamento que não possa ser expresso pelo `SKILL.md` compartilhado.
