"""Integration tests against real Postgres and Redis.

Run with: ``docker compose up -d postgres redis``, ``uv run poe migrate``, then
``uv run poe test-integration``. Connection settings come from ``.env`` / ``AI_SRE_*``.
"""

import pytest
from sqlalchemy import text

from ai_sre.config import Settings
from ai_sre.db.postgres import create_engine
from ai_sre.db.redis import create_redis
from ai_sre.services.health import check_readiness, postgres_check, redis_check

pytestmark = pytest.mark.integration


async def test_real_dependencies_are_ready() -> None:
    settings = Settings()
    engine = create_engine(settings.database_url)
    redis = create_redis(settings.redis_url)
    try:
        report = await check_readiness(
            {"postgres": postgres_check(engine), "redis": redis_check(redis)}, 5.0
        )
        assert report.status == "ready", report
    finally:
        await redis.aclose()
        await engine.dispose()


async def test_migrations_enabled_pgvector() -> None:
    engine = create_engine(Settings().database_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT extname FROM pg_extension"))
            assert "vector" in set(result.scalars())
    finally:
        await engine.dispose()
