# Orientações para contribuidores e agentes

RunnerOps é o produto deste repositório. `runnerctl` é sua interface pública para gerenciar runners locais self-hosted do GitHub Actions.

## Invariantes arquiteturais

- O repositório Git contém o código da plataforma, não o inventário da máquina.
- Configuração, dados, cache e estado específicos da máquina pertencem a `RUNNERS_CONFIG`, `RUNNER_DATA_ROOT`, `RUNNER_CACHE_ROOT` e `RUNNER_STATE_ROOT`; a operação normal não deve gravar no checkout Git.
- Nunca faça commit de registration tokens, conteúdo do registry local da máquina ou credenciais de runners.
- systemd é a autoridade de ciclo de vida para runners migrados.
- `RUNNER_BOOT_POLICY=on-demand` é o padrão, e um runner inativo/com boot desabilitado pode representar capacidade ociosa saudável.
- `runnerctl` é a CLI pública e estável; scripts internos são detalhes de implementação.
- Planejamento de autoscale permanece separado de mutação: `autoscale plan` é read-only; controller/provisioning precisam de slices explícitas.
- `job.created_at`/`queue_age_seconds` são evidência de origem, não relógio de autoscaling; decisões de threshold usam continuidade observada pelo RunnerOps.
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
  init-machine-config.sh sync-local-git-excludes.sh install-agent-skills.sh runnerctl install.sh runner-package.sh \
  ci-watch.sh tests/test-runnerctl-contracts.sh tests/test-runnerctl-routing-contracts.sh \
  tests/test-runner-package-contracts.sh tests/test-ci-watch-contracts.sh tests/test-agent-skills-contracts.sh \
  tests/test-performance-skill-contracts.sh
```

Para mudanças em capacity/autoscale:

```bash
python3 -B -m py_compile capacity.py autoscale_contracts.py autoscale_store.py autoscale_audit.py autoscale_planner.py
python3 -B tests/test-capacity-contracts.py
python3 -B tests/test-capacity-bom-contract.py
python3 -B tests/test-autoscale-audit-contracts.py
python3 -B tests/test-autoscale-planner-contracts.py
```

Para mudanças de autoscale:

```bash
python3 -B tests/test-capacity-contracts.py
python3 -B tests/test-autoscale-audit-contracts.py
python3 -B tests/test-autoscale-planner-contracts.py
python3 -B tests/test-autoscale-controller-contracts.py
```

Para Agent Skills:

```bash
./install-agent-skills.sh --list
./install-agent-skills.sh --tool all --dry-run
```

Ferramentas locais opcionais de navegação de código podem ser usadas quando instaladas, mas não são pré-requisitos para contribuir com este repositório.
