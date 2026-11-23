"""Top-level API routing. Versioned endpoints live under ``/api/v1``."""

from fastapi import APIRouter

from ai_sre.api.routes import evaluations, health, investigations, knowledge_settings, platform

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(health.system)
api_v1.include_router(platform.router)
api_v1.include_router(investigations.router)
api_v1.include_router(knowledge_settings.knowledge)
api_v1.include_router(knowledge_settings.settings_router)
api_v1.include_router(evaluations.router)

root = APIRouter()
root.include_router(health.probes)
