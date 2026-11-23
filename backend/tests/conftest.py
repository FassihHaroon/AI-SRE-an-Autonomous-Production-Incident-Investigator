from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_sre.config import Settings
from ai_sre.main import create_app


@pytest.fixture
def settings() -> Settings:
    """Settings isolated from the developer's .env and environment variables."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment="test",
        log_format="json",
        # Unreachable on purpose: tests that need dependencies override the health checks.
        database_url="postgresql+asyncpg://test:test@127.0.0.1:1/test",
        redis_url="redis://127.0.0.1:1/0",
        dependency_check_timeout_seconds=0.5,
        llm_provider="gemini",
        gemini_api_key=None,
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:  # runs the lifespan (startup/shutdown)
        yield test_client
