# GitHub Actions Local Runners

Uma central Linux leve para operar múltiplos GitHub Actions self-hosted runners com **systemd**, configuração local por máquina e execução **on-demand**.

O repositório contém a plataforma de gerenciamento. O inventário real de runners, caminhos locais e credenciais ficam fora do Git.

## O que este projeto oferece

- múltiplos runners por repositório;
- lifecycle systemd-first;
- policy on-demand por padrão;
- registry local por máquina;
- cache persistente fora de `_work`;
- operação por runner, grupo ou frota;
- health, doctor, logs e planejamento de migração;
- Cockpit opcional para UI do host;
- Agent Skills portáveis para Codex, GitHub Copilot CLI, Claude Code e clientes compatíveis.

## Plataformas suportadas

| Ambiente | Estado |
|---|---|
| Linux x64 + systemd | ✅ suportado |
| Linux arm64 + systemd | ✅ suportado |
| WSL2 com systemd habilitado | ✅ suportado |
| macOS nativo | ❌ fora do escopo |
| Windows nativo | ❌ fora do escopo |

O produto é **Linux + systemd**. WSL2 é apenas um ambiente Linux suportado; macOS exigiria `launchd` e Windows exigiria um backend de Windows Services, ambos fora do escopo atual.

## Catálogo de funcionalidades

| Capacidade | Interface pública |
|---|---|
| Inicializar máquina | `runnerctl init` |
| Inventário e grupos | `runnerctl list`, `runnerctl groups` |
| Status e saúde | `runnerctl status`, `runnerctl health`, `runnerctl doctor` |
| Lifecycle | `runnerctl start/stop/restart/logs` |
| On-demand / autostart | `runnerctl on-demand`, `runnerctl autostart` |
| Repositório atual | `runnerctl repo .`, `runnerctl ensure .` |
| Registrar runner | `runnerctl add .` |
| Remover runner | `runnerctl remove <runner> --plan/--yes` |
| Pacote oficial do runner | `runnerctl package detect/ensure` |
| Agent Skills | `runnerctl skills list/install` |
| Diagnóstico da plataforma | `runnerctl platform-doctor` |

## Modelo

```text
GitHub repository
      │
      ▼
machine-local registry
      │
      ▼
runners.sh / runner-services.sh
      │
      ▼
systemd unit per runner
      │
      ├── idle + boot disabled   ← default on-demand
      └── active                ← when a job/project needs it
```

## Quick start

### 1. Clone

```bash
git clone https://github.com/<owner>/actions-runners.git ~/actions-runners
cd ~/actions-runners
```

### 2. Install the public CLI

```bash
./install.sh
```

This installs `runnerctl` under `~/.local/bin` and stores the checkout location in XDG config, so agents and humans do not need to know where the repository was cloned.

Then initialize machine-local state:

```bash
runnerctl init
```

Isso cria, por padrão:

```text
~/.config/actions-runners/config.env
~/.config/actions-runners/runners.conf
~/.local/share/actions-runners/runners/
~/.cache/actions-runners/
~/.local/state/actions-runners/
```

O `config.env` aponta para o estado desta máquina. Config, data, cache e runtime state ficam fora do checkout:

```bash
ACTIONS_RUNNERS_HOME="/path/to/actions-runners"
RUNNERS_CONFIG="$HOME/.config/actions-runners/runners.conf"
RUNNER_DATA_ROOT="$HOME/.local/share/actions-runners/runners"
RUNNER_CACHE_ROOT="$HOME/.cache/actions-runners"
RUNNER_STATE_ROOT="$HOME/.local/state/actions-runners"
RUNNER_BOOT_POLICY="on-demand"
```

A lista real de runners **não é versionada** e o checkout pode permanecer read-only durante operação normal.

### 3. Register a runner

Authenticate GitHub CLI once:

```bash
gh auth status
```

Inside the target repository:

```bash
runnerctl add .
```

The command:

- resolves the current `owner/repo`;
- infers a technical profile from project files;
- requests a short-lived registration token through `gh`;
- detects Linux architecture (`x64` or `arm64`);
- resolves the latest official `actions/runner` release;
- downloads it to XDG cache;
- verifies the SHA-256 digest published by GitHub;
- registers the runner;
- installs the systemd service;
- validates doctor/health.

Overrides remain available when needed:

```bash
runnerctl add . \
  --profile python \
  --group backend \
  --runner-version latest \
  --runner-arch auto
```

For offline/manual package control, `configure-runner.sh --runner-tar ... --expected-sha256 ...` remains available as an internal/advanced escape hatch.

### 4. On-demand result

With the default `on-demand` policy, registration/migration proves the GitHub session and finishes with:

```text
state=idle
boot=disabled
policy=on-demand
```

O runner não precisa ficar permanentemente ligado.

## Operação diária

Use `runnerctl` as the stable public interface:

```bash
runnerctl list
runnerctl status all
runnerctl health all

runnerctl start my-api
runnerctl stop my-api
runnerctl restart my-api
runnerctl logs my-api

runnerctl remove my-api --plan
```

Por grupo:

```bash
runnerctl start group:my-team
runnerctl health group:my-team
```

Para o repositório atual:

```bash
runnerctl ensure .
```

Evite `start all` no uso normal. O modelo recomendado é acordar somente a capacidade necessária.

### Remoção segura

```bash
runnerctl remove my-api --plan
runnerctl remove my-api --yes
```

A remoção padrão para o runner exato para/desinstala o serviço, valida e remove a registration remota, remove sua entrada do registry e **preserva a pasta local**.

Para apagar também a pasta da instância:

```bash
runnerctl remove my-api --yes --delete-dir
```

Para remover apenas da plataforma local e manter a registration no GitHub:

```bash
runnerctl remove my-api --yes --keep-remote
```

`remove` não aceita `all` nem grupos.

## On-demand e autostart

On-demand é o default:

```bash
runnerctl on-demand my-api
```

Se uma máquina ou runner realmente precisar ficar sempre disponível:

```bash
runnerctl autostart my-api
```

Em on-demand, `inactive + boot disabled` representa um runner saudável em idle.

## Agent Skills

As skills canônicas vivem em:

```text
skills/
├── start-project-runners-before-pr/
│   └── SKILL.md
├── manage-local-github-runners/
│   └── SKILL.md
└── README.md
```

Instale nos três clientes principais:

```bash
runnerctl skills install all
```

Ou escolha um:

```bash
runnerctl skills install codex
runnerctl skills install copilot
runnerctl skills install claude
runnerctl skills install agents
```

A skill `start-project-runners-before-pr` pode acordar apenas os runners associados ao repositório atual antes de publicar uma PR.

A skill `manage-local-github-runners` cobre inventário, health, start/stop, diagnóstico, cadastro e remoção governada de runners.

Veja [skills/README.md](skills/README.md) para destinos e instalação project-local.

## Configuração por máquina

Arquivo de exemplo versionado:

```text
runners.conf.example
```

Registry real:

```text
~/.config/actions-runners/runners.conf
```

Formato:

```properties
# name|path|profile|repo|enabled|group
my-api|/home/me/.local/share/actions-runners/runners/my-api|python|example/my-api|true|my-team
```

O grupo é explícito quando informado. Em registros antigos sem a sexta coluna, o fallback é o slug do repositório.

## Cache persistente

`_work` continua sendo workspace descartável.

O cache durável da plataforma fica fora do checkout, sob `${XDG_CACHE_HOME:-~/.cache}/actions-runners`:

```text
~/.cache/actions-runners/
├── packages/       # tarballs oficiais do GitHub Runner, validados por SHA-256
├── shared/
├── tools/          # tool cache compartilhado
└── stacks/         # npm/pnpm/yarn, pip, Gradle/Maven, Pub, Go, NuGet etc.
```

O prewarm de **GitHub Actions** é a exceção: `prewarm-actions.sh` aquece `<runner>/_work/_actions`, porque essa é a estrutura consumida pelo runner e ela é específica de cada instância.

Runtime state (service env, logs/PIDs legados durante migração) fica sob `${XDG_STATE_HOME:-~/.local/state}/actions-runners`.

Caches podem ser inspecionados com:

```bash
./cache.sh profiles
./cache.sh status --profile python
./cache.sh status --profile node
./cache.sh status --profile flutter
```

Prewarm:

```bash
./prewarm-cache.sh python
./prewarm-actions.sh my-api
```

## Cockpit

Cockpit é opcional e recomendado quando você quer uma UI para serviços, journal, CPU, RAM, disco e processos:

```bash
./setup-cockpit.sh install
```

Não exponha a porta administrativa diretamente à internet. Para acesso remoto, prefira VPN/rede privada.

Mais detalhes em [docs/systemd-cockpit-migration.md](docs/systemd-cockpit-migration.md).

## Workflows de exemplo

```text
templates/
├── smart-runner-router.yml
├── flutter-self-hosted.yml
├── node-self-hosted.yml
└── python-self-hosted.yml
```

Os templates não exigem `x64` por padrão, então podem casar com runners Linux x64 ou arm64 que tenham as labels funcionais necessárias. Adicione uma label de arquitetura somente quando o job realmente depender dela.

Adapte labels e política de fallback ao seu repositório. Não trate os templates como autorização para executar código não confiável em runners persistentes.

## Home lab / host dedicado

Um blueprint genérico para notebook, mini PC ou host Ubuntu dedicado está em:

[docs/notebook-central-blueprint.md](docs/notebook-central-blueprint.md)

## Validação

```bash
bash -n \
  configure-runner.sh \
  runners.sh \
  runner-services.sh \
  runner-runtime-env.sh \
  init-machine-config.sh \
  sync-local-git-excludes.sh \
  install-agent-skills.sh \
  runnerctl \
  install.sh \
  runner-package.sh \
  setup-cockpit.sh \
  cache.sh \
  prewarm-cache.sh \
  prewarm-actions.sh

./install-agent-skills.sh --tool all --dry-run
./runners.sh list
./runners.sh health all
```

## Segurança

- não execute PR externo não confiável em runner persistente;
- não rode runners como root;
- use labels específicas por repositório e capacidade;
- mantenha permissões mínimas no `GITHUB_TOKEN`;
- mantenha registration tokens fora de logs, commits e documentação;
- não exponha Docker socket, bancos ou painéis administrativos à internet;
- use containers ou usuários isolados para código não confiável;
- faça backup de configuração e caches importantes, não de `_work`.

## Documentação

- [Agent Skills](skills/README.md)
- [systemd + Cockpit](docs/systemd-cockpit-migration.md)
- [Home lab blueprint](docs/notebook-central-blueprint.md)

## Estado do projeto

A direção atual é **systemd-first + on-demand + machine-local configuration**.

Compatibilidade com lifecycle legado ainda existe apenas para migração; novas instalações devem usar systemd.
