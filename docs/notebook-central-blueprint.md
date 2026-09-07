# Blueprint de home lab para runners self-hosted

Um notebook, mini PC ou pequeno host Ubuntu pode funcionar muito bem como nó privado de CI/laboratório quando tratado como:

```text
CI privado + laboratório de desenvolvimento + ambientes de teste
```

e não como um serviço público de produção com disponibilidade garantida.

## Topologia sugerida

```text
GitHub
  │
  ▼
Host Ubuntu
├── runners GitHub gerenciados por systemd
├── runnerctl
├── Docker Engine + Compose
├── VPN privada / SSH
├── volumes persistentes
└── backups
      │
      └── workstation/GPU opcional sob demanda
```

## Responsabilidades do host

Execute diretamente no host:

- SSH / VPN privada;
- runners do GitHub Actions;
- systemd e journal;
- `runnerctl`;
- Docker Engine;
- monitoramento do host e backups.

APIs, bancos de dados, filas e stacks de teste específicas de aplicações devem normalmente viver em containers, evitando poluir o host dos runners.

## Layout de dados

Um layout possível:

```text
~/.config/actions-runners/
└── runners.conf

~/.local/share/actions-runners/
└── runners/
    ├── my-api/
    └── my-web/

/srv/stacks/
├── project-a/
└── project-b/

/srv/data/
├── databases/
├── backups/
└── logs/
```

O checkout da plataforma pode ficar em qualquer diretório; as instâncias dos runners não precisam viver dentro dele.

## Grupos e capacidades

Use grupos para representar ownership operacional ou pools, e labels para representar capacidades.

Exemplos:

```text
grupos:
  backend
  frontend
  mobile

labels:
  python
  node
  flutter
  android
  gpu
  heavy
```

Não codifique a taxonomia de projetos de um mantenedor na plataforma; mantenha essas escolhas no registro local.

## Capacidade

Quantidade de runners não é o mesmo que capacidade do host.

Comece com poucos runners concorrentes e observe:

- carga de CPU;
- RAM e swap;
- I/O do SSD;
- temperatura;
- tempo em fila;
- duração dos jobs.

Aumente a concorrência somente quando as medições sustentarem isso.

## Ambientes de teste

Um host privado de runners também pode executar ambientes de integração com Docker Compose:

```text
proxy reverso
├── API
├── web
└── serviços de apoio
    ├── banco de dados
    └── cache/fila
```

Exponha apenas a superfície mínima necessária. Bancos e serviços internos normalmente devem permanecer em redes Docker privadas.

## Acesso remoto

Prefira:

```text
VPN privada
  → SSH
  → Cockpit / APIs de teste
```

Evite encaminhamento público de portas para:

- SSH;
- Cockpit;
- bancos de dados;
- socket do Docker;
- APIs administrativas.

## Worker pesado

Uma workstation mais poderosa pode ser um segundo pool opcional para:

- builds Android/Flutter;
- tarefas de GPU;
- jobs intensivos de CPU;
- paralelismo temporário.

Use labels explícitas para que jobs normais permaneçam no host mais eficiente.

## Confiabilidade

Para um host home lab sempre ligado:

- acompanhe a saúde do SSD;
- monitore temperatura;
- evite sleep/suspend indesejado;
- mantenha backups fora da máquina;
- considere bateria/UPS para host e equipamentos de rede;
- use reinício automático após queda de energia quando suportado.

## Segurança

- não execute pull requests não confiáveis em runners persistentes e privilegiados;
- evite dar acesso irrestrito ao socket do Docker para jobs;
- separe secrets por projeto;
- use tokens GitHub com privilégio mínimo;
- isole tooling autônomo em containers/usuários quando apropriado;
- mantenha superfícies administrativas em rede privada.

## Resultado

```text
pequeno host Ubuntu
→ CI privado previsível + laboratório de testes

workstation pesada opcional
→ capacidade sob demanda

GitHub Actions
→ orquestração e checks

systemd
→ ciclo de vida local dos runners

Docker Compose
→ stacks de teste descartáveis/privadas
```
