# systemd + Cockpit

## Modelo atual

O ciclo de vida recomendado é:

```text
registro do runner
      ↓
svc.sh
      ↓
unit systemd
      ↓
journalctl
      ↓
interface opcional do Cockpit
```

`runnerctl` é a interface pública recomendada. Internamente, a plataforma é orientada por systemd. A política de boot padrão é `on-demand`: os serviços ficam instalados, mas desabilitados no boot até que um projeto ou operador precise iniciá-los.

O gerenciamento legado por PID/processo permanece apenas por compatibilidade durante a migração.

## Pré-requisitos

```bash
systemctl --version
test -d /run/systemd/system
```

No WSL, habilite systemd em `/etc/wsl.conf` quando necessário:

```ini
[boot]
systemd=true
```

Depois execute `wsl --shutdown` no PowerShell e reabra a distribuição.

## Inspecione antes de alterar

```bash
runnerctl doctor all
runnerctl list
runnerctl plan all
```

## Migre um runner

```bash
runnerctl migrate my-api
```

A migração:

1. interrompe o processo legado quando ele existir;
2. instala o serviço systemd oficial via `svc.sh`;
3. aplica o drop-in de ambiente de cache;
4. inicia o serviço tempo suficiente para comprovar a sessão com o GitHub;
5. sob `on-demand`, interrompe novamente e deixa o boot desabilitado.

Inspecione:

```bash
runnerctl status my-api
runnerctl logs my-api
runnerctl health my-api
```

Estado ocioso esperado em on-demand:

```text
state=inactive
boot=disabled
policy=on-demand
```

## Grupos

Os grupos vêm do registro local da máquina. Se uma entrada antiga não tiver a coluna de grupo, o fallback é o slug do repositório.

```bash
runnerctl migrate group:my-team
runnerctl status group:my-team
```

Evite migrações grandes antes de revisar o `plan`.

## Alterar política de boot

```bash
runnerctl on-demand my-api
runnerctl autostart my-api
```

Use autostart apenas quando um runner realmente precisar permanecer disponível após o boot do host.

## Cockpit

Instale opcionalmente:

```bash
./setup-cockpit.sh install
```

O Cockpit fornece administração padrão do host e dos serviços para:

- units systemd;
- logs do journal;
- CPU e memória;
- disco e processos.

Não exponha a porta administrativa diretamente à internet pública. Prefira VPN/rede privada e permissões normais de usuário Linux.

## Ambiente de cache

`runner-services.sh` materializa as variáveis de cache em:

```text
${XDG_STATE_HOME:-~/.local/state}/actions-runners/service-env/<runner>.env
```

e cria um drop-in systemd em:

```text
/etc/systemd/system/<unit>.d/10-actions-runners-cache.conf
```

Esses artefatos são locais da máquina e não são versionados.

## Rollback de migração

Para remover somente a integração systemd durante uma migração avançada:

```bash
./runner-services.sh uninstall my-api
```

Esse comando interno preserva o registro no GitHub e o diretório do runner.

Para remoção governada da plataforma, prefira a interface pública:

```bash
runnerctl remove my-api --plan
runnerctl remove my-api --yes
```

O ciclo de vida legado ainda pode ser usado durante migrações, mas novas instalações devem permanecer orientadas por systemd.

## Escala futura

Se um host Linux dedicado eventualmente precisar de runners efêmeros ou scale-to-zero, avalie um gerenciador de runners ou uma camada de virtualização dedicada. Kubernetes não é necessário apenas para operar uma pequena frota local de runners.
