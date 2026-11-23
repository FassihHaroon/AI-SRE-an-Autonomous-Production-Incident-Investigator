"""User-editable settings (agent limits, reasoner) persisted in ``app_settings``."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ai_sre.agent.models import Limits
from ai_sre.services.repository import Repository


class AgentSettings(BaseModel):
    reasoner: Literal["rules", "llm"] = "rules"
    max_rounds: int = Field(default=3, ge=1, le=6)
    max_tool_calls: int = Field(default=24, ge=3, le=100)
    time_budget_s: float = Field(default=300, gt=0, le=3600)
    sufficient_confidence: float = Field(default=0.75, ge=0.5, le=0.95)
    verification_wait_s: float = Field(default=45, ge=0, le=600)

    def limits(self) -> Limits:
        return Limits(**self.model_dump(exclude={"reasoner"}))


class SettingsStore:
    KEY = "agent"

    def __init__(self, repo: Repository) -> None:
        self.repo = repo
        self.current = AgentSettings()

    async def load(self) -> AgentSettings:
        stored = await self.repo.get_setting(self.KEY)
        self.current = AgentSettings.model_validate(stored) if stored else AgentSettings()
        return self.current

    async def save(self, settings: AgentSettings) -> AgentSettings:
        await self.repo.put_setting(self.KEY, settings.model_dump())
        self.current = settings
        return settings
