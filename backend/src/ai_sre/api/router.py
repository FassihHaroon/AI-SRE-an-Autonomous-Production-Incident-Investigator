"""Top-level API routing. Versioned endpoints live under ``/api/v1``."""

from fastapi import APIRouter

from ai_sre.api.routes import health

api_v1 = APIRouter(prefix="/api/v1")
api_v1.include_router(health.system)

root = APIRouter()
root.include_router(health.probes)
