"""Google Gemini provider (official ``google-genai`` SDK).

Note: the Gemini free tier has low per-model request limits and Google may use free-tier
prompts to improve its products. Only send synthetic data (the simulator's) through it.
"""

from __future__ import annotations

from google import genai
from google.genai import errors, types

from ai_sre.integrations.llm.base import LLMError, LLMRequest, LLMResponse, LLMUsage

_RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        client: genai.Client | None = None,
    ) -> None:
        self.model = model
        self._client = client or genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),
        )

    async def generate(self, request: LLMRequest) -> LLMResponse:
        contents = [
            types.Content(
                role="user" if message.role == "user" else "model",
                parts=[types.Part.from_text(text=message.content)],
            )
            for message in request.messages
        ]
        config = types.GenerateContentConfig(
            system_instruction=request.system,
            max_output_tokens=request.max_output_tokens,
        )
        if request.response_schema is not None:
            config.response_mime_type = "application/json"
            config.response_json_schema = request.response_schema.model_json_schema()

        try:
            response = await self._client.aio.models.generate_content(
                model=self.model, contents=contents, config=config
            )
        except errors.APIError as exc:
            raise LLMError(
                f"Gemini API error {exc.code}: {exc.message}",
                retryable=exc.code in _RETRYABLE_STATUSES,
                status=exc.code,
            ) from exc

        finish_reason = None
        if response.candidates and response.candidates[0].finish_reason is not None:
            finish_reason = str(response.candidates[0].finish_reason.value)
        usage = response.usage_metadata
        return LLMResponse(
            text=response.text or "",
            model=response.model_version or self.model,
            finish_reason=finish_reason,
            usage=LLMUsage(
                input_tokens=(usage.prompt_token_count or 0) if usage else 0,
                output_tokens=(usage.candidates_token_count or 0) if usage else 0,
            ),
        )
