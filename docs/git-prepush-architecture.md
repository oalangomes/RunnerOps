# Git pre-push hooks: prewarm de capacidade por repositório (proposta)

> **Status:** arquitetura proposta, **não implementada**. Tracking: [issue #153](https://github.com/oalangomes/RunnerOps/issues/153). Nenhum dos comandos `runnerctl hooks` mostrados abaixo está disponível na CLI atual. Não instalar hooks, ativar schedulers ou iniciar runners para validar este documento.

## Objetivo

Permitir que um **push local** antecipe a preparação de **um runner persistente exato**, previamente autorizado e pertencente ao mesmo repositório, sem precisar manter um runner de controle permanentemente online. O mecanismo é um Git Hook **`pre-push` opt-in por clone**, separado da observação contínua de fila feita pelo scheduler systemd do próprio repositório.

**Escopo:** cada repositório tem seu runner-orquestrador opcional, capacidade persistente/ephemeral, labels, configuração, scheduler e política **independentes**. A CLI `runnerctl` pode ser instalada uma vez no host; não existe pool global de execução nem compartilhamento entre repositórios.

## Motivação e garantias

O RunnerOps já fornece `runnerctl start <runner>`, `autoscale plan`, `autoscale status`, `autoscale run-once`, `autoscale enable` e `ephemeral create/status/reconcile/cleanup`. O Git Hook **não** é um novo planner nem um novo controlador de autoscale: é apenas mais um sinal antecipatório para o mecanismo existente.

O `pre-push` é executado **antes** de os commits chegarem ao GitHub. O hook não sabe se o push será aceito, qual workflow será executado ou qual job ficará em fila. Assim:

- Não inferir filas, capacidade disponível nem jobs a partir do hook.
- Não criar `CREATE_EPHEMERAL` diretamente no hook: primeiro deve haver demanda elegível observada e uma decisão governada do planner/controller.
- No modo de prewarm, permitir somente um runner persistente **exato**, autorizável via policy e do mesmo repositório.
- Não fazer `start all`, `ensure` em massa, autostart silencioso ou mudanças em registro remoto.
- Nunca usar IA como autoridade direta para executar ações privilegiadas. Ollama/LiteLLM podem analisar evidências depois.

### Diagrama

```text
                 Repositório R / clone local
                        git push
                           |
              Git pre-push (opt-in, local)
                           |
       verifica identity R + remote + target + policy
                           |
             prewarm idempotente, com TTL/lock
                           |
               START_LOCAL runner exato de R
                           |
         GitHub recebe push (pode falhar ou ser recusado)
                           |
             GitHub Actions publica jobs de R
                           |
        scheduler systemd de R, quando habilitado
              /              |              \
       START_LOCAL       PROVISION_LOCAL   CREATE_EPHEMERAL
         `runnerctl` aplica política, reconcilia e audita
```

Git Hooks são configurados por clone; outro clone, a interface web, um runner hospedado, `workflow_dispatch`, `schedule` e reexecuções não acionam esse hook. O scheduler **por repositório** cobre essas fontes quando estiver habilitado e saudável. O hook não o habilita automaticamente.

## Interfaces públicas propostas (sujeitas a contrato e testes)

```bash
# Propostas, não implementadas:
runnerctl hooks install --repo owner/repo --mode prewarm --runner exact-name --plan
runnerctl hooks install --repo owner/repo --mode prewarm --runner exact-name --yes
runnerctl hooks status --repo owner/repo --json
runnerctl hooks uninstall --repo owner/repo --plan
runnerctl hooks uninstall --repo owner/repo --yes

# Já existentes:
runnerctl autoscale status owner/repo --json
runnerctl autoscale plan owner/repo --json
runnerctl status exact-name
runnerctl health exact-name
```

Considerar no contrato: instalação **idempotente**, desinstalação reversível, `--plan` antes de mutação, fingerprint da configuração, versionamento e tratamento de `core.hooksPath` existente. Nunca sobrescrever um `pre-push` de terceiro: usar dispatcher/chain seguro quando possível ou recusar com diagnóstico claro.

### Política ilustrativa (não suportada ainda)

```json
{
  "schema_version": 1,
  "repository": "owner/repo",
  "pre_push": {
    "enabled": false,
    "mode": "prewarm",
    "target_runner": "repo-runner-1",
    "failure_policy": "warn",
    "cooldown_seconds": 120
  }
}
```

Este JSON **não deve ser interpretado como policy efetiva**. O contrato precisa ser definido/validado antes da implementação. A gestão pública de políticas/approval boundary continua acompanhada em [#144](https://github.com/oalangomes/RunnerOps/issues/144).

## Segurança e confiabilidade

1. **Escopo e identidade:** normalizar o repositório canônico associado ao checkout, ao remote de destino e ao registry local; rejeitar mismatch, remote/fork não autorizado e runner pertencente a outro repositório. O nome do diretório local não é prova suficiente.
2. **Privilégio mínimo:** o hook é código invocado por Git, potencialmente vindo de checkout não confiável. Nunca executar shell arbitrário recebido do workflow, nem conceder controle irrestrito de systemd, credenciais GitHub ou tokens. Definir uma interface controlada de prewarm com allowlist e autorização explícita.
3. **Respeito aos hooks do usuário:** `.git/hooks` e `core.hooksPath` podem conter rotinas existentes. Não sobrescrever silenciosamente, não remover configurações que não pertençam ao RunnerOps, não alterar hooks de outros clones.
4. **Não bloquear o push por padrão:** erro transitório de prewarm gera aviso e evidência, mas não impede `git push`. `strict` é opt-in explícito. Aplicar timeout, lock por repositório/runner, cooldown e proteção contra concorrência e reentrância.
5. **Antecipação não é agendamento:** inclusive `git push --dry-run` pode executar o `pre-push`; não pressupor detecção infalível de dry-run. Mitigar com opt-in, targets exatos, TTL e idempotência. Um push rejeitado não pode manter capacidade ligada indefinidamente.
6. **Recuperação:** o scheduler por repositório continua independente, não depende do hook, e não pode presumir que o hook executou. O controller governa execuções e cleanup, sem desligar capacidade ocupada.
7. **Observabilidade:** status, reason codes, política/fingerprint, resultado de prewarm, timestamps, caminho de recuperação, sem registrar conteúdo de secrets ou tokens.

## Estados e cenários de validação

| Cenário | Comportamento correto |
|---|---|
| Hook não instalado ou policy disabled | Git opera normalmente; nenhum runner ativado |
| `git push` local para repo autorizado | Prewarm de runner persistente exato; sem tocar outros repositórios |
| Runner já ativo | Operação idempotente, sem restart desnecessário |
| Push de fork/remote não autorizado | Nenhuma mutação privilegiada; diagnóstico explícito |
| Falha no systemd ou `runnerctl` ausente | Aviso e push continua no modo padrão |
| Clone com `core.hooksPath` próprio | Coexistência segura ou recusa, sem sobrescrever |
| Jobs remotos, `schedule`, `workflow_dispatch`, rerun | Não dependem do hook; scheduler por repo pode observar |
| Job elegível para ephemeral | Controller decide `CREATE_EPHEMERAL` após evidência real de fila |
| `git push` recusado após prewarm | Cooldown/reconciliation tratam capacidade ociosa; sem desligar job ocupado |
| Auditoria inconclusiva | Nenhuma decisão otimista de autoscale |

## Plano de entrega

1. **Contrato/segurança:** comando, policy por repo, allowlist, semântica de falha, compatibilidade com hooks existentes e migração.
2. **Implementação:** instalador de hook e dispatcher reversível, integração com CLI e lifecycle existente, logs e diagnóstico.
3. **Testes:** testes shell/CLI com Git real temporário, mocks de `runnerctl`, cenários de idempotência/remote/fork/concorrrência/falha; testes de CI sem systemd mutável.
4. **Documentação e operator:** README, guia final de instalação/operação, atualização das Agent Skills pertinentes, `agents/runnerops-operator/AGENT.md`, `AGENTS.md`, `.github/copilot-instructions.md` e seus contratos. O operator continua coordenando por repositório; não vira autoridade independente do planner.
5. **Qualification no host:** validar em **um repositório de laboratório** com um runner exato, sem habilitar automaticamente o scheduler, registrar evidência de push/job/cleanup e só então ampliar.

Referências relacionadas: [#144](https://github.com/oalangomes/RunnerOps/issues/144) (policy), [#146](https://github.com/oalangomes/RunnerOps/issues/146)–[#149](https://github.com/oalangomes/RunnerOps/issues/149) e [PR #150](https://github.com/oalangomes/RunnerOps/pull/150) (workflow audit/adoption/reconciliation/intelligence, integração futura opcional).

> Esta proposta documenta a intenção. A entrega só estará concluída após implementação, testes e qualification real da [#153](https://github.com/oalangomes/RunnerOps/issues/153).
