"""FastAPI application factory.

Run locally with ``uv run poe api``, or in Docker via ``uvicorn ai_sre.main:app``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ai_sre import __version__
from ai_sre.api import router
from ai_sre.api.errors import register_error_handlers
from ai_sre.config import Settings, get_settings
from ai_sre.db.postgres import create_engine
from ai_sre.db.redis import create_redis
from ai_sre.observability.logging import configure_logging, get_logger
from ai_sre.observability.middleware import RequestContextMiddleware

log = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.effective_log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Clients connect lazily, so the API starts even when a dependency is down;
        # /readyz reports it instead of the process crash-looping.
        app.state.settings = settings
        app.state.db_engine = create_engine(settings.database_url)
        app.state.redis = create_redis(
            settings.redis_url, settings.dependency_check_timeout_seconds
        )
        log.info(
            "app.startup",
            version=__version__,
            environment=settings.environment,
            llm_provider=settings.llm_provider,
            llm_configured=settings.llm_configured,
        )
        try:
            yield
        finally:
            await app.state.redis.aclose()
            await app.state.db_engine.dispose()
            log.info("app.shutdown")

    is_prod = settings.environment == "production"
    app = FastAPI(
        title="AI SRE API",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else "/openapi.json",
    )
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(router.root)
    app.include_router(router.api_v1)
    return app


app = create_app()
