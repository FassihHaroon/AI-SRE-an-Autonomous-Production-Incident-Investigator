"""MCP server configuration (environment variables prefixed ``AI_SRE_MCP_``)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ai_sre.config import REPO_ROOT

_DEV_DEFAULTS = {
    "read_token": "dev-mcp-read-token",
    "remediation_token": "dev-mcp-remediation-token",
    "admin_token": "dev-mcp-admin-token",
    "approval_secret": "dev-mcp-approval-secret-change-me",
}


class McpSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AI_SRE_MCP_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="development", validation_alias="AI_SRE_ENVIRONMENT")
    host: str = "127.0.0.1"
    port: int = 8300

    # --- the monitored (simulated) environment
    prometheus_url: str = "http://127.0.0.1:9090"
    loki_url: str = "http://127.0.0.1:3100"
    deployer_url: str = "http://127.0.0.1:8202"
    orchestrator_url: str = "http://127.0.0.1:8210"  # chaos controller's /api/v1/actions
    database_dsn: SecretStr = SecretStr(
        "postgresql://sre_readonly:sre-readonly-sim@127.0.0.1:55432/shop"
    )
    incidents_dir: Path = REPO_ROOT / "knowledge" / "incidents"
    # Knowledge base (platform database). Default: AI_SRE_DATABASE_URL.
    knowledge_dsn: SecretStr | None = None

    # --- auth (bearer tokens). Read tools: read or remediation token. Remediation tools:
    # remediation token only. Approvals: admin token (held by humans, never by the agent).
    read_token: SecretStr = SecretStr(_DEV_DEFAULTS["read_token"])
    remediation_token: SecretStr = SecretStr(_DEV_DEFAULTS["remediation_token"])
    admin_token: SecretStr = SecretStr(_DEV_DEFAULTS["admin_token"])
    approval_secret: SecretStr = SecretStr(_DEV_DEFAULTS["approval_secret"])

    # --- remediation
    remediation_enabled: bool = False
    approval_ttl_s: int = Field(default=900, ge=30, le=86_400)

    # --- limits
    tool_timeout_s: float = Field(default=20.0, gt=0, le=120)
    backend_timeout_s: float = Field(default=10.0, gt=0, le=60)
    max_result_bytes: int = Field(default=16_000, ge=2_000, le=200_000)

    @model_validator(mode="after")
    def _no_default_secrets_in_production(self) -> McpSettings:
        if self.environment == "production":
            for name, default in _DEV_DEFAULTS.items():
                if getattr(self, name).get_secret_value() == default:
                    raise ValueError(f"AI_SRE_MCP_{name.upper()} must be set in production")
        return self


@lru_cache
def get_mcp_settings() -> McpSettings:
    return McpSettings()
