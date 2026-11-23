"""Provider-neutral LLM interface.

Agent nodes (built in a later phase) depend only on :class:`LLMProvider`. Concrete providers
translate :class:`LLMRequest` into their SDK's request format and back. The interface is
deliberately small: one ``generate`` call that optionally returns schema-validated JSON.
Tool calling will be added when the step executor needs it, not before.
"""

from __future__ import annotations

import json
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class LLMMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class LLMRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    messages: list[LLMMessage] = Field(min_length=1)
    system: str | None = None
    response_schema: type[BaseModel] | None = Field(
        default=None, description="If set, the provider must return JSON matching this model."
    )
    max_output_tokens: int = Field(default=4096, gt=0)
    node: str = Field(default="unspecified", description="Calling graph node; used for metrics.")


class LLMUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class LLMResponse(BaseModel):
    text: str
    model: str
    usage: LLMUsage = LLMUsage()
    finish_reason: str | None = None


class LLMError(Exception):
    """A provider call failed. ``retryable`` marks rate limits and server-side errors."""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class LLMConfigurationError(LLMError):
    """The provider cannot be constructed from the current settings."""


class StructuredOutputError(LLMError):
    """The model's output did not validate against the requested schema."""


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    async def generate(self, request: LLMRequest) -> LLMResponse: ...


async def generate_structured[T: BaseModel](
    provider: LLMProvider, request: LLMRequest, schema: type[T]
) -> tuple[T, LLMResponse]:
    """Call the provider with ``schema`` as the response format and validate the result."""
    response = await provider.generate(request.model_copy(update={"response_schema": schema}))
    try:
        parsed = schema.model_validate(json.loads(response.text))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise StructuredOutputError(
            f"{provider.name} output did not match {schema.__name__}: {exc}"
        ) from exc
    return parsed, response
