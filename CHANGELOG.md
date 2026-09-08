# Changelog

Mudanças relevantes do `runnerctl` são registradas aqui.

O projeto segue versionamento SemVer enquanto a API pública amadurece. Em versões `0.x`, mudanças incompatíveis continuam sendo evitadas e devem ser explicitadas quando inevitáveis.

## Unreleased — v0.2.0

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

### Release gate

Antes de trocar a versão de desenvolvimento por `0.2.0` e criar a tag:

- CI da candidata deve estar verde;
- `runnerctl --version` deve reportar `0.2.0`;
- instalação/upgrade devem estar validados;
- smoke real Linux/systemd ou WSL2/systemd deve passar;
- smoke real do bridge de GitHub Actions deve passar;
- README e changelog devem ser revisados contra a diferença real desde `v0.1.0`.

## v0.1.0

Primeira release pública do `runnerctl`.

A tag `v0.1.0` permanece imutável no commit original da release.
