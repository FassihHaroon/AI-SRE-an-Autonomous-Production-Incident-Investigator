"""Build the configured LLM provider."""

from __future__ import annotations

from ai_sre.config import Settings
from ai_sre.integrations.llm.base import LLMConfigurationError, LLMProvider
from ai_sre.integrations.llm.gemini import GeminiProvider
from ai_sre.integrations.llm.scripted import ScriptedProvider
from ai_sre.integrations.llm.traced import TracedLLMProvider


def create_llm_provider(settings: Settings) -> LLMProvider:
    """The configured provider, wrapped so every request is traced (tokens, latency, cost)."""
    return TracedLLMProvider(_create(settings))


def _create(settings: Settings) -> LLMProvider:
    match settings.llm_provider:
        case "gemini":
            if not settings.llm_configured or settings.gemini_api_key is None:
                raise LLMConfigurationError(
                    "AI_SRE_GEMINI_API_KEY is not set. Create a free key at "
                    "https://aistudio.google.com/apikey and put it in .env."
                )
            return GeminiProvider(
                api_key=settings.gemini_api_key.get_secret_value(),
                model=settings.gemini_model,
                timeout_seconds=settings.llm_request_timeout_seconds,
            )
        case "scripted":
            return ScriptedProvider()
