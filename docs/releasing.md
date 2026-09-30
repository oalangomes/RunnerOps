# Processo de release do RunnerOps

RunnerOps publica releases SemVer a partir de `master`.

## Regra padrão

Cada pull request efetivamente mergeado em `master` gera uma nova release com incremento de **PATCH**:

```text
v0.5.0
  ↓ merge PR
v0.5.1
  ↓ merge PR
v0.5.2
```

Push direto em `master` não cria release automática.

A publicação só começa depois que o workflow **Validate runner platform** termina com sucesso para o commit exato de `master`.

## Override por PR

O comportamento padrão pode ser elevado explicitamente antes do merge:

- label `release:minor` → incrementa MINOR e zera PATCH;
- label `release:major` → incrementa MAJOR e zera MINOR/PATCH;
- sem label → PATCH.

As duas labels não podem coexistir no mesmo PR.

Exemplos:

```text
0.5.7 + patch → 0.5.8
0.5.7 + minor → 0.6.0
0.5.7 + major → 1.0.0
```

## Release manual

O workflow **Publish RunnerOps release** também aceita `workflow_dispatch`.

Escolha explicitamente:

- `patch`;
- `minor`;
- `major`.

A execução manual não publica direto. Ela cria primeiro o commit de identidade da nova versão; a tag e o GitHub Release só são publicados depois que esse commit também passa pelo **Validate runner platform**.

## Fluxo automático

```text
PR mergeado em master
        ↓
Validate runner platform
        ↓ success
resolver bump
        ↓
PATCH por padrão
MINOR/MAJOR por override
        ↓
atualizar identidade da versão
        ↓
commit chore(release): vX.Y.Z [release-publish]
        ↓
Validate runner platform
        ↓ success
tag anotada vX.Y.Z
        ↓
GitHub Release
```

Isso mantém a tag apontando para um commit que já passou pelo gate de CI.

## Identidade sincronizada

O helper de release atualiza de forma atômica:

- `runnerctl --version`;
- contrato de versão em `tests/runner/test-runnerctl-contracts.sh`;
- release estável no `README.md`;
- identidade/link de release em `site/index.html`;
- checks de identidade em `.github/workflows/validate.yml`;
- checks de identidade em `.github/workflows/pages.yml`;
- `CHANGELOG.md`.

O helper falha fechado quando a identidade esperada não aparece exatamente onde deveria.

## Release notes

Se `docs/release-notes.md` já estiver preparado para a versão que está sendo publicada, esse arquivo é usado como corpo do GitHub Release.

Caso contrário, o GitHub Release usa notas geradas automaticamente.

Isso permite:

- PATCH diário sem manutenção manual de release notes;
- notas editoriais quando houver um corte MINOR/MAJOR ou marco relevante.

## Bootstrap / recuperação

Se o código em `master` já declara uma versão que ainda não possui tag, uma execução automática após CI verde publica **essa versão atual** sem avançá-la novamente.

Esse caminho existe para bootstrap/recovery — por exemplo, fechar a baseline `v0.5.0` antes de começar os PATCH automáticos.

Se uma tag existir sem GitHub Release, a automação só recupera a publicação quando a tag aponta para o commit atual esperado.

## Anti-loop

O commit automático de versão usa:

```text
chore(release): vX.Y.Z [release-publish]
```

O push desse commit é feito com o `GITHUB_TOKEN`, então o próprio release workflow dispara explicitamente um `workflow_dispatch` de `validate.yml` para validar o commit gerado antes da publicação.

Se esse commit for observado novamente por um gatilho de release, o marcador faz a execução **pular** em vez de calcular outro bump.

A criação da tag não dispara novo bump.

## Concorrência

Releases usam um único grupo de concorrência e não cancelam execuções em andamento.

Além disso, o workflow exige que o SHA validado continue sendo o HEAD exato de `master` antes de preparar uma nova versão. Se `master` avançar enquanto um release está sendo resolvido, a automação falha em vez de versionar código que ainda não passou pelo gate correspondente.

Na prática, evite mergear outro PR enquanto a versão do merge anterior ainda está sendo preparada.

## Validação local do helper

```bash
python3 -B tests/release/test-release-version.py
```

O helper usa apenas a biblioteca padrão do Python.

## Releases publicadas são imutáveis

Tags/releases já publicadas não devem ser movidas.

Se uma publicação parcial falhar, recupere o mesmo alvo; não reaproveite a versão para outro commit.
