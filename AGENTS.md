# Orientações para contribuidores e agentes

RunnerOps é o produto deste repositório. `runnerctl` é sua interface pública para gerenciar runners locais self-hosted do GitHub Actions.

## Invariantes arquiteturais

- O repositório Git contém o código da plataforma, não o inventário da máquina.
- Configuração, dados, cache e estado específicos da máquina pertencem a `RUNNERS_CONFIG`, `RUNNER_DATA_ROOT`, `RUNNER_CACHE_ROOT` e `RUNNER_STATE_ROOT`; a operação normal não deve gravar no checkout Git.
- Nunca faça commit de registration tokens, conteúdo do registry local da máquina ou credenciais de runners.
- systemd é a autoridade de ciclo de vida para runners migrados.
- systemd também é a autoridade local do lifecycle ephemeral; PID observado nunca é autoridade de mutação.
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
mapfile -d '' shell_files < <(find scripts tests -type f -name '*.sh' -print0)
bash -n runnerctl install.sh scripts/systemd/runnerops-systemctl "${shell_files[@]}"
```

Para mudanças em capacity/autoscale:

```bash
python3 -B -m py_compile $(find src/runnerops -type f -name '*.py' -print)
python3 -B tests/capacity/test-capacity-contracts.py
python3 -B tests/capacity/test-capacity-bom-contract.py
python3 -B tests/autoscale/test-autoscale-audit-contracts.py
python3 -B tests/autoscale/test-autoscale-planner-contracts.py
```

Para mudanças de autoscale:

```bash
python3 -B tests/capacity/test-capacity-contracts.py
python3 -B tests/autoscale/test-autoscale-audit-contracts.py
python3 -B tests/autoscale/test-autoscale-planner-contracts.py
python3 -B tests/autoscale/test-autoscale-controller-contracts.py
python3 -B tests/autoscale/test-autoscale-scheduler-contracts.py
```

Para mudanças de lifecycle ephemeral:

```bash
python3 -B -m py_compile $(find src/runnerops/ephemeral -type f -name '*.py' -print)
for test_file in tests/ephemeral/*.py; do python3 -B "$test_file"; done
for test_file in tests/ephemeral/*.sh; do bash "$test_file"; done
```

Para Agent Skills:

```bash
./scripts/setup/install-agent-skills.sh --list
./scripts/setup/install-agent-skills.sh --tool all --dry-run
```

Ferramentas locais opcionais de navegação de código podem ser usadas quando instaladas, mas não são pré-requisitos para contribuir com este repositório.
