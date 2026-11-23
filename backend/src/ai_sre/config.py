"""Application configuration.

All settings come from environment variables prefixed ``AI_SRE_`` (or a ``.env`` file at the
repository root for local runs). Secrets are ``SecretStr`` so they never appear in logs or
reprs. See ``.env.example`` for the full list.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

Environment = Literal["development", "test", "production"]
LogFormat = Literal["console", "json"]
LLMProviderName = Literal["gemini", "scripted"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_SRE_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- runtime
    environment: Environment = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: LogFormat | None = Field(
        default=None, description="Defaults to 'console' in development, 'json' otherwise."
    )

    # --- data stores
    database_url: str = "postgresql+asyncpg://aisre:aisre@127.0.0.1:5432/aisre"
    redis_url: str = "redis://127.0.0.1:6379/0"
    dependency_check_timeout_seconds: float = Field(default=2.0, gt=0, le=30)

    # --- LLM (agent logic arrives in a later phase; the provider layer exists now)
    llm_provider: LLMProviderName = "gemini"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    llm_request_timeout_seconds: float = Field(default=60.0, gt=0)

    @model_validator(mode="after")
    def _check_production_safety(self) -> Settings:
        if self.environment == "production":
            if "aisre:aisre@" in self.database_url:
                raise ValueError("Default database credentials are not allowed in production.")
            if self.llm_provider == "scripted":
                raise ValueError("The 'scripted' LLM provider is for tests only.")
        return self

    @property
    def effective_log_format(self) -> LogFormat:
        if self.log_format is not None:
            return self.log_format
        return "console" if self.environment == "development" else "json"

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider == "gemini":
            return self.gemini_api_key is not None and bool(
                self.gemini_api_key.get_secret_value().strip()
            )
        return True


@lru_cache
def get_settings() -> Settings:
    return Settings()
