"""LLM provider wrapper: one OpenTelemetry span per request, with retries of transient errors.

Recorded: provider, requested/served model, input/output tokens, latency, estimated cost, the
graph node that asked, prompt size in characters, attempts and the error class. Never recorded:
prompt text or model output.

Retryable errors (rate limits, 5xx "high demand") are retried with exponential backoff. Each
failed attempt is a span event, so the trace shows how often the provider was unavailable.
"""

from __future__ import annotations

import asyncio

from opentelemetry.trace import Status, StatusCode

from ai_sre.integrations.llm.base import LLMError, LLMProvider, LLMRequest, LLMResponse
from ai_sre.observability.tracing import estimate_cost_usd, llm_span


class TracedLLMProvider:
    def __init__(self, inner: LLMProvider, *, retries: int = 2, backoff_s: float = 2.0) -> None:
        self.inner = inner
        self.name = inner.name
        self.model = inner.model
        self.retries = retries
        self.backoff_s = backoff_s

    async def generate(self, request: LLMRequest) -> LLMResponse:
        with llm_span(request.node or "unknown", self.inner.name, self.inner.model) as span:
            span.set_attribute(
                "ai_sre.llm.prompt_chars", sum(len(m.content) for m in request.messages)
            )
            span.set_attribute("gen_ai.request.max_tokens", request.max_output_tokens)
            attempt = 0
            while True:
                attempt += 1
                span.set_attribute("ai_sre.llm.attempts", attempt)
                try:
                    response = await self.inner.generate(request)
                    break
                except LLMError as exc:
                    span.add_event(
                        "llm.attempt_failed",
                        {
                            "attempt": attempt,
                            "error.type": type(exc).__name__,
                            "status": exc.status or 0,
                        },
                    )
                    if not exc.retryable or attempt > self.retries:
                        span.set_attribute("error.type", type(exc).__name__)
                        span.set_attribute("ai_sre.llm.retryable", exc.retryable)
                        span.set_status(
                            Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"[:200])
                        )
                        raise
                    await asyncio.sleep(self.backoff_s * 2 ** (attempt - 1))
            usage = response.usage
            span.set_attribute("gen_ai.response.model", response.model or self.inner.model)
            span.set_attribute("gen_ai.usage.input_tokens", usage.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", usage.output_tokens)
            span.set_attribute(
                "ai_sre.llm.cost_usd",
                estimate_cost_usd(
                    response.model or self.inner.model, usage.input_tokens, usage.output_tokens
                ),
            )
            span.set_attribute("ai_sre.llm.output_chars", len(response.text))
            return response
