# AI observability: investigation tracing

> Status: Phase 7 (2026-11-23). Code: [`backend/src/ai_sre/observability/tracing.py`](../backend/src/ai_sre/observability/tracing.py),
> [`integrations/llm/traced.py`](../backend/src/ai_sre/integrations/llm/traced.py). UI: **Investigation → Trace**.

Every investigation is traced with OpenTelemetry. The trace answers: what did the agent do, in
what order, how long did each step take, which tools failed or were retried, how many tokens
and how much money the LLM cost, and what it concluded.

## Span model

```
investigation.run | investigation.resume      one per run segment (start; resume after approval)
  node.<name>                                  one per LangGraph node execution
    tool.<server>.<tool>                       one per MCP tool call (retries inside)
    llm.generate                               one per LLM request (retries inside)
```

| Span | Attributes |
|------|------------|
| `investigation.*` | `ai_sre.investigation.id`, `ai_sre.query` (redacted, ≤300 chars), `ai_sre.reasoner`, `ai_sre.phase`, final result: `ai_sre.result.status/severity/category/component/root_cause/confidence/band/stop_reason/evidence_cited` |
| `node.*` | `ai_sre.node`, `ai_sre.node.attempts` (2 = retried after a transient error), `ai_sre.node.interrupted` (paused for approval); status error when the node failed |
| `tool.*` | `ai_sre.tool.server/name/key`, `ai_sre.round` (investigation iteration), `ai_sre.tool.arguments` (sanitised), `ai_sre.tool.ok`, `ai_sre.tool.attempts/retries`, `ai_sre.tool.error_code`, `ai_sre.evidence.id`, `ai_sre.tool.result_bytes` |
| `llm.generate` | `gen_ai.system`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens/output_tokens`, `gen_ai.request.max_tokens`, `ai_sre.node`, `ai_sre.llm.cost_usd`, `ai_sre.llm.attempts`, `ai_sre.llm.prompt_chars/output_chars`, `error.type`; one `llm.attempt_failed` event per failed attempt |

LLM attribute names follow the OpenTelemetry GenAI semantic conventions where they exist.

### What is tracked, mapped to the requirements

| Requirement | Where |
|-------------|-------|
| Investigation id, user query | root span attributes |
| Agent node execution, node duration | `node.*` spans |
| LLM model, token usage, LLM latency, estimated cost | `llm.generate` spans |
| Tool calls, tool latency, tool errors | `tool.*` spans |
| Retry counts | `ai_sre.tool.retries`, `ai_sre.node.attempts`, `ai_sre.llm.attempts` |
| Investigation iterations | `ai_sre.round` on tool spans (max = iterations) |
| Evidence retrieved | successful tool spans, each with its `ai_sre.evidence.id` |
| Final result | `ai_sre.result.*` on the root span |

`summarize(spans)` aggregates a trace into the numbers the UI and the evaluation use:
duration, iterations, per-node executions/time/retries/errors, per-tool calls/errors/retries/
time, LLM calls/tokens/latency/cost/models/retries, and the final result.

## Privacy

Spans never contain tool results, prompts or model output. User text and tool arguments pass
through `redact_text` / `sanitize_args`:

- keys that look like credentials (`token`, `secret`, `password`, `authorization`, `api_key`,
  `cookie`) are replaced with `[REDACTED]`;
- JWTs, Google/OpenAI-style API keys, bearer tokens, `key=value` secrets, e-mail addresses and
  card numbers are masked in free text;
- strings are truncated (200–300 chars), lists to 10 items, nesting to depth 3.

The tests in `backend/tests/agent/test_tracing.py` assert this, including that the prompt text
of an LLM call does not appear in its span.

## Where traces go

- **In-process collector** (always on): a `SpanProcessor` keeps finished spans per
  investigation. The API persists them to `investigation_spans` (migration 0004) when each run
  segment ends, and serves `GET /api/v1/investigations/{id}/trace` (persisted + in-flight spans,
  with the summary). The evaluation harness reads them directly.
- **Export** (optional): `AI_SRE_OTEL_EXPORTER=console` prints spans; `AI_SRE_OTEL_EXPORTER=otlp`
  sends them to any OTLP collector (Jaeger, Tempo, Honeycomb…) using the standard
  `OTEL_EXPORTER_OTLP_ENDPOINT`. It needs `opentelemetry-exporter-otlp-proto-http`, which is not
  a default dependency.

## Cost estimate

`estimate_cost_usd(model, input_tokens, output_tokens)` uses per-model-family list prices in
`PRICES_PER_MTOK` (USD per million tokens). These are estimates: the Gemini free tier is billed
at $0, and prices change. Override with `AI_SRE_LLM_PRICE_INPUT_PER_MTOK` and
`AI_SRE_LLM_PRICE_OUTPUT_PER_MTOK`.

## Trace view

Open an investigation and click **Trace** (`/investigations/:id/trace`):

- tiles for agent time, iterations, tool calls and average latency, tool errors and retries,
  evidence retrieved, LLM calls and models, tokens in/out, estimated cost;
- the final result;
- a waterfall of spans per run segment (indentation = parent/child, colour = kind, red =
  error); click a span to see its attributes;
- per-node and per-tool tables (executions, retries, errors, total time).

The view polls while the investigation is running and stops when it is finished.
