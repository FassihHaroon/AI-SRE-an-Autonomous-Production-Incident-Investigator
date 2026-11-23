from types import SimpleNamespace
from typing import Any

import pytest
from google.genai import errors, types
from pydantic import BaseModel, SecretStr

from ai_sre.config import Settings
from ai_sre.integrations.llm import (
    LLMConfigurationError,
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMRequest,
    StructuredOutputError,
    create_llm_provider,
    generate_structured,
)
from ai_sre.integrations.llm.gemini import GeminiProvider
from ai_sre.integrations.llm.scripted import ScriptedProvider


class Verdict(BaseModel):
    category: str
    confidence: float


def _request(text: str = "hi") -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role="user", content=text)])


# ---------------------------------------------------------------- scripted provider


async def test_scripted_provider_returns_responses_in_order() -> None:
    provider = ScriptedProvider(["first", Verdict(category="bad_deploy", confidence=0.8)])
    assert (await provider.generate(_request())).text == "first"
    second = await provider.generate(_request())
    assert Verdict.model_validate_json(second.text).category == "bad_deploy"
    assert len(provider.requests) == 2


async def test_scripted_provider_raises_when_exhausted() -> None:
    with pytest.raises(LLMError, match="no responses left"):
        await ScriptedProvider().generate(_request())


async def test_generate_structured_parses_and_passes_schema() -> None:
    provider = ScriptedProvider(['{"category": "config_change", "confidence": 0.6}'])
    parsed, _ = await generate_structured(provider, _request(), Verdict)
    assert parsed == Verdict(category="config_change", confidence=0.6)
    assert provider.requests[0].response_schema is Verdict


@pytest.mark.parametrize("bad", ["not json", '{"category": "x"}'])
async def test_generate_structured_rejects_invalid_output(bad: str) -> None:
    with pytest.raises(StructuredOutputError):
        await generate_structured(ScriptedProvider([bad]), _request(), Verdict)


# ---------------------------------------------------------------- factory


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_factory_requires_gemini_key() -> None:
    with pytest.raises(LLMConfigurationError, match="AI_SRE_GEMINI_API_KEY"):
        create_llm_provider(_settings(llm_provider="gemini", gemini_api_key=None))


def test_factory_builds_gemini_without_network_calls() -> None:
    provider = create_llm_provider(
        _settings(llm_provider="gemini", gemini_api_key=SecretStr("k"), gemini_model="m-1")
    )
    assert isinstance(provider, GeminiProvider)
    assert isinstance(provider, LLMProvider)
    assert provider.model == "m-1"


def test_factory_builds_scripted() -> None:
    assert isinstance(create_llm_provider(_settings(llm_provider="scripted")), ScriptedProvider)


# ---------------------------------------------------------------- gemini adapter (fake SDK client)


class _FakeModels:
    def __init__(self, result: Any = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.result


def _fake_client(models: _FakeModels) -> Any:
    return SimpleNamespace(aio=SimpleNamespace(models=models))


def _fake_response(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part.from_text(text=text)]),
                finish_reason=types.FinishReason.STOP,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=12, candidates_token_count=5
        ),
        model_version="gemini-test-001",
    )


async def test_gemini_translates_request_and_response() -> None:
    models = _FakeModels(result=_fake_response('{"category": "bad_deploy", "confidence": 0.9}'))
    provider = GeminiProvider(api_key="k", model="gemini-x", client=_fake_client(models))
    request = LLMRequest(
        system="You are an SRE.",
        messages=[
            LLMMessage(role="user", content="q1"),
            LLMMessage(role="assistant", content="a1"),
            LLMMessage(role="user", content="q2"),
        ],
        max_output_tokens=100,
    )

    parsed, response = await generate_structured(provider, request, Verdict)

    call = models.calls[0]
    assert call["model"] == "gemini-x"
    assert [c.role for c in call["contents"]] == ["user", "model", "user"]
    config = call["config"]
    assert config.system_instruction == "You are an SRE."
    assert config.max_output_tokens == 100
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema["title"] == "Verdict"
    assert parsed.category == "bad_deploy"
    assert response.model == "gemini-test-001"
    assert response.finish_reason == "STOP"
    assert (response.usage.input_tokens, response.usage.output_tokens) == (12, 5)


async def test_gemini_plain_text_request_has_no_json_config() -> None:
    models = _FakeModels(result=_fake_response("hello"))
    provider = GeminiProvider(api_key="k", model="gemini-x", client=_fake_client(models))
    response = await provider.generate(_request())
    assert response.text == "hello"
    assert models.calls[0]["config"].response_mime_type is None


@pytest.mark.parametrize(("code", "retryable"), [(429, True), (503, True), (400, False)])
async def test_gemini_maps_api_errors(code: int, retryable: bool) -> None:
    error = errors.APIError(code, {"error": {"message": "nope", "status": "X"}})
    provider = GeminiProvider(
        api_key="k", model="gemini-x", client=_fake_client(_FakeModels(error=error))
    )
    with pytest.raises(LLMError) as excinfo:
        await provider.generate(_request())
    assert excinfo.value.retryable is retryable
    assert excinfo.value.status == code
