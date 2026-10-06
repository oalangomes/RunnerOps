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
| `runnerops-pr-validation` | Validar PRs sem acordar capacidade repository-wide preventivamente; inspecionar capacity/autoscale e consumir `runnerctl ci watch` após a publicação. |
| `runnerops-manage-runners` | Operar capacidade com escopo seguro: inventário, lifecycle exato, autoscale governado, cadastro/recovery, ephemeral one-job explícito, remoção e feedback de CI. |
| `runnerops-ci-performance` | Analisar DAG, critical path, repetição, cache, artifacts, triggers, fila e capacidade/autoscale usando evidência STATIC / OBSERVED / ESTIMATED; read-only por padrão. |

## Semântica operacional da skill de gestão

As skills seguem uma política **capacity-first**: primeiro observam `overview`, `capacity` e o estado do autoscale; depois deixam o planner/controller governado agir ou operam um runner exato quando isso é realmente necessário.

A `runnerops-manage-runners` trata grupos como agrupamentos operacionais, não como boundary de repositório. `runnerctl ensure .` é um override manual amplo para ativar todos os runners habilitados daquele repositório e não é a preferência padrão.

Ela também distingue:

- **provisionado/ocioso** — `inactive + boot disabled + on-demand`;
- **disponível agora** — runner ativo e, quando necessário para o objetivo, confirmado online no GitHub;
- **inconclusivo** — lifecycle unknown/query-error ou provisioning marcado como `INCONCLUSIVE`.

Após `start` ou `restart` explícito, a skill exige `status` + `health`. Após `PARTIAL` / `INCONCLUSIVE` em cadastro, não repete `runnerctl add` automaticamente.

O autoscale contínuo continua sendo determinístico e governado por RunnerOps; a skill não substitui decisões do planner por “ligar tudo”. Com policy explícita e limites, o planner pode escolher `CREATE_EPHEMERAL` e o controller reconcilia a identidade exata pelo lifecycle one-job existente. `ephemeral create` continua disponível como primitive explícito, mas a skill não o escolhe por conta própria diante de fila.

## Instalação

Instale as skills canônicas e a projeção global do operador nos destinos de usuário suportados (Codex, Copilot, Claude e agentes genéricos):

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

A instalação global é separada do install por repositório: o operador canônico continua em `agents/runnerops-operator/AGENT.md`, enquanto cada provider recebe apenas uma projeção fina compatível do mesmo conteúdo.

Instale somente uma skill:

O installer descobre automaticamente cada `skills/<name>/SKILL.md`: o mesmo nome listado por `--list` é aceito por `--skill <name>`, sem cadastro manual. Targets inexistentes falham antes de qualquer escrita. `runnerops-operator` é a exceção explícita de Agent, não uma Skill.

```bash
./scripts/setup/install-agent-skills.sh \
  --tool claude \
  --skill runnerops-manage-runners
```

Instale o operador canônico em um provider específico:

```bash
./scripts/setup/install-agent-skills.sh --tool codex --skill runnerops-operator
```

Visualize o que seria feito sem gravar:

```bash
./scripts/setup/install-agent-skills.sh --tool all --dry-run
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
./scripts/setup/install-agent-skills.sh \
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

As Agent Skills canônicas vivem somente em `skills/` e devem ser instaladas por `runnerctl skills install ...` ou pelo helper interno `scripts/setup/install-agent-skills.sh`.

Adaptadores específicos de provedor só devem ser introduzidos quando uma ferramenta exigir comportamento que não possa ser expresso pelo `SKILL.md` compartilhado.
