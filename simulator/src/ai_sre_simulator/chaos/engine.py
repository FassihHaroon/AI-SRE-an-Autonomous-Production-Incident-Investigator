"""Chaos engine: applies and reverses faults, runs scenario timelines, resets the environment.

State (active faults, scenario runs, the reset epoch) is persisted as JSON documents in the
``simcontrol`` database, so a controller restart neither loses nor duplicates faults.

Reversibility:

* in-app / traffic faults: removed from the active set; services drop the effect on their next
  poll (leaked connections returned, ballast memory freed).
* network faults: the Toxiproxy toxic is deleted or the proxy re-enabled.
* deployment steps and ``failed_deployment``: a ``rollback`` deployment restores the version
  and flags the service ran before the scenario. History is kept, as in a real CD tool.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from typing import Any, Literal

import asyncpg
import httpx
import redis.asyncio as aioredis
from pydantic import BaseModel, Field

from ai_sre_simulator.chaos.scenarios import DeployStep, Scenario
from ai_sre_simulator.chaos.toxiproxy import PROXY_FOR_TARGET, Toxiproxy
from ai_sre_simulator.common.faults import (
    FAULT_SPECS,
    ActiveFault,
    FaultRequest,
    FaultType,
    RuntimeFault,
    RuntimeFaults,
)
from ai_sre_simulator.common.logging import error_fields, log
from ai_sre_simulator.common.settings import Settings
from ai_sre_simulator.shop import FULFILMENT_DLQ, FULFILMENT_QUEUE
from ai_sre_simulator.shop.seed import RESET_LIVE_DATA

logger = logging.getLogger("chaos")

SCHEMA = """
CREATE TABLE IF NOT EXISTS chaos_faults (id text PRIMARY KEY, doc jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS chaos_runs (id text PRIMARY KEY, doc jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS chaos_state (key text PRIMARY KEY, value jsonb NOT NULL);
"""

StepStatus = Literal["pending", "applied", "failed", "skipped", "reverted"]
RunStatus = Literal["running", "active", "cleared", "failed", "interrupted"]


class StepState(BaseModel):
    index: int
    at_s: float
    description: str
    status: StepStatus = "pending"
    applied_at: datetime | None = None
    fault_id: str | None = None
    deployment_id: str | None = None
    error: str | None = None


class ScenarioRun(BaseModel):
    id: str
    scenario_id: str
    speed: float
    status: RunStatus = "running"
    started_at: datetime
    cleared_at: datetime | None = None
    steps: list[StepState]
    # Runtime (version + flags) of each service before the run's first deploy touched it.
    restore: dict[str, dict[str, Any]] = Field(default_factory=dict)


class EngineError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _now() -> datetime:
    return datetime.now(UTC)


class ChaosEngine:
    def __init__(self, settings: Settings, scenarios: dict[str, Scenario]) -> None:
        self.settings = settings
        self.scenarios = scenarios
        self.faults: dict[str, ActiveFault] = {}
        self.runs: dict[str, ScenarioRun] = {}
        self.epoch = 1
        self.restarts: dict[str, int] = {}
        self.replicas: dict[str, int] = {}
        self._counters = {"fault": 0, "run": 0}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._db: asyncpg.Pool | None = None
        self.toxiproxy = Toxiproxy(settings.toxiproxy_url)
        self.deployer = httpx.AsyncClient(base_url=settings.deployer_url, timeout=10.0)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        for attempt in range(60):
            try:
                self._db = await asyncpg.create_pool(
                    self.settings.control_database_url, min_size=1, max_size=4
                )
                break
            except (OSError, asyncpg.PostgresError):
                if attempt == 59:
                    raise
                await asyncio.sleep(1)
        async with self._pool().acquire() as conn:
            await conn.execute("SELECT pg_advisory_lock(4343)")
            try:
                await conn.execute(SCHEMA)
            finally:
                await conn.execute("SELECT pg_advisory_unlock(4343)")
            for row in await conn.fetch("SELECT doc FROM chaos_faults"):
                fault = ActiveFault.model_validate_json(row["doc"])
                self.faults[fault.id] = fault
            for row in await conn.fetch("SELECT doc FROM chaos_runs"):
                run = ScenarioRun.model_validate_json(row["doc"])
                if run.status == "running":
                    run.status = "interrupted"
                self.runs[run.id] = run
            for row in await conn.fetch("SELECT key, value FROM chaos_state"):
                value = json.loads(row["value"])
                if row["key"] == "epoch":
                    self.epoch = int(value)
                elif row["key"] == "restarts":
                    self.restarts = {str(k): int(v) for k, v in value.items()}
                elif row["key"] == "replicas":
                    self.replicas = {str(k): int(v) for k, v in value.items()}
                elif row["key"] in self._counters:
                    self._counters[row["key"]] = int(value)
        # Toxiproxy keeps state in memory only: re-apply network faults after a restart.
        for fault in self.faults.values():
            if FAULT_SPECS[fault.type].mechanism == "network":
                try:
                    await self._apply_network(fault)
                except httpx.HTTPError as exc:
                    log(
                        logger,
                        logging.ERROR,
                        "could not re-apply network fault",
                        fault_id=fault.id,
                        **error_fields(exc),
                    )
        log(
            logger,
            logging.INFO,
            "chaos controller started",
            scenarios=len(self.scenarios),
            active_faults=len(self.faults),
            epoch=self.epoch,
        )

    async def stop(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        await self.toxiproxy.close()
        await self.deployer.aclose()
        if self._db:
            await self._db.close()

    # ------------------------------------------------------------------ persistence
    def _pool(self) -> asyncpg.Pool:
        if self._db is None:
            raise RuntimeError("chaos engine is not started")
        return self._db

    async def _save_fault(self, fault: ActiveFault) -> None:
        await self._pool().execute(
            "INSERT INTO chaos_faults (id, doc) VALUES ($1, $2) "
            "ON CONFLICT (id) DO UPDATE SET doc = $2",
            fault.id,
            fault.model_dump_json(),
        )

    async def _delete_fault(self, fault_id: str) -> None:
        await self._pool().execute("DELETE FROM chaos_faults WHERE id = $1", fault_id)

    async def _save_run(self, run: ScenarioRun) -> None:
        await self._pool().execute(
            "INSERT INTO chaos_runs (id, doc) VALUES ($1, $2) "
            "ON CONFLICT (id) DO UPDATE SET doc = $2",
            run.id,
            run.model_dump_json(),
        )

    async def _save_state(self, key: str, value: Any) -> None:
        await self._pool().execute(
            "INSERT INTO chaos_state (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = $2",
            key,
            json.dumps(value),
        )

    async def _next_id(self, kind: str, prefix: str) -> str:
        self._counters[kind] += 1
        await self._save_state(kind, self._counters[kind])
        return f"{prefix}-{self._counters[kind]:04d}"

    # ------------------------------------------------------------------ queries
    def runtime_faults(self, service: str) -> RuntimeFaults:
        faults = [
            RuntimeFault(id=f.id, type=f.type, params=f.params, seed=f.seed)
            for f in self.faults.values()
            if f.target == service and FAULT_SPECS[f.type].mechanism in ("in_app", "traffic")
        ]
        return RuntimeFaults(
            epoch=self.epoch,
            faults=faults,
            restart_generation=self.restarts.get(service, 0),
            replicas=self.replicas.get(service, 1),
        )

    # ------------------------------------------------------------------ orchestrator actions
    async def restart(self, service: str) -> dict[str, Any]:
        """Restart a service: its process exits on the next poll and Docker starts a new one."""
        async with self._lock:
            self.restarts[service] = self.restarts.get(service, 0) + 1
            await self._save_state("restarts", self.restarts)
        log(logger, logging.INFO, "restart requested", target=service)
        return {"service": service, "restart_generation": self.restarts[service]}

    async def scale(self, service: str, replicas: int) -> dict[str, Any]:
        async with self._lock:
            previous = self.replicas.get(service, 1)
            self.replicas[service] = replicas
            await self._save_state("replicas", self.replicas)
        log(logger, logging.INFO, "scaled", target=service, replicas=replicas)
        return {"service": service, "previous_replicas": previous, "replicas": replicas}

    async def clear_queue(self, queue: str) -> dict[str, Any]:
        key = {"fulfilment": FULFILMENT_QUEUE, "fulfilment-dead-letter": FULFILMENT_DLQ}[queue]
        client: aioredis.Redis = aioredis.from_url(self.settings.redis_url, socket_timeout=3)
        try:
            removed = int(await client.llen(key))
            await client.delete(key)
        finally:
            await client.aclose()
        log(logger, logging.INFO, "queue cleared", queue=queue, removed=removed)
        return {"queue": queue, "removed_jobs": removed}

    # ------------------------------------------------------------------ faults
    async def inject(self, req: FaultRequest, run_id: str | None = None) -> ActiveFault:
        async with self._lock:
            return await self._inject(req, run_id)

    async def _inject(self, req: FaultRequest, run_id: str | None) -> ActiveFault:
        fault = ActiveFault(
            id=await self._next_id("fault", "f"),
            type=req.type,
            target=req.target,
            params=req.params,
            seed=req.seed if req.seed is not None else self.settings.seed,
            scenario_run_id=run_id,
            created_at=_now(),
        )
        mechanism = FAULT_SPECS[fault.type].mechanism
        if mechanism == "network":
            await self._apply_network(fault)
        elif mechanism == "deployment":
            fault.params = {**fault.params, "restore": await self._runtime_of(fault.target)}
            deployment = await self._deploy(
                DeployStep(
                    service=fault.target,  # type: ignore[arg-type]
                    version=fault.params["version"],
                    changes_summary=fault.params["changes_summary"],
                    rollout_s=20,
                    outcome="failed",
                    failure_reason=(
                        "canary analysis failed: canary error rate above 5% threshold; "
                        "rollout halted with canary replicas still serving traffic"
                    ),
                    canary={  # type: ignore[arg-type]
                        "fraction": fault.params["canary_fraction"],
                        "error": (
                            "ConfigError: invalid value for ORDERS_SERVICE_TIMEOUT_MS: '2s' "
                            "(expected an integer number of milliseconds)"
                        ),
                    },
                )
            )
            fault.params["deployment_id"] = deployment["id"]
        self.faults[fault.id] = fault
        await self._save_fault(fault)
        log(
            logger,
            logging.INFO,
            "fault injected",
            fault_id=fault.id,
            fault_type=fault.type.value,
            target=fault.target,
            params=fault.params,
            scenario_run_id=run_id,
        )
        return fault

    async def remove(self, fault_id: str) -> ActiveFault:
        async with self._lock:
            return await self._remove(fault_id)

    async def _remove(self, fault_id: str, *, record_rollback: bool = True) -> ActiveFault:
        fault = self.faults.get(fault_id)
        if fault is None:
            raise EngineError(f"fault {fault_id} is not active", 404)
        mechanism = FAULT_SPECS[fault.type].mechanism
        if mechanism == "network":
            await self._revert_network(fault)
        elif mechanism == "deployment" and record_rollback:
            restore = fault.params.get("restore") or {}
            if restore:
                await self._deploy(
                    DeployStep(
                        service=fault.target,  # type: ignore[arg-type]
                        version=restore["version"],
                        kind="deploy",
                        changes_summary=f"Abort rollout of {fault.params['version']}; "
                        "scale canary replicas to zero",
                        author="oncall",
                        flags=restore["flags"],
                        rollout_s=5,
                    ),
                    kind="rollback",
                )
        del self.faults[fault_id]
        await self._delete_fault(fault_id)
        log(
            logger,
            logging.INFO,
            "fault removed",
            fault_id=fault_id,
            fault_type=fault.type.value,
            target=fault.target,
        )
        return fault

    async def _apply_network(self, fault: ActiveFault) -> None:
        proxy = PROXY_FOR_TARGET[fault.target]
        p = fault.params
        if fault.type == FaultType.DB_NETWORK_LATENCY:
            await self.toxiproxy.add_toxic(
                proxy, fault.id, "latency", {"latency": p["latency_ms"], "jitter": p["jitter_ms"]}
            )
        elif fault.type in (FaultType.REDIS_FAILURE, FaultType.DEPENDENCY_OUTAGE):
            mode = p["mode"]
            if mode == "down":
                await self.toxiproxy.set_enabled(proxy, False)
            elif mode == "timeout":
                await self.toxiproxy.add_toxic(proxy, fault.id, "timeout", {"timeout": 0})
            else:
                await self.toxiproxy.add_toxic(
                    proxy, fault.id, "latency", {"latency": p["latency_ms"]}
                )
        else:  # pragma: no cover - guarded by FAULT_SPECS
            raise EngineError(f"no network implementation for {fault.type}")

    async def _revert_network(self, fault: ActiveFault) -> None:
        proxy = PROXY_FOR_TARGET[fault.target]
        if fault.params.get("mode") == "down":
            still_down = any(
                f.id != fault.id
                and FAULT_SPECS[f.type].mechanism == "network"
                and PROXY_FOR_TARGET[f.target] == proxy
                and f.params.get("mode") == "down"
                for f in self.faults.values()
            )
            if not still_down:
                await self.toxiproxy.set_enabled(proxy, True)
        else:
            await self.toxiproxy.remove_toxic(proxy, fault.id)

    # ------------------------------------------------------------------ deployments
    async def _runtime_of(self, service: str) -> dict[str, Any]:
        r = await self.deployer.get(f"/internal/runtime/{service}")
        r.raise_for_status()
        result: dict[str, Any] = r.json()
        return result

    async def _deploy(self, step: DeployStep, kind: str | None = None) -> dict[str, Any]:
        body = step.model_dump(exclude_none=True)
        if kind:
            body["kind"] = kind
        r = await self.deployer.post("/api/v1/deployments", json=body)
        if r.status_code >= 400:
            raise EngineError(f"deployer rejected the deployment: {r.status_code} {r.text}", 502)
        result: dict[str, Any] = r.json()
        return result

    # ------------------------------------------------------------------ scenarios
    async def trigger(self, scenario_id: str, speed: float) -> ScenarioRun:
        scenario = self.scenarios.get(scenario_id)
        if scenario is None:
            raise EngineError(
                f"unknown scenario {scenario_id!r}; valid: {', '.join(sorted(self.scenarios))}", 404
            )
        async with self._lock:
            active = [r for r in self.runs.values() if r.status in ("running", "active")]
            if active:
                raise EngineError(
                    f"scenario run {active[0].id} ({active[0].scenario_id}) is still active; "
                    "clear it first (simctl clear) so effects do not overlap",
                    409,
                )
            run = ScenarioRun(
                id=await self._next_id("run", "run"),
                scenario_id=scenario.id,
                speed=speed,
                started_at=_now(),
                steps=[
                    StepState(index=i, at_s=s.at_s, description=s.description)
                    for i, s in enumerate(scenario.steps)
                ],
            )
            self.runs[run.id] = run
            await self._save_run(run)
        log(
            logger,
            logging.INFO,
            "scenario triggered",
            scenario_id=scenario.id,
            run_id=run.id,
            speed=speed,
        )
        self._tasks[run.id] = asyncio.create_task(self._execute(run, scenario), name=run.id)
        return run

    async def _execute(self, run: ScenarioRun, scenario: Scenario) -> None:
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        try:
            for state, step in zip(run.steps, scenario.steps, strict=True):
                delay = t0 + step.at_s / run.speed - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                async with self._lock:
                    try:
                        if step.deploy is not None:
                            service = step.deploy.service
                            if service not in run.restore:
                                run.restore[service] = await self._runtime_of(service)
                            deployment = await self._deploy(
                                step.deploy.model_copy(
                                    update={"rollout_s": step.deploy.rollout_s / max(run.speed, 1)}
                                )
                            )
                            state.deployment_id = deployment["id"]
                        elif step.fault is not None:
                            fault = await self._inject(
                                FaultRequest(
                                    type=step.fault.type,
                                    target=step.fault.target,
                                    params=step.fault.params,
                                    seed=scenario.seed,
                                ),
                                run.id,
                            )
                            state.fault_id = fault.id
                        state.status = "applied"
                        state.applied_at = _now()
                    except (EngineError, httpx.HTTPError) as exc:
                        state.status = "failed"
                        state.error = str(exc)
                        log(
                            logger,
                            logging.ERROR,
                            "scenario step failed",
                            run_id=run.id,
                            step=state.index,
                            **error_fields(exc),
                        )
                    await self._save_run(run)
            run.status = "failed" if any(s.status == "failed" for s in run.steps) else "active"
            await self._save_run(run)
        except asyncio.CancelledError:
            raise
        finally:
            self._tasks.pop(run.id, None)

    async def clear_run(self, run_id: str, *, record_rollbacks: bool = True) -> ScenarioRun:
        run = self.runs.get(run_id)
        if run is None:
            raise EngineError(f"unknown run {run_id}", 404)
        task = self._tasks.pop(run_id, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        async with self._lock:
            for state in reversed(run.steps):
                if state.status == "pending":
                    state.status = "skipped"
                if state.status != "applied":
                    continue
                if state.fault_id and state.fault_id in self.faults:
                    await self._remove(state.fault_id, record_rollback=record_rollbacks)
                state.status = "reverted"
            if record_rollbacks:
                # The deployer resolves a service's runtime from the newest *completed* record,
                # so this rollback wins even if the scenario's own rollout is still in progress.
                scenario = self.scenarios.get(run.scenario_id)
                steps = scenario.steps if scenario else []
                for service, runtime in run.restore.items():
                    reverted = [
                        step.deploy.version
                        for step, state in zip(steps, run.steps, strict=False)
                        if step.deploy and step.deploy.service == service and state.deployment_id
                    ]
                    if not reverted:
                        continue
                    await self._deploy(
                        DeployStep(
                            service=service,  # type: ignore[arg-type]
                            version=runtime["version"],
                            changes_summary=f"Rollback to {runtime['version']} "
                            f"(reverts {', '.join(reverted)})",
                            author="oncall",
                            flags=runtime["flags"],
                            rollout_s=5,
                        ),
                        kind="rollback",
                    )
            run.status = "cleared"
            run.cleared_at = _now()
            await self._save_run(run)
        log(logger, logging.INFO, "scenario cleared", run_id=run.id, scenario_id=run.scenario_id)
        return run

    async def clear_all(self) -> dict[str, Any]:
        cleared_runs = []
        for run in list(self.runs.values()):
            if (
                run.status in ("running", "active", "failed", "interrupted")
                and run.cleared_at is None
            ):
                await self.clear_run(run.id)
                cleared_runs.append(run.id)
        async with self._lock:
            removed = [(await self._remove(fid)).id for fid in list(self.faults)]
        return {"cleared_runs": cleared_runs, "removed_faults": removed}

    async def reset(self, *, data: bool) -> dict[str, Any]:
        """Return the whole environment to its baseline state."""
        for run_id in [r.id for r in self.runs.values() if r.cleared_at is None]:
            await self.clear_run(run_id, record_rollbacks=False)
        async with self._lock:
            for fault_id in list(self.faults):
                await self._remove(fault_id, record_rollback=False)
            await self.toxiproxy.reset()
            r = await self.deployer.post("/internal/reset")
            r.raise_for_status()
            await self._pool().execute("DELETE FROM chaos_runs")
            self.runs.clear()
            self._counters = {"fault": 0, "run": 0}
            for key in self._counters:
                await self._save_state(key, 0)
            self.replicas = {}
            await self._save_state("replicas", self.replicas)
            self.epoch += 1
            await self._save_state("epoch", self.epoch)
        # Give services one poll interval to drop in-app effects (return leaked connections).
        await asyncio.sleep(self.settings.poll_interval_s * 2)
        await self._reset_redis()
        await self._reset_shop_db(data=data)
        log(logger, logging.INFO, "environment reset", epoch=self.epoch, data_reset=data)
        return {"status": "reset", "epoch": self.epoch, "data_reset": data}

    async def _reset_redis(self) -> None:
        client: aioredis.Redis = aioredis.from_url(self.settings.redis_url, socket_timeout=3)
        try:
            await client.delete(FULFILMENT_QUEUE, FULFILMENT_DLQ)
            keys = [k async for k in client.scan_iter(match="catalog:*", count=500)]
            if keys:
                await client.delete(*keys)
        finally:
            await client.aclose()

    async def _reset_shop_db(self, *, data: bool) -> None:
        conn = await asyncpg.connect(self.settings.database_url, timeout=5)
        try:
            # Terminate leftover leaked sessions (idle in transaction) from the shop services.
            await conn.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = current_database() AND state = 'idle in transaction' "
                "AND pid <> pg_backend_pid()"
            )
            await conn.execute("SELECT pg_stat_statements_reset()")
            if data:
                await conn.execute(RESET_LIVE_DATA)
        finally:
            await conn.close()
