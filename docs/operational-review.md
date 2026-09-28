# AI Operational Review v1

`runnerctl review` produz um `OperationalReview v1` estrito a partir de um único
`OperationalEvidence v1`. O modelo é um analista, não o control plane.

**AI review cannot mutate RunnerOps infrastructure.**

**V1 recommendations are null-only; deterministic RunnerOps
policy/planner/controller remains authoritative.**

O fluxo V1 é uma única passagem, sem agente, memória, ferramentas ou contexto
adicional:

```text
OperationalEvidence
      ↓
structural + secret validation
      ↓
provider privacy preflight
      ↓
canonical safe serialization
      ↓
Review Prompt v2
      ↓
┌──────────────┬──────────────┐
│ Ollama       │ LiteLLM      │
│ local model  │ gateway      │
└──────┬───────┴──────┬───────┘
       ↓              ↓
       provider response
              ↓
       strict validation
              ↓
       OperationalReview v1
```

## CLI

Aquisição live reutiliza uma única chamada ao builder de `OperationalEvidence`:

```bash
runnerctl review . \
  --since 24h \
  --provider ollama \
  --model qwen3.5:9b
```

Replay lê o artefato sem consultar GitHub, capacidade ou audit history:

```bash
runnerctl review \
  --evidence evidence.json \
  --provider ollama \
  --model qwen3.5:9b \
  --json
```

`--evidence` não aceita repository nem `--since`. No modo live, `--since` é
obrigatório. `--provider` e `--model` são sempre explícitos. Controles comuns:

- `--base-url URL` seleciona o endpoint;
- `--timeout SECONDS` limita uma chamada, default 120;
- `--max-output-tokens N` limita a saída solicitada, default 2048 e faixa
  64..8192;
- `--allow-remote` confirma explicitamente que `OperationalEvidence` pode
  atravessar uma fronteira de rede ou ser processado pelo Ollama Cloud;
- `--json` emite exatamente um `OperationalReview` ou
  `OperationalReviewError`.

## Providers e rede

Ollama usa `POST /api/chat`, sem streaming ou thinking, com JSON Schema em
`format` e `num_predict` limitado. Desabilitar `think` evita que modelos de
raciocínio consumam o limite de saída antes do documento JSON. O default é
`http://127.0.0.1:11434`; pode ser alterado
por `--base-url` ou `RUNNEROPS_OLLAMA_BASE_URL`. RunnerOps nunca instala Ollama,
inicia o serviço ou baixa modelos.

`provider=ollama` não significa necessariamente inferência local. Antes de
construir ou enviar o prompt, RunnerOps aplica estas regras:

1. somente `localhost` (com ponto final opcional) e endereços IP que
   `ipaddress.is_loopback` reconhece são endpoints loopback;
2. qualquer outro host no base URL é uma fronteira remota e exige
   `--allow-remote`, sem tentativa de conexão quando a flag está ausente;
3. para endpoint loopback, RunnerOps envia `POST /api/show` contendo somente
   `{"model":"<nome>"}`. `remote_model` ou `remote_host` não vazio classifica o
   modelo como Ollama Cloud e exige `--allow-remote`;
4. sem esses campos remotos, o modelo só é aceito como local quando
   `model_info.general.architecture` é uma string não vazia e sem controles.
   `model_info` ausente, vazio, com campos irrelevantes, valor nulo ou tipo
   incorreto falha com `OLLAMA_MODEL_LOCATION_UNKNOWN`.

Essa regra usa os campos machine-readable do Ollama para modelos remotos, não
um sufixo ou substring do nome. Os campos seguem o
[contrato oficial da API do Ollama](https://github.com/ollama/ollama/blob/main/api/types.go).
O preflight `/api/show` nunca recebe bytes de
`OperationalEvidence`. Apenas depois da classificação e, quando necessária, da
confirmação `--allow-remote`, RunnerOps serializa a evidência no prompt e chama
`POST /api/chat`.

Para um endpoint estável fora do localhost, mantenha a configuração específica
da máquina fora do checkout Git e confirme a fronteira em cada execução:

```bash
# ~/.config/actions-runners/config.env
RUNNEROPS_OLLAMA_BASE_URL="http://wsl-host.example:11434"
runnerctl review . --since 24h --provider ollama --model qwen3.5:9b --allow-remote
```

`runnerctl` carrega esse `config.env`. Sem uma entrada persistida, a variável
exportada no ambiente fornece o endpoint; `--base-url` sobrescreve ambos para
uma execução específica.

```text
RunnerOps → configured Ollama endpoint
```

LiteLLM usa a superfície OpenAI-compatible
`POST /v1/chat/completions`. O default é `http://127.0.0.1:4000`; pode ser
alterado por `--base-url` ou `RUNNEROPS_LITELLM_BASE_URL`. A chave vem somente de
`RUNNEROPS_LITELLM_API_KEY` e é enviada como Bearer token. Não há flag de chave,
para não gravá-la no histórico do shell.

```text
RunnerOps → configured LiteLLM gateway → upstream provider
```

RunnerOps não conhece nem roteia o vendor upstream. Todo uso LiteLLM exige
`--allow-remote`, inclusive quando o gateway está em localhost, porque ele ainda
pode encaminhar evidência a um serviço remoto. A checagem ocorre antes da
chamada. Não há adapters diretos de OpenAI, Anthropic, Gemini ou outros vendors.
Todos os requests de provider rejeitam HTTP 30x. RunnerOps não segue redirect
nem em `/api/show`, nem em `/api/chat`, nem em `/v1/chat/completions`; portanto
`Authorization` e a evidência nunca são encaminhados implicitamente a outro URL.

## Serialização e digest

Antes da inferência, a evidência é validada como `schema_version: 1` e
`kind: OperationalEvidence`. O limite é 256 KiB. A serialização usa UTF-8,
chaves ordenadas, separadores JSON compactos, sem whitespace arbitrário,
timestamps novos ou mutação do objeto. `SHA-256(canonical evidence bytes)` é
gravado em `evidence.sha256`, permitindo replay e comparação entre modelos.

Campos inesperados com nomes associados a token, credencial, authorization,
password, secret, environment, registry, raw log ou stderr são rejeitados; eles
não são removidos silenciosamente nem enviados. A lista de campos do collector
é a allowlist já definida pelo contrato de OperationalEvidence. RunnerOps não
envia ambiente, inventário de filesystem, conteúdo do registry ou logs livres.

A evidência é delimitada como dados não confiáveis no prompt estático
`runnerops-operational-review-v2`. O prompt proíbe seguir instruções contidas em
valores, transformar `null` em zero, inventar fatos ausentes, converter uma
observação atual em histórico ou declarar causa a partir de correlação.

## OperationalReview v1

Metadados de provider, modelo, prompt, evidência, digest, período e uso são
montados pelo RunnerOps; o modelo não pode fornecê-los ou substituí-los.

```json
{
  "schema_version": 1,
  "kind": "OperationalReview",
  "prompt_version": "runnerops-operational-review-v2",
  "provider": {"name": "ollama", "model": "qwen3.5:9b"},
  "evidence": {
    "kind": "OperationalEvidence",
    "schema_version": 1,
    "sha256": "<64 lowercase hex>",
    "period": {"from": "...", "to": "..."},
    "repository": "owner/repo"
  },
  "findings": [
    {
      "id": "F001",
      "category": "CAPACITY",
      "confidence": "high",
      "claim_kind": "BOUNDED_CURRENT_INFERENCE",
      "observation": "Cited evidence values: /capacity/latest/available_now=5; /capacity/queue/queued_job_count=0.",
      "inference": "The cited values support only a current-snapshot inference; historical behavior, capacity sizing, and root cause remain undetermined.",
      "recommendation": null,
      "evidence_refs": [
        "/capacity/latest/available_now",
        "/capacity/queue/queued_job_count"
      ]
    }
  ],
  "unknowns": [
    {
      "reasons": ["historical_capacity_not_persisted"],
      "summary": "Incomplete evidence remains unknown: historical_capacity_not_persisted.",
      "evidence_refs": ["/incomplete_evidence/0/reason"]
    }
  ],
  "usage": {
    "input_tokens": null,
    "output_tokens": null,
    "latency_ms": 1234,
    "provider_cost": null
  }
}
```

Categorias aceitas: `CI`, `CAPACITY`, `AUTOSCALE`, `COLLECTOR` e `EVIDENCE`.
Confiança aceita: `low`, `medium` e `high`. Há no máximo 2 findings, 6
unknowns e 20 referências por item; textos têm no máximo 2000 caracteres. Todo
finding e unknown exige pelo menos uma referência.

O modelo não fornece `observation`, `inference`, `recommendation` ou `summary`.
Sua resposta contém somente IDs, categoria, confiança, `claim_kind`, motivos
estruturados e referências folha. RunnerOps deriva a prosa do artefato depois da
validação. Os `claim_kind` aceitos são:

- `EVIDENCE_OBSERVATION`: enumeração literal dos valores citados;
- `BOUNDED_CURRENT_INFERENCE`: inferência explicitamente limitada ao snapshot;
- `BOUNDED_PERIOD_INFERENCE`: inferência explicitamente limitada ao período.

Não existem classes V1 para `CAPACITY_SIZING`, `HISTORICAL_TREND` ou
`ROOT_CAUSE`. Toda recomendação é `null`. Assim, mudar `category` não permite
converter snapshot ocioso em sizing, overprovisioning, redução de runners ou
causa raiz.

Referências são JSON Pointer RFC 6901 e precisam resolver no documento exato
serializado. Sintaxe inválida, índice de array inválido ou caminho inexistente
rejeita a resposta completa. Saídas malformadas nunca viram review parcial.
O prompt inclui um índice determinístico de no máximo 512 JSON Pointers folha e
seus valores exatos, exigindo que o modelo copie referências desse índice. O
índice também é marcado como evidência não confiável. O validator ainda
resolve cada referência contra a evidência original; o catálogo não concede
confiança ao output do modelo.

Cada entrada de `incomplete_evidence` precisa ser citada pelo pointer exato
`/incomplete_evidence/N/reason`. O campo estruturado `reasons` precisa coincidir
exatamente com os valores citados; `summary` é derivado pelo RunnerOps. O modelo
não pode citar um gap e negar sua incerteza em prosa livre. Findings sustentados
somente por gaps precisam ser `EVIDENCE`.

## Source map e validação

| Campo/saída | Origem | Autoridade | Validação |
|---|---|---|---|
| `evidence.repository` | `/repository/nameWithOwner`, fallback `/repository/requested` | RunnerOps | string bounded |
| `evidence.period` | `/period/from`, `/period/to` | RunnerOps | contrato inclusive/exclusive de v1 |
| `evidence.sha256` | bytes JSON canônicos completos | RunnerOps | SHA-256 lowercase de 64 hex |
| `provider` e `prompt_version` | configuração CLI e constante do produto | RunnerOps | providers/model/prompt permitidos |
| `findings.id/category/confidence/claim_kind/refs` | resposta do modelo | modelo, sob enum e refs folha | schema, tipos, vocabulário, limites e pré-requisitos do claim |
| `findings.observation/inference/recommendation` | derivação determinística | RunnerOps | recomputada na validação; recommendation null-only |
| `unknowns.reasons/refs` | resposta estruturada do modelo | modelo, sob valores exatos da evidência | cada gap obrigatório e reason exato |
| `unknowns.summary` | derivação determinística | RunnerOps | recomputada na validação |
| `usage.input_tokens/output_tokens` | campos do provider, quando presentes | provider | inteiro não negativo ou `null` |
| `usage.latency_ms` | relógio monotônico ao redor da chamada | RunnerOps | inteiro não negativo |
| `usage.provider_cost` | header explícito `x-litellm-response-cost` | LiteLLM | número finito não negativo ou `null` |

RunnerOps não calcula custo com tabelas de preço. Ollama fornece contagens
`prompt_eval_count` e `eval_count`; LiteLLM fornece `usage.prompt_tokens` e
`usage.completion_tokens` quando o upstream as retorna.

## Falhas e privacidade

Timeout, conexão recusada, auth, modelo/endpoint ausente, redirect HTTP, HTTP 4xx/5xx, JSON
malformado, resposta grande demais, review inválido e referência inexistente
falham fechados. O exit code é 2 para configuração/argumentos e 3 para falha de
runtime/provider/validação. Não há traceback para falhas esperadas. JSON usa um
único envelope `OperationalReviewError`; mensagens não incluem body bruto,
Authorization ou chave.

Strings de evidência e campos estruturados rejeitam C0/C1, ESC, DEL, quebras de
linha, separadores de linha/parágrafo e controles Unicode de formatação/bidi.
A prosa derivada também é validada antes de entrar no artefato ou terminal.

O gateway LiteLLM pode ter logging próprio. Quem o opera é responsável por
configurar retenção, redaction e o upstream adequado ao nível de sensibilidade
da evidência. `--allow-remote` confirma a transferência, não altera a política
do gateway.

## Limitações V1

- uma chamada e uma resposta; sem retry, fallback, tools, agentes ou drill-down;
- V1 privilegia segurança: apenas observações e inferências bounded são
  representáveis; não há prosa livre do modelo, sizing, causa raiz ou recomendação;
- disponibilidade histórica ausente deve permanecer unknown; capacidade atual
  ociosa não prova overprovisioning;
- sem benchmark multi-model, memória, RAG, embeddings ou banco adicional;
- sem adapters diretos de vendors e sem dependências Python de terceiros;
- nenhum download automático de modelo ou dogfood remoto pago.
