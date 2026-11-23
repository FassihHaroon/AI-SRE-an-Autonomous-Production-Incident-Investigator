"""Deterministic provider for tests: returns pre-written responses in order."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from pydantic import BaseModel

from ai_sre.integrations.llm.base import LLMError, LLMRequest, LLMResponse, LLMUsage


class ScriptedProvider:
    name = "scripted"

    def __init__(self, responses: Iterable[str | BaseModel] = (), model: str = "scripted") -> None:
        self.model = model
        self._responses: deque[str | BaseModel] = deque(responses)
        self.requests: list[LLMRequest] = []

    def add(self, response: str | BaseModel) -> None:
        self._responses.append(response)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._responses:
            raise LLMError("ScriptedProvider has no responses left")
        item = self._responses.popleft()
        text = item.model_dump_json() if isinstance(item, BaseModel) else item
        return LLMResponse(
            text=text,
            model=self.model,
            finish_reason="STOP",
            usage=LLMUsage(input_tokens=0, output_tokens=0),
        )
