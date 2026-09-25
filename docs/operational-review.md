# AI Operational Review v1

`runnerctl review` produz um `OperationalReview v1` estrito a partir de um único
`OperationalEvidence v1`. O modelo é um analista, não o control plane.

**AI review cannot mutate RunnerOps infrastructure.**

**Recommendations are advisory and evidence-grounded; deterministic RunnerOps
policy/planner/controller remains authoritative.**

O fluxo V1 é uma única passagem, sem agente, memória, ferramentas ou contexto
adicional:

```text
OperationalEvidence
      ↓
canonical safe serialization
      ↓
Review Prompt v1
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
- `--json` emite exatamente um `OperationalReview` ou
  `OperationalReviewError`.

## Providers e rede

Ollama usa `POST /api/chat`, sem streaming, com JSON Schema em `format` e
`num_predict` limitado. O default é `http://127.0.0.1:11434`; pode ser alterado
por `--base-url` ou `RUNNEROPS_OLLAMA_BASE_URL`. RunnerOps nunca instala Ollama,
inicia o serviço ou baixa modelos.

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
`runnerops-operational-review-v1`. O prompt proíbe seguir instruções contidas em
valores, transformar `null` em zero, inventar fatos ausentes, converter uma
observação atual em histórico ou declarar causa a partir de correlação.

## OperationalReview v1

Metadados de provider, modelo, prompt, evidência, digest, período e uso são
montados pelo RunnerOps; o modelo não pode fornecê-los ou substituí-los.

```json
{
  "schema_version": 1,
  "kind": "OperationalReview",
  "prompt_version": "runnerops-operational-review-v1",
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
      "observation": "...",
      "inference": null,
      "recommendation": null,
      "evidence_refs": ["/capacity/latest/available_now"]
    }
  ],
  "unknowns": [
    {
      "summary": "...",
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
Confiança aceita: `low`, `medium` e `high`. Há no máximo 20 findings, 20
unknowns e 20 referências por item; textos têm no máximo 2000 caracteres. Todo
finding e unknown exige pelo menos uma referência.

Referências são JSON Pointer RFC 6901 e precisam resolver no documento exato
serializado. Sintaxe inválida, índice de array inválido ou caminho inexistente
rejeita a resposta completa. Saídas malformadas nunca viram review parcial.

## Source map e validação

| Campo/saída | Origem | Autoridade | Validação |
|---|---|---|---|
| `evidence.repository` | `/repository/nameWithOwner`, fallback `/repository/requested` | RunnerOps | string bounded |
| `evidence.period` | `/period/from`, `/period/to` | RunnerOps | contrato inclusive/exclusive de v1 |
| `evidence.sha256` | bytes JSON canônicos completos | RunnerOps | SHA-256 lowercase de 64 hex |
| `provider` e `prompt_version` | configuração CLI e constante do produto | RunnerOps | providers/model/prompt permitidos |
| `findings.*` | resposta do modelo | modelo | schema, vocabulário, limites e refs existentes |
| `unknowns.*` | resposta do modelo | modelo | schema, limites e refs existentes |
| `usage.input_tokens/output_tokens` | campos do provider, quando presentes | provider | inteiro não negativo ou `null` |
| `usage.latency_ms` | relógio monotônico ao redor da chamada | RunnerOps | inteiro não negativo |
| `usage.provider_cost` | header explícito `x-litellm-response-cost` | LiteLLM | número finito não negativo ou `null` |

RunnerOps não calcula custo com tabelas de preço. Ollama fornece contagens
`prompt_eval_count` e `eval_count`; LiteLLM fornece `usage.prompt_tokens` e
`usage.completion_tokens` quando o upstream as retorna.

## Falhas e privacidade

Timeout, conexão recusada, auth, modelo/endpoint ausente, HTTP 4xx/5xx, JSON
malformado, resposta grande demais, review inválido e referência inexistente
falham fechados. O exit code é 2 para configuração/argumentos e 3 para falha de
runtime/provider/validação. Não há traceback para falhas esperadas. JSON usa um
único envelope `OperationalReviewError`; mensagens não incluem body bruto,
Authorization ou chave.

O gateway LiteLLM pode ter logging próprio. Quem o opera é responsável por
configurar retenção, redaction e o upstream adequado ao nível de sensibilidade
da evidência. `--allow-remote` confirma a transferência, não altera a política
do gateway.

## Limitações V1

- uma chamada e uma resposta; sem retry, fallback, tools, agentes ou drill-down;
- nenhuma avaliação automática da qualidade semântica além de grounding por
  referências e schema;
- disponibilidade histórica ausente deve permanecer unknown; capacidade atual
  ociosa não prova overprovisioning;
- sem benchmark multi-model, memória, RAG, embeddings ou banco adicional;
- sem adapters diretos de vendors e sem dependências Python de terceiros;
- nenhum download automático de modelo ou dogfood remoto pago.
