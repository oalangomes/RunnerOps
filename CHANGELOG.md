# Changelog

Mudanças relevantes do RunnerOps e de sua CLI pública `runnerctl` são registradas aqui.

O projeto segue versionamento SemVer enquanto a API pública amadurece. Em versões `0.x`, mudanças incompatíveis continuam sendo evitadas e devem ser explicitadas quando inevitáveis.

## Unreleased

### Added

- audit store local e opcional de autoscale com SQLite via biblioteca padrão do Python, schema/migration v1, observações consecutivas de fila, decisões imutáveis, ações correlacionadas e transições idempotentes. Retention e limites de crescimento são configuráveis; registros não incluem snapshots brutos ou diagnósticos livres.
- `runnerctl autoscale history [--since DURATION] [--json]` e `runnerctl autoscale explain --decision ID [--json]` consultam o histórico sem criar ou modificar o banco em `RUNNER_STATE_ROOT/autoscale.db`.
- duração de fila observada pelo RunnerOps usa `first_seen_queued_at`/`last_seen_queued_at` e interrompe continuidade em lacunas, falhas de coleta e saída da fila; `job.created_at` permanece apenas evidência de origem. A gravação é uma API interna, sem planner/controller ou mudanças em `capacity`.
- `runnerctl capacity [owner/repo|.] [--json]` e `runnerctl autoscale status` adicionam observabilidade read-only de fila e capacidade com `CapacitySnapshot` v1, preservando identidade canônica, labels, estados local/remoto e evidência inconclusiva sem mutação.
- a capacidade opcional usa Python 3.8+ somente com biblioteca padrão; `platform-doctor` reporta essa capability sem torná-la requisito para os comandos tradicionais do RunnerOps.

### Fixed

- `runnerctl init` passa a proteger `RUNNER_STATE_ROOT` com modo `0700`, inclusive ao reaplicar a configuração sobre um diretório existente, garantindo compatibilidade entre o fluxo oficial de inicialização e o audit store privado.
- `runnerctl ensure .` deixa de chamar `systemctl start` para runners systemd já ativos; o caminho passa a ser idempotente e não solicita sudo quando nenhuma mutação é necessária.
- lifecycle runtime (`ensure/start/stop/restart`) deixa de abrir prompt interativo de sudo: mutações usam autorização one-time via `runnerctl platform-authorize` e falham rápido quando ela não existe.
- lifecycle systemd desconhecido/query-error continua fail-safe e não dispara mutação privilegiada.

### Security

- novo helper root-owned restringe runtime privilegiado a `start|stop|restart` de units `actions.runner.*.service`; não há `NOPASSWD: ALL` nem acesso arbitrário a `systemctl`.

## v0.2.2 — 2026-09-08

### Fixed

- `runnerctl logs` do backend legado passa a mostrar conteúdo bounded do log local, sinalizar vazio/ausente e incluir automaticamente o `_diag` mais recente quando disponível.
- consultas de lifecycle systemd deixam de converter falha de observação em `STOP`, `idle=true` ou `OK`; estado não observável passa a ser explícito como unknown/query-error.
- o start do backend legado agora aguarda um settle curto, exige evidência do processo do runner (PID isolado não basta), limpa PID stale e retorna erro quando a ativação morre imediatamente.
- `runnerctl add` faz preflight de systemd/sudo antes de solicitar registration token, evitando registro remoto quando a instalação systemd já é sabidamente inviável.
- o cadastro preserva o `nameWithOwner` canônico do GitHub no registry, mantendo comparações de repositório case-insensitive.
- falhas pós-registro passam a expor estados `PARTIAL` / `INCONCLUSIVE` com recuperação explícita, evitando retries cegos de `runnerctl add`.

### Changed

- `runnerops-manage-runners` passa a distinguir capacidade provisionada/ociosa de capacidade disponível agora, exige status/health após start/restart, prefere operações repo-scoped/exatas e trata `PARTIAL`, `INCONCLUSIVE` e lifecycle unknown sem retries cegos.
- Agent Skills passam a usar namespace `runnerops-` para melhorar descoberta manual: `runnerops-manage-runners`, `runnerops-pr-validation` e `runnerops-ci-performance`.
- o installer migra somente os nomes legados conhecidos correspondentes ao instalar uma skill renomeada, evitando descoberta duplicada.

### Validation

- o release gate #64 passou no host real WSL2 + systemd: lifecycle truthfulness, start de runner exato com confirmação `online` no GitHub, preflight de sudo antes do registro remoto, persistência canônica de `nameWithOwner`, provisioning temporário e limpeza governada.
- `runnerctl ci watch` foi validado contra um workflow real e classificou corretamente uma falha de step como `kind=ci`, sem atribuí-la a runner/infraestrutura.
- diagnósticos do backend legado permaneceram cobertos por contratos focados; não havia runner legado no host do release gate.

## v0.2.1 — 2026-09-08

### Changed

- o produto e o repositório público passam a se chamar **RunnerOps**; a CLI pública permanece `runnerctl` para preservar o contrato existente.
- clone, README e portability guard passam a usar a URL canônica do RunnerOps.
- nenhum namespace XDG, contrato de runtime ou comando público foi renomeado.

## v0.2.0 — 2026-09-08

### Added

- `runnerctl ci watch` para correlacionar GitHub Actions por repositório + SHA ou PR, com saída humana/JSON e exit codes estáveis.
- diagnóstico de falha de CI separado de falha de infraestrutura/runner no CI feedback bridge.
- suporte a reruns/`run_attempt`, jobs, steps e identificação de runner/group/labels no feedback estruturado.
- Agent Skill `analyze-ci-workflow-performance`, read-only por padrão e baseada em evidência `STATIC`, `OBSERVED` e `ESTIMATED`.
- contratos automatizados para `runnerctl`, routing/lifecycle, runner package, CI watch e Agent Skills.
- smoke real do CI feedback bridge contra a API do GitHub Actions em pushes para `master`.
- `runnerctl --version` como identidade explícita da CLI instalada.

### Fixed

- `runnerctl ci watch` ignora runs históricos de um SHA reutilizado e mantém a decisão restrita à coorte de execução atual.

### Changed

- a skill pre-PR pode consumir `runnerctl ci watch` depois da publicação para devolver o estado conclusivo do CI.
- a skill de gerenciamento local passou a documentar e consumir o feedback estruturado do CI.
- documentação de instalação/upgrade agora deixa explícito que `install.sh` deve ser executado novamente após atualizar o checkout.
- validação de release passa a distinguir a evidência histórica da v0.1.0 da prova exigida para uma release nova.

## v0.1.0

Primeira release pública do `runnerctl`.

A tag `v0.1.0` permanece imutável no commit original da release.
