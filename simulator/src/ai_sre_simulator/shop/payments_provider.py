"""payments-provider: a stand-in for a third-party payment service provider (PSP).

It is *external*: its own logs and metrics are not collected, as you would not have them for a
real PSP. Its health is only visible through the shop's client-side metrics and error logs.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ai_sre_simulator.common.determinism import sampler
from ai_sre_simulator.common.faults import FaultType
from ai_sre_simulator.common.runtime import Runtime
from ai_sre_simulator.common.settings import Settings

_REASONS = {502: "bad_gateway", 503: "service_unavailable", 504: "gateway_timeout"}


class ChargeIn(BaseModel):
    amount_cents: int = Field(ge=1)
    currency: str = Field(min_length=3, max_length=3)
    reference: str


def create_app(settings: Settings) -> FastAPI:
    runtime = Runtime(settings, track_deployments=False)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await runtime.start()
        yield
        await runtime.stop()

    app = FastAPI(title="payments-provider (simulated third party)", lifespan=lifespan)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/charges")
    async def charge(body: ChargeIn) -> Response:
        # Normal processing time 40-120 ms, deterministic per call.
        delay_ms = 80 + sampler.jitter(settings.seed, "charge-latency", 40)
        status = 200
        for fault in runtime.state.faults_of(FaultType.DEPENDENCY_ERRORS):
            p: dict[str, Any] = fault.params
            delay_ms += p["extra_latency_ms"]
            if sampler.chance(fault.seed, fault.id, p["error_rate"]):
                status = p["status"]
        await asyncio.sleep(delay_ms / 1000)
        if status != 200:
            code = _REASONS.get(status, "error")
            return JSONResponse({"error": code, "message": "upstream acquirer unavailable"}, status)
        return JSONResponse(
            {
                "charge_id": f"ch_{secrets.token_hex(8)}",
                "status": "succeeded",
                "reference": body.reference,
            }
        )

    return app
