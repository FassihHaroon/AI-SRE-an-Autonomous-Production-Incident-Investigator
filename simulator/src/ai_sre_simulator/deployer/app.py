"""deployer HTTP API.

Public (what an SRE or, later, the investigator reads):

* ``GET /api/v1/deployments``: history, newest first (filters: service, since, until, status).
* ``GET /api/v1/deployments/{id}``
* ``GET /api/v1/services``: version currently running per service.

Control (used by the chaos controller and ``simctl``):

* ``POST /api/v1/deployments``: start a rollout (deploy, rollback or config change).
* ``GET /internal/runtime/{service}``: version and behaviour flags a service should run.
* ``POST /internal/reset``: restore the deterministic baseline history.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from fastapi import FastAPI, HTTPException, Query
from prometheus_client import Counter, Gauge

from ai_sre_simulator.common.db import InstrumentedPool
from ai_sre_simulator.common.faults import APP_SERVICES
from ai_sre_simulator.common.http import instrument_app
from ai_sre_simulator.common.logging import log
from ai_sre_simulator.common.settings import Settings
from ai_sre_simulator.deployer.models import (
    BASELINE,
    AppService,
    Deployment,
    DeployRequest,
    RollbackRequest,
    RuntimeConfig,
    ServiceVersion,
    commit_sha_for,
)

logger = logging.getLogger("deployer")

DEPLOYMENTS = Counter(
    "deployments_total", "Completed deployments", ["target_service", "kind", "status"]
)
IN_PROGRESS = Gauge("deployments_in_progress", "Rollouts in progress", ["target_service"])
LAST_COMPLETED = Gauge(
    "deployment_last_completed_timestamp_seconds",
    "Unix time the last rollout of a service completed",
    ["target_service"],
)

FIRST_ID = 401
BASELINE_ROLLOUT = timedelta(seconds=42)

SCHEMA = f"""
CREATE SEQUENCE IF NOT EXISTS deployment_seq START {FIRST_ID};
CREATE TABLE IF NOT EXISTS deployments (
    seq              bigint PRIMARY KEY DEFAULT nextval('deployment_seq'),
    id               text NOT NULL UNIQUE,
    service          text NOT NULL,
    kind             text NOT NULL,
    version          text NOT NULL,
    previous_version text,
    status           text NOT NULL,
    started_at       timestamptz NOT NULL,
    finished_at      timestamptz,
    changes_summary  text NOT NULL,
    author           text NOT NULL,
    commit_sha       text,
    failure_reason   text,
    flags            jsonb NOT NULL DEFAULT '{{}}',
    runtime_version  text,
    runtime_flags    jsonb
);
CREATE INDEX IF NOT EXISTS deployments_service_seq_idx ON deployments (service, seq DESC);
"""

_PUBLIC_COLUMNS = (
    "id, service, kind, version, previous_version, status, started_at, finished_at, "
    "changes_summary, author, commit_sha, failure_reason"
)


def _public(row: asyncpg.Record) -> Deployment:
    return Deployment.model_validate(dict(row))


class DeployerStore:
    def __init__(self, pool: InstrumentedPool) -> None:
        self.pool = pool

    async def init(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("SELECT pg_advisory_lock(4242)")
            try:
                await conn.execute(SCHEMA)
                if not await conn.fetchval("SELECT count(*) FROM deployments"):
                    await self._seed_baseline(conn)
                # A restart mid-rollout leaves records in progress: close them as failed.
                await conn.execute(
                    "UPDATE deployments SET status = 'failed', finished_at = now(), "
                    "failure_reason = 'rollout interrupted (deployer restarted)' "
                    "WHERE status = 'in_progress'"
                )
            finally:
                await conn.execute("SELECT pg_advisory_unlock(4242)")

    async def _seed_baseline(self, conn: asyncpg.pool.PoolConnectionProxy) -> None:
        now = datetime.now(UTC)
        current: dict[str, str] = {}
        for item in BASELINE:
            started = now - item.age
            seq = await conn.fetchval("SELECT nextval('deployment_seq')")
            await conn.execute(
                "INSERT INTO deployments (seq, id, service, kind, version, previous_version, "
                "status, started_at, finished_at, changes_summary, author, commit_sha, flags, "
                "runtime_version, runtime_flags) VALUES ($1, $2, $3, 'deploy', $4, $5, "
                "'succeeded', $6, $7, $8, $9, $10, '{}', $4, '{}')",
                seq,
                f"d-{seq}",
                item.service,
                item.version,
                current.get(item.service),
                started,
                started + BASELINE_ROLLOUT,
                item.changes_summary,
                item.author,
                commit_sha_for(item.service, item.version),
            )
            current[item.service] = item.version

    async def reset(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute("TRUNCATE deployments")
            await conn.execute(f"ALTER SEQUENCE deployment_seq RESTART WITH {FIRST_ID}")
            await self._seed_baseline(conn)

    async def rollback_target(self, service: str) -> RuntimeConfig | None:
        """The runtime the service ran before its current one (what a rollback restores)."""
        async with self.pool.connection() as conn:
            rows = await conn.fetch(
                "SELECT runtime_version, runtime_flags FROM deployments WHERE service = $1 "
                "AND runtime_version IS NOT NULL ORDER BY seq DESC LIMIT 2",
                service,
            )
        if len(rows) < 2:
            return None
        return RuntimeConfig(
            version=rows[1]["runtime_version"], flags=_json(rows[1]["runtime_flags"])
        )

    async def runtime(self, service: str) -> RuntimeConfig | None:
        async with self.pool.connection() as conn:
            row = await conn.fetchrow(
                "SELECT runtime_version, runtime_flags FROM deployments WHERE service = $1 "
                "AND runtime_version IS NOT NULL ORDER BY seq DESC LIMIT 1",
                service,
            )
        if row is None:
            return None
        return RuntimeConfig(version=row["runtime_version"], flags=_json(row["runtime_flags"]))

    async def list(
        self,
        service: str | None,
        since: datetime | None,
        until: datetime | None,
        status: str | None,
        limit: int,
    ) -> list[Deployment]:
        async with self.pool.connection() as conn:
            rows = await conn.fetch(
                f"SELECT {_PUBLIC_COLUMNS} FROM deployments "  # noqa: S608 - constant column list
                "WHERE ($1::text IS NULL OR service = $1) "
                "AND ($2::timestamptz IS NULL OR started_at >= $2) "
                "AND ($3::timestamptz IS NULL OR started_at <= $3) "
                "AND ($4::text IS NULL OR status = $4) ORDER BY seq DESC LIMIT $5",
                service,
                since,
                until,
                status,
                limit,
            )
        return [_public(r) for r in rows]

    async def get(self, deployment_id: str) -> Deployment | None:
        async with self.pool.connection() as conn:
            row = await conn.fetchrow(
                f"SELECT {_PUBLIC_COLUMNS} FROM deployments WHERE id = $1",  # noqa: S608
                deployment_id,
            )
        return _public(row) if row else None

    async def start(self, req: DeployRequest) -> Deployment:
        """Record a rollout. Until it completes the service keeps its current runtime, unless
        the rollout has a (broken) canary: canary replicas take traffic from the start."""
        current = await self.runtime(req.service)
        runtime_version: str | None = None
        runtime_flags: str | None = None
        if req.canary is not None and current is not None:
            runtime_version = current.version
            runtime_flags = json.dumps(self._with_canary(current.flags, req))
        async with self.pool.connection() as conn:
            seq = await conn.fetchval("SELECT nextval('deployment_seq')")
            row = await conn.fetchrow(
                "INSERT INTO deployments (seq, id, service, kind, version, previous_version, "  # noqa: S608
                "status, started_at, changes_summary, author, commit_sha, flags, runtime_version, "
                "runtime_flags) VALUES ($1, $2, $3, $4, $5, $6, 'in_progress', now(), $7, $8, $9, "
                f"$10, $11, $12) RETURNING {_PUBLIC_COLUMNS}",
                seq,
                f"d-{seq}",
                req.service,
                req.kind,
                req.version,
                current.version if current else None,
                req.changes_summary,
                req.author,
                req.commit_sha or commit_sha_for(req.service, req.version),
                json.dumps(req.flags),
                runtime_version,
                runtime_flags,
            )
        if row is None:
            raise RuntimeError("deployment row vanished during the write")
        return _public(row)

    @staticmethod
    def _with_canary(flags: dict[str, Any], req: DeployRequest) -> dict[str, Any]:
        result = {k: v for k, v in flags.items() if k != "canary"}
        if req.canary is not None:
            result["canary"] = {
                "version": req.version,
                "fraction": req.canary.fraction,
                "error": req.canary.error,
            }
        return result

    async def finish(self, deployment: Deployment, req: DeployRequest) -> Deployment:
        async with self.pool.connection() as conn:
            row = await conn.fetchrow(
                "SELECT runtime_version, runtime_flags FROM deployments WHERE service = $1 "
                "AND runtime_version IS NOT NULL AND id <> $2 ORDER BY seq DESC LIMIT 1",
                req.service,
                deployment.id,
            )
        current_flags = _json(row["runtime_flags"]) if row else {}
        current_flags = {k: v for k, v in current_flags.items() if k != "canary"}
        if req.outcome == "succeeded":
            runtime_version = req.version
            flags = {**current_flags, **req.flags} if req.kind == "config_change" else req.flags
            failure = None
        else:
            runtime_version = deployment.previous_version or req.version
            flags = self._with_canary(current_flags, req)
            failure = req.failure_reason or "rollout failed"
        async with self.pool.connection() as conn:
            row = await conn.fetchrow(
                "UPDATE deployments SET status = $2, finished_at = now(), failure_reason = $3, "  # noqa: S608
                "runtime_version = $4, runtime_flags = $5 WHERE id = $1 "
                f"RETURNING {_PUBLIC_COLUMNS}",
                deployment.id,
                req.outcome,
                failure,
                runtime_version,
                json.dumps(flags),
            )
        if row is None:
            raise RuntimeError("deployment row vanished during the write")
        return _public(row)


def _json(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        parsed: dict[str, Any] = json.loads(value)
        return parsed
    return dict(value)


def create_app(settings: Settings) -> FastAPI:
    pool = InstrumentedPool(
        settings.control_database_url, max_size=settings.db_pool_size, acquire_timeout_s=5.0
    )
    store = DeployerStore(pool)
    rollouts: set[asyncio.Task[None]] = set()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await pool.open()
        await store.init()
        log(logger, logging.INFO, "deployer started")
        yield
        for task in rollouts:
            task.cancel()
        await pool.close()

    app = FastAPI(title="deployer (simulated CD system)", lifespan=lifespan)
    instrument_app(app, None)

    async def run_rollout(deployment: Deployment, req: DeployRequest) -> None:
        IN_PROGRESS.labels(req.service).inc()
        try:
            await asyncio.sleep(req.rollout_s)
            done = await store.finish(deployment, req)
        finally:
            IN_PROGRESS.labels(req.service).dec()
        DEPLOYMENTS.labels(done.service, done.kind, done.status).inc()
        LAST_COMPLETED.labels(done.service).set(datetime.now(UTC).timestamp())
        fields = _log_fields(done)
        if done.status == "succeeded":
            log(
                logger,
                logging.INFO,
                f"deployment {done.id} succeeded: {done.service} is now running {done.version}",
                **fields,
            )
        else:
            log(
                logger,
                logging.ERROR,
                f"deployment {done.id} failed: {done.service} {done.version}: "
                f"{done.failure_reason}",
                **fields,
            )

    @app.post("/api/v1/deployments", status_code=202, response_model=Deployment)
    async def create_deployment(req: DeployRequest) -> Deployment:
        deployment = await store.start(req)
        log(
            logger,
            logging.INFO,
            f"deployment {deployment.id} started: {deployment.service} "
            f"{deployment.previous_version} -> {deployment.version}",
            **_log_fields(deployment),
        )
        task = asyncio.create_task(run_rollout(deployment, req))
        rollouts.add(task)
        task.add_done_callback(rollouts.discard)
        return deployment

    @app.post("/api/v1/deployments/{service}/rollback", status_code=202, response_model=Deployment)
    async def rollback(service: AppService, body: RollbackRequest) -> Deployment:
        current = await store.runtime(service)
        target = await store.rollback_target(service)
        if current is None or target is None:
            raise HTTPException(409, f"{service} has no earlier deployment to roll back to")
        req = DeployRequest(
            service=service,
            version=target.version,
            kind="rollback",
            changes_summary=f"Rollback {current.version} -> {target.version}: {body.reason}",
            author=body.author,
            flags=target.flags,
            rollout_s=body.rollout_s,
        )
        return await create_deployment(req)

    @app.get("/api/v1/deployments", response_model=list[Deployment])
    async def list_deployments(
        service: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        status: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[Deployment]:
        return await store.list(service, since, until, status, limit)

    @app.get("/api/v1/deployments/{deployment_id}", response_model=Deployment)
    async def get_deployment(deployment_id: str) -> Deployment:
        deployment = await store.get(deployment_id)
        if deployment is None:
            raise HTTPException(404, f"deployment {deployment_id} not found")
        return deployment

    @app.get("/api/v1/services", response_model=list[ServiceVersion])
    async def services() -> list[ServiceVersion]:
        result = []
        for service in APP_SERVICES:
            latest = [
                d
                for d in await store.list(service, None, None, None, 20)
                if d.status != "in_progress"
            ]
            runtime = await store.runtime(service)
            if runtime and latest:
                result.append(
                    ServiceVersion(
                        service=service,
                        version=runtime.version,
                        deployment_id=latest[0].id,
                        deployed_at=latest[0].finished_at,
                    )
                )
        return result

    @app.get("/internal/runtime/{service}", response_model=RuntimeConfig)
    async def runtime(service: str) -> RuntimeConfig:
        config = await store.runtime(service)
        if config is None:
            raise HTTPException(404, f"no deployment recorded for {service}")
        return config

    @app.post("/internal/reset")
    async def reset() -> dict[str, str]:
        for task in list(rollouts):
            task.cancel()
        await store.reset()
        log(logger, logging.INFO, "deployment history reset to baseline")
        return {"status": "reset"}

    return app


def _log_fields(d: Deployment) -> dict[str, Any]:
    return {
        "deployment_id": d.id,
        "target_service": d.service,
        "deployment_kind": d.kind,
        "target_version": d.version,
        "previous_version": d.previous_version,
        "deployment_status": d.status,
        "changes_summary": d.changes_summary,
        "author": d.author,
        "commit_sha": d.commit_sha,
    }
