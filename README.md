# RunnerOps

**Release estável atual:** [v0.2.0](https://github.com/oalangomes/RunnerOps/releases/tag/v0.2.0)

**CLI pública:** `runnerctl`

RunnerOps é uma central Linux leve para operar múltiplos runners self-hosted do GitHub Actions com **systemd**, configuração local por máquina e execução **on-demand**.

O repositório contém a plataforma de gerenciamento. O inventário real de runners, caminhos locais e credenciais ficam fora do Git.

## O que este projeto oferece

- múltiplos runners por repositório;
- ciclo de vida orientado por systemd;
- política on-demand por padrão;
- registro local por máquina;
- cache persistente fora de `_work`;
- operação por runner, grupo ou frota;
- health, doctor, logs e planejamento de migração;
- Cockpit opcional para interface administrativa do host;
- Agent Skills portáveis para Codex, GitHub Copilot CLI, Claude Code e clientes compatíveis.

## Plataformas suportadas

| Ambiente | Estado |
|---|---|
| Linux x64 + systemd | ✅ suportado |
| Linux arm64 + systemd | ✅ suportado |
| WSL2 com systemd habilitado | ✅ suportado |
| macOS nativo | ❌ fora do escopo |
| Windows nativo | ❌ fora do escopo |

RunnerOps é **Linux + systemd**. WSL2 é apenas um ambiente Linux suportado; macOS exigiria `launchd` e Windows exigiria um backend de Windows Services, ambos fora do escopo atual.

## Catálogo de funcionalidades

| Capacidade | Interface pública |
|---|---|
| Inicializar a máquina | `runnerctl init` |
| Inventário e grupos | `runnerctl list`, `runnerctl groups` |
| Status e saúde | `runnerctl status`, `runnerctl health`, `runnerctl doctor` |
| Ciclo de vida | `runnerctl start/stop/restart/logs` |
| On-demand / autostart | `runnerctl on-demand`, `runnerctl autostart` |
| Repositório atual | `runnerctl repo .`, `runnerctl ensure .` |
| Registrar runner | `runnerctl add .` |
| Remover runner | `runnerctl remove <runner> --plan/--yes` |
| Pacote oficial do runner | `runnerctl package detect/ensure` |
| Aguardar resultado do CI | `runnerctl ci watch .` |
| Agent Skills | `runnerctl skills list/install` |
| Diagnóstico da plataforma | `runnerctl platform-doctor` |

## Modelo

```text
humano / agente
      │
      ▼
   runnerctl
      │
      ▼
scripts internos
      │
      ▼
unidade systemd por runner
      │
      ├── ocioso + boot desabilitado   ← padrão on-demand
      └── ativo                        ← quando um job/projeto precisa
```

RunnerOps é o produto; `runnerctl` é sua interface pública estável. `runners.sh`, `runner-services.sh` e os demais scripts do checkout são detalhes de implementação e migração.

## Pré-requisitos

Em uma máquina já preparada, a instalação leva poucos minutos.

Você precisa de:

- Linux x64 ou arm64 com systemd;
- Git;
- GitHub CLI (`gh`);
- `tar`;
- `sha256sum`;
- `sudo` para instalação e controle das units systemd.

No WSL2, habilite systemd antes de usar a plataforma.

## Início rápido

### 1. Clone

```bash
git clone https://github.com/oalangomes/RunnerOps.git ~/runnerops
cd ~/runnerops
```

Para uma instalação estável, prefira uma tag publicada (`vX.Y.Z`). A branch `master` representa o estado de desenvolvimento entre releases.

### 2. Instale a CLI pública

```bash
./install.sh
```

Isso instala `runnerctl` em `~/.local/bin` e salva a localização do checkout na configuração XDG. Humanos e agentes não precisam saber onde o repositório foi clonado.

Confirme a versão instalada:

```bash
runnerctl --version
```

Depois, inicialize o estado local da máquina:

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

O `config.env` aponta para o estado desta máquina. Configuração, dados, cache e estado de runtime ficam fora do checkout:

```bash
ACTIONS_RUNNERS_HOME="/path/to/runnerops"
RUNNERS_CONFIG="$HOME/.config/actions-runners/runners.conf"
RUNNER_DATA_ROOT="$HOME/.local/share/actions-runners/runners"
RUNNER_CACHE_ROOT="$HOME/.cache/actions-runners"
RUNNER_STATE_ROOT="$HOME/.local/state/actions-runners"
RUNNER_BOOT_POLICY="on-demand"
```

A lista real de runners **não é versionada** e o checkout pode permanecer somente leitura durante a operação normal.

### 3. Registre um runner

Autentique a GitHub CLI uma vez:

```bash
gh auth status
```

Dentro do repositório alvo:

```bash
runnerctl add .
```

O comando:

- resolve o `owner/repo` atual;
- infere um perfil técnico a partir dos arquivos do projeto;
- solicita um registration token de curta duração via `gh`;
- detecta a arquitetura Linux (`x64` ou `arm64`);
- resolve a release oficial mais recente de `actions/runner`;
- baixa o pacote para o cache XDG;
- verifica o digest SHA-256 publicado pelo GitHub;
- registra o runner;
- instala o serviço systemd;
- valida doctor/health.

Sobrescritas continuam disponíveis quando necessário:

```bash
runnerctl add . \
  --profile python \
  --group backend \
  --runner-version latest \
  --runner-arch auto
```

Para controle manual/offline do pacote, `configure-runner.sh --runner-tar ... --expected-sha256 ...` permanece disponível como escape hatch interno/avançado.

### 4. Resultado on-demand

Com a política padrão `on-demand`, o registro/migração comprova a sessão com o GitHub e termina com:

```text
state=idle
boot=disabled
policy=on-demand
```

O runner não precisa ficar permanentemente ligado.

## Atualização

`install.sh` copia a CLI pública para `~/.local/bin`. Portanto, atualizar somente o checkout Git pode deixar um `runnerctl` antigo chamando scripts mais novos.

Para atualizar uma instalação que acompanha `master`:

```bash
cd "$(runnerctl platform-home)"
git status --short
git pull --ff-only
./install.sh
runnerctl --version
runnerctl platform-doctor
```

Para mudar para uma release específica, faça checkout da tag desejada e **execute `./install.sh` novamente**:

```bash
cd "$(runnerctl platform-home)"
git fetch --tags
git checkout vX.Y.Z
./install.sh
runnerctl --version
runnerctl platform-doctor
```

Não faça upgrade sobre um checkout com alterações locais sem antes revisá-las.

## Operação diária

Use `runnerctl` como interface pública estável:

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

Evite `start all` no uso normal. O modelo recomendado é acordar apenas a capacidade necessária.

### Aguardar o CI do commit atual

Depois de publicar um push ou PR, o `runnerctl` pode aguardar os workflows associados ao SHA atual:

```bash
runnerctl ci watch .
```

Para agentes e harnesses, use a saída estruturada:

```bash
runnerctl ci watch . --json
```

O watcher correlaciona `owner/repo + SHA`, observa a coorte de execução atual do commit e não altera o lifecycle dos runners. Runs históricos do mesmo SHA ficam fora da decisão; múltiplos workflows pertencentes à coorte atual continuam agregados.

Exit codes:

- `0` — todos os workflows observados concluíram com sucesso, neutral ou skipped;
- `1` — workflow concluído com falha;
- `2` — falha de infraestrutura/acesso ao GitHub;
- `3` — timeout, cancelamento ou resultado inconclusivo.

Por padrão, a espera usa polling moderado e exige duas leituras terminais estáveis antes de declarar sucesso, reduzindo o risco de concluir antes de um segundo workflow aparecer.

Opções principais:

```bash
runnerctl ci watch . --timeout 900 --interval 5
runnerctl ci watch owner/repo --sha <commit-sha> --json
runnerctl ci watch . --pr 123 --json
```

Falha de teste/build **não** é tratada como falha do runner e o watcher não para, reinicia nem remove serviços.

Quando `--pr NUMERO` é usado, o watcher resolve o `head.sha` atual da PR antes de buscar os workflows. `--sha` e `--pr` são mutuamente exclusivos para evitar correlação ambígua.

Em reruns, o `run_attempt` faz parte da correlação. Detalhes de jobs são consultados no endpoint do attempt exato, evitando reutilizar step/job de uma tentativa anterior.

Quando um workflow falha, o payload estruturado também tenta identificar `job`, `step`, runner, grupo e labels associados à falha.

Em timeout com job `self-hosted` ainda em fila, o watcher consulta a capacidade disponível para as labels requeridas:

- nenhum runner compatível online → `kind=infra`, exit `2`, com orientação para `runnerctl doctor/health`;
- runners compatíveis apenas ocupados → continua `inconclusive`, exit `3`;
- runner compatível online ou job já em execução → continua aguardando/termina como timeout inconclusivo.

`startup_failure` do workflow também é classificado como infraestrutura. Nenhum desses diagnósticos executa recuperação automática.

### Remoção segura

```bash
runnerctl remove my-api --plan
runnerctl remove my-api --yes
```

A remoção padrão do runner exato interrompe e desinstala o serviço, valida e remove o registro remoto, remove sua entrada do registro local e **preserva a pasta local**.

Para apagar também a pasta da instância:

```bash
runnerctl remove my-api --yes --delete-dir
```

Para remover apenas da plataforma local e manter o registro no GitHub:

```bash
runnerctl remove my-api --yes --keep-remote
```

`remove` não aceita `all` nem grupos.

## On-demand e autostart

On-demand é o padrão:

```bash
runnerctl on-demand my-api
```

Se uma máquina ou runner realmente precisar ficar sempre disponível:

```bash
runnerctl autostart my-api
```

Em on-demand, `inactive + boot disabled` representa um runner saudável e ocioso.

## Agent Skills

As skills canônicas vivem em:

```text
skills/
├── start-project-runners-before-pr/
│   └── SKILL.md
├── manage-local-github-runners/
│   └── SKILL.md
├── analyze-ci-workflow-performance/
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

A skill `start-project-runners-before-pr` pode acordar apenas os runners associados ao repositório atual antes de publicar uma PR e, depois da publicação, consumir `runnerctl ci watch` para devolver o estado conclusivo do CI ao agente.

A skill `manage-local-github-runners` cobre inventário, health, start/stop, diagnóstico, cadastro, remoção governada e leitura estruturada do feedback de CI.

A skill `analyze-ci-workflow-performance` faz análise read-only de arquitetura/performance do GitHub Actions, separando evidência `STATIC`, `OBSERVED` e `ESTIMATED` e evitando recomendar otimizações sem prova.

Veja [skills/README.md](skills/README.md) para destinos e instalação local por projeto.

## Configuração por máquina

Arquivo de exemplo versionado:

```text
runners.conf.example
```

Registro real:

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

O prewarm de **GitHub Actions** é a exceção: `prewarm-actions.sh` aquece `<runner>/_work/_actions`, porque essa é a estrutura consumida pelo runner e é específica de cada instância.

O estado de runtime (service env, logs/PIDs legados durante migração) fica sob `${XDG_STATE_HOME:-~/.local/state}/actions-runners`.

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

Cockpit é opcional e recomendado quando você quer uma interface para serviços, journal, CPU, RAM, disco e processos:

```bash
./setup-cockpit.sh install
```

Não exponha a porta administrativa diretamente à internet. Para acesso remoto, prefira VPN ou rede privada.

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

Para usuários, prefira a interface pública:

```bash
runnerctl platform-doctor
runnerctl list
runnerctl health all
```

Para contribuidores, o CI valida sintaxe shell, defaults XDG, instalação do `runnerctl`, plano de remoção governada, portabilidade das Agent Skills e ausência de pressupostos específicos da máquina do mantenedor.

A `v0.1.0` foi validada com smoke/E2E real em WSL2 + systemd, incluindo cadastro de runner, execução de workflow self-hosted, remoção governada, checkout em caminho arbitrário e fresh config XDG. A `v0.2.0` repetiu o gate em WSL2 + systemd, comprovando instalação/upgrade, operação on-demand repo-scoped, workflow real em self-hosted runner e o bridge `ci watch` contra GitHub Actions. Mudanças posteriores em `master` não herdam automaticamente essas provas; cada nova release deve repetir o gate de smoke antes da tag.

O bridge de feedback de CI também possui smoke real em push para `master`: `ci-watch.sh` consulta a API real do GitHub Actions com token efêmero `actions:read` e valida um workflow já concluído do SHA anterior, evitando self-watch.

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

- [Changelog](CHANGELOG.md)
- [Processo de release](docs/releasing.md)
- [Agent Skills](skills/README.md)
- [systemd + Cockpit](docs/systemd-cockpit-migration.md)
- [Home lab](docs/notebook-central-blueprint.md)

## Licença

Distribuído sob a licença **Apache License 2.0**. Consulte [LICENSE](LICENSE).

## Estado do projeto

A direção atual é **systemd-first + on-demand + configuração local por máquina**.

A compatibilidade com ciclo de vida legado existe apenas para migração; novas instalações devem usar systemd.
