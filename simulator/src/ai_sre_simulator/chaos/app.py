"""chaos HTTP API: the simulation control plane (fault injection, scenarios, reset).

Not part of the observable "production" surface: no metrics endpoint is scraped and its logs
are not shipped. ``simctl`` is a thin client for this API.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ai_sre_simulator.chaos.engine import ChaosEngine, EngineError, ScenarioRun
from ai_sre_simulator.chaos.scenarios import DEFAULT_SCENARIO_DIR, Scenario, load_scenarios
from ai_sre_simulator.common.faults import FAULT_SPECS, ActiveFault, FaultRequest, RuntimeFaults
from ai_sre_simulator.common.settings import Settings

logger = logging.getLogger("chaos.api")


class TriggerRequest(BaseModel):
    speed: float = Field(default=1.0, gt=0, le=100)


class ResetRequest(BaseModel):
    data: bool = Field(default=False, description="Also delete orders created since the seed.")


class ScenarioSummary(BaseModel):
    id: str
    title: str
    category: str
    summary: str
    steps: int
    duration_s: float


def create_app(settings: Settings) -> FastAPI:
    scenario_dir = Path(os.environ.get("SIM_SCENARIO_DIR", str(DEFAULT_SCENARIO_DIR)))
    engine = ChaosEngine(settings, load_scenarios(scenario_dir))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await engine.start()
        yield
        await engine.stop()

    app = FastAPI(
        title="chaos — SIMULATION control plane",
        description="Fault injection and scenarios for the simulated production environment.",
        lifespan=lifespan,
    )

    @app.exception_handler(EngineError)
    async def engine_error(_request: Request, exc: Exception) -> JSONResponse:
        status = exc.status_code if isinstance(exc, EngineError) else 500
        return JSONResponse({"error": str(exc)}, status_code=status)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # ---------------------------------------------------------------- service polling
    @app.get("/internal/faults/{service}", response_model=RuntimeFaults)
    async def runtime_faults(service: str) -> RuntimeFaults:
        return engine.runtime_faults(service)

    # ---------------------------------------------------------------- scenarios
    @app.get("/api/v1/scenarios", response_model=list[ScenarioSummary])
    async def list_scenarios() -> list[ScenarioSummary]:
        return [
            ScenarioSummary(
                id=s.id,
                title=s.title,
                category=s.category,
                summary=s.summary,
                steps=len(s.steps),
                duration_s=s.duration_s,
            )
            for s in engine.scenarios.values()
        ]

    @app.get("/api/v1/scenarios/{scenario_id}", response_model=Scenario)
    async def get_scenario(scenario_id: str) -> Scenario:
        scenario = engine.scenarios.get(scenario_id)
        if scenario is None:
            raise EngineError(
                f"unknown scenario {scenario_id!r}; valid: {', '.join(sorted(engine.scenarios))}",
                404,
            )
        return scenario

    @app.post(
        "/api/v1/scenarios/{scenario_id}/trigger", status_code=202, response_model=ScenarioRun
    )
    async def trigger(scenario_id: str, body: TriggerRequest | None = None) -> ScenarioRun:
        return await engine.trigger(scenario_id, (body or TriggerRequest()).speed)

    @app.post("/api/v1/scenarios/{scenario_id}/clear", response_model=list[ScenarioRun])
    async def clear_scenario(scenario_id: str) -> list[ScenarioRun]:
        runs = [
            r for r in engine.runs.values() if r.scenario_id == scenario_id and r.cleared_at is None
        ]
        if not runs:
            raise EngineError(f"scenario {scenario_id!r} has no active run", 404)
        return [await engine.clear_run(r.id) for r in runs]

    @app.get("/api/v1/runs", response_model=list[ScenarioRun])
    async def list_runs() -> list[ScenarioRun]:
        return sorted(engine.runs.values(), key=lambda r: r.id)

    @app.get("/api/v1/runs/{run_id}", response_model=ScenarioRun)
    async def get_run(run_id: str) -> ScenarioRun:
        run = engine.runs.get(run_id)
        if run is None:
            raise EngineError(f"unknown run {run_id}", 404)
        return run

    @app.post("/api/v1/runs/{run_id}/clear", response_model=ScenarioRun)
    async def clear_run(run_id: str) -> ScenarioRun:
        return await engine.clear_run(run_id)

    # ---------------------------------------------------------------- faults
    @app.get("/api/v1/fault-types")
    async def fault_types() -> list[dict[str, Any]]:
        return [
            {
                "type": t.value,
                "mechanism": spec.mechanism,
                "targets": list(spec.targets),
                "description": spec.description,
                "params": spec.params.model_json_schema(),
            }
            for t, spec in FAULT_SPECS.items()
        ]

    @app.get("/api/v1/faults", response_model=list[ActiveFault])
    async def list_faults() -> list[ActiveFault]:
        return list(engine.faults.values())

    @app.post("/api/v1/faults", status_code=201, response_model=ActiveFault)
    async def inject(req: FaultRequest) -> ActiveFault:
        return await engine.inject(req)

    @app.delete("/api/v1/faults/{fault_id}", response_model=ActiveFault)
    async def remove(fault_id: str) -> ActiveFault:
        return await engine.remove(fault_id)

    # ---------------------------------------------------------------- whole environment
    @app.get("/api/v1/status")
    async def status() -> dict[str, Any]:
        return {
            "environment": settings.environment,
            "simulated": True,
            "epoch": engine.epoch,
            "active_runs": [
                r.model_dump(mode="json") for r in engine.runs.values() if r.cleared_at is None
            ],
            "active_faults": [f.model_dump(mode="json") for f in engine.faults.values()],
        }

    @app.post("/api/v1/clear")
    async def clear_all() -> dict[str, Any]:
        return await engine.clear_all()

    @app.post("/api/v1/reset")
    async def reset(body: ResetRequest | None = None) -> dict[str, Any]:
        return await engine.reset(data=(body or ResetRequest()).data)

    return app
