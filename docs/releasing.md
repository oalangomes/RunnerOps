# Processo de release do RunnerOps

Este processo existe para impedir que uma tag seja criada apenas porque a branch parece pronta.

## 1. Confirmar o delta

Compare a release anterior com a candidata:

```bash
git fetch --tags
PREVIOUS_TAG="$(git describe --tags --abbrev=0)"
git log --oneline "$PREVIOUS_TAG"..HEAD
git diff --stat "$PREVIOUS_TAG"..HEAD
```

Atualize `CHANGELOG.md` somente com mudanças realmente presentes no delta.

## 2. Fechar a versão

Durante desenvolvimento, `runnerctl --version` pode reportar uma versão com sufixo `-dev`.

No commit de release:

- remova o sufixo de desenvolvimento;
- confirme que `runnerctl --version` corresponde exatamente à tag planejada;
- atualize o changelog.

## 3. Rodar validações locais

```bash
bash -n configure-runner.sh runners.sh runner-services.sh runner-runtime-env.sh \
  init-machine-config.sh sync-local-git-excludes.sh install-agent-skills.sh runnerctl install.sh runner-package.sh \
  ci-watch.sh tests/test-runnerctl-contracts.sh tests/test-runnerctl-routing-contracts.sh \
  tests/test-runner-package-contracts.sh tests/test-ci-watch-contracts.sh tests/test-agent-skills-contracts.sh \
  tests/test-performance-skill-contracts.sh

bash tests/test-runnerctl-contracts.sh
bash tests/test-runnerctl-routing-contracts.sh
bash tests/test-runner-package-contracts.sh
bash tests/test-ci-watch-contracts.sh
bash tests/test-agent-skills-contracts.sh
bash tests/test-performance-skill-contracts.sh
```

## 4. Validar instalação e upgrade

Em checkout limpo/arbitrário:

```bash
./install.sh
runnerctl --version
runnerctl platform-home
runnerctl init
runnerctl platform-doctor
```

Para uma instalação existente, prove também que atualizar o checkout e executar `./install.sh` substitui a CLI instalada.

## 5. Smoke real

Antes da tag, execute em Linux + systemd ou WSL2 + systemd:

- registro de um runner descartável ou já reservado para smoke;
- `runnerctl doctor` e `runnerctl health`;
- estado on-demand saudável;
- `runnerctl ensure .` apenas no repositório alvo;
- execução de um workflow self-hosted;
- `runnerctl ci watch` contra GitHub Actions real;
- remoção governada quando o runner for descartável.

Não reutilize automaticamente a evidência de uma release anterior.

## 6. Tag

Somente depois dos gates:

```bash
git status --short
runnerctl --version
VERSION="$(runnerctl --version | awk '{print $2}')"
git tag -a "v$VERSION" -m "RunnerOps v$VERSION"
git push origin "v$VERSION"
```

A tag de uma release publicada é imutável.
