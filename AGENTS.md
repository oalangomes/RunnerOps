# Orientações para contribuidores e agentes

Este repositório gerencia runners locais self-hosted do GitHub Actions.

## Invariantes arquiteturais

- O repositório Git contém o código da plataforma, não o inventário da máquina.
- Configuração, dados, cache e estado específicos da máquina pertencem a `RUNNERS_CONFIG`, `RUNNER_DATA_ROOT`, `RUNNER_CACHE_ROOT` e `RUNNER_STATE_ROOT`; a operação normal não deve gravar no checkout Git.
- Nunca faça commit de registration tokens, conteúdo do registry local da máquina ou credenciais de runners.
- systemd é a autoridade de ciclo de vida para runners migrados.
- `RUNNER_BOOT_POLICY=on-demand` é o padrão, e um runner inativo/com boot desabilitado pode representar capacidade ociosa saudável.
- `runnerctl` é a CLI pública e estável; scripts internos são detalhes de implementação.
- Prefira Agent Skills neutras de provedor em `skills/<nome>/SKILL.md`.
- Não introduza cópias específicas de provedor, a menos que um cliente não consiga expressar o comportamento pela skill compartilhada.

## Disciplina de mudança

- Mantenha exemplos públicos genéricos; não adicione nomes de usuário, hostnames ou projetos do mantenedor.
- Prefira o slug do repositório como grupo padrão; agrupamentos específicos de projeto pertencem à configuração local da máquina.
- Não expanda o ciclo de vida legado baseado em PID. Novas capacidades operacionais devem usar systemd/journal/Cockpit.
- Preserve registrations existentes e diretórios locais de runners, a menos que a mudança trate explicitamente de migração ou remoção.

## Validação

Para mudanças em shell:

```bash
bash -n configure-runner.sh runners.sh runner-services.sh runner-runtime-env.sh \
  init-machine-config.sh sync-local-git-excludes.sh install-agent-skills.sh runnerctl install.sh runner-package.sh
```

Para Agent Skills:

```bash
./install-agent-skills.sh --list
./install-agent-skills.sh --tool all --dry-run
```

Ferramentas locais opcionais de navegação de código podem ser usadas quando instaladas, mas não são pré-requisitos para contribuir com este repositório.
