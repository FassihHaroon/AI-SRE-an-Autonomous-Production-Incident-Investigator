import pytest
from pydantic import SecretStr, ValidationError

from ai_sre.config import Settings


def make(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_defaults_are_development_friendly() -> None:
    settings = make()
    assert settings.environment == "development"
    assert settings.effective_log_format == "console"


def test_non_development_defaults_to_json_logs() -> None:
    assert make(environment="test").effective_log_format == "json"


def test_reads_prefixed_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SRE_REDIS_URL", "redis://cache:6379/2")
    monkeypatch.setenv("AI_SRE_GEMINI_API_KEY", "k-123")
    settings = make()
    assert settings.redis_url == "redis://cache:6379/2"
    assert settings.llm_configured is True


def test_secret_is_not_exposed_in_repr() -> None:
    settings = make(gemini_api_key=SecretStr("super-secret-key"))
    assert "super-secret-key" not in repr(settings)


@pytest.mark.parametrize("key", [None, SecretStr(""), SecretStr("   ")])
def test_gemini_not_configured_without_key(key: SecretStr | None) -> None:
    assert make(gemini_api_key=key).llm_configured is False


def test_production_rejects_default_database_credentials() -> None:
    with pytest.raises(ValidationError, match="Default database credentials"):
        make(environment="production")


def test_production_rejects_scripted_llm() -> None:
    with pytest.raises(ValidationError, match="tests only"):
        make(
            environment="production",
            database_url="postgresql+asyncpg://app:strong@db:5432/aisre",
            llm_provider="scripted",
        )
