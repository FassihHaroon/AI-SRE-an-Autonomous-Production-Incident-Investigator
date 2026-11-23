"""Smoke-test the configured LLM provider with one short request.

Usage: ``uv run poe llm-smoke`` (or ``python -m ai_sre.integrations.llm "your prompt"``).
Uses one request of your free-tier quota.
"""

from __future__ import annotations

import asyncio
import sys

from pydantic import BaseModel

from ai_sre.config import get_settings
from ai_sre.integrations.llm import (
    LLMError,
    LLMMessage,
    LLMRequest,
    create_llm_provider,
    generate_structured,
)


class _Answer(BaseModel):
    answer: str
    confidence: float


async def _main(prompt: str) -> int:
    settings = get_settings()
    try:
        provider = create_llm_provider(settings)
        parsed, response = await generate_structured(
            provider,
            LLMRequest(messages=[LLMMessage(role="user", content=prompt)], max_output_tokens=256),
            _Answer,
        )
    except LLMError as exc:
        print(f"LLM call failed: {exc}", file=sys.stderr)
        return 1
    print(f"provider={provider.name} model={response.model} finish={response.finish_reason}")
    print(f"tokens in={response.usage.input_tokens} out={response.usage.output_tokens}")
    print(parsed.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "In one sentence: what does an SRE do?"
    sys.exit(asyncio.run(_main(question)))
