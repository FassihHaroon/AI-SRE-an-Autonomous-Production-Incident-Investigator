"""fulfilment-worker: consumes ``queue:fulfilment`` and marks paid orders as fulfilled.

Failed jobs are retried (re-queued at the back) up to ``MAX_ATTEMPTS`` times, then moved to the
dead-letter queue. Metrics are served on port 9100.

Behaviour flags (set by deployments):

* ``label_service``: ``{"timeout_s", "failure_rate"}``. v1.7.0 switched to a new shipping-label
  client whose endpoint is wrong: each call hangs for ``timeout_s`` and most fail.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from prometheus_client import start_http_server
from redis.exceptions import RedisError

from ai_sre_simulator.common import context, metrics
from ai_sre_simulator.common.cache import CacheUnavailableError, InstrumentedRedis
from ai_sre_simulator.common.db import InstrumentedPool, PoolTimeoutError
from ai_sre_simulator.common.determinism import sampler
from ai_sre_simulator.common.faults import FaultType
from ai_sre_simulator.common.logging import error_fields, log
from ai_sre_simulator.common.memory import MemoryBallast
from ai_sre_simulator.common.runtime import Runtime, RuntimeState
from ai_sre_simulator.common.settings import Settings
from ai_sre_simulator.shop import FULFILMENT_DLQ, FULFILMENT_QUEUE

logger = logging.getLogger("worker")

QUEUE = "fulfilment"
MAX_ATTEMPTS = 5
METRICS_PORT = 9100


class LabelServiceTimeoutError(Exception):
    pass


class FulfilmentStepError(Exception):
    pass


class Worker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.runtime = Runtime(settings)
        self.pool = InstrumentedPool(
            settings.database_url,
            max_size=settings.db_pool_size,
            acquire_timeout_s=settings.db_acquire_timeout_s,
            slow_queries=self._slow_queries,
        )
        # BRPOP blocks for up to 1 s, so the socket timeout must be longer than that.
        self.cache = InstrumentedRedis(settings.redis_url, timeout_s=settings.redis_timeout_s + 1.5)
        self.ballast = MemoryBallast(self.runtime)
        self.runtime.on_change(self._on_runtime_change)

    def _slow_queries(self) -> set[str]:
        names: set[str] = set()
        for fault in self.runtime.state.faults_of(FaultType.SLOW_DB_QUERIES):
            names.update(fault.params["queries"])
        return names

    async def _on_runtime_change(self, _old: RuntimeState, new: RuntimeState) -> None:
        if not new.faults_of(FaultType.DB_POOL_EXHAUSTION) and self.pool.leaked_count:
            await self.pool.release_leaked()

    # ------------------------------------------------------------------ main
    async def run(self) -> None:
        start_http_server(METRICS_PORT)
        await self.pool.open()
        await self.runtime.start()
        self.ballast.start()
        log(
            logger,
            logging.INFO,
            "fulfilment-worker started",
            concurrency=self.settings.worker_concurrency,
            queue=FULFILMENT_QUEUE,
        )
        tasks = [
            asyncio.create_task(self._consume(i), name=f"consumer-{i}")
            for i in range(self.settings.worker_concurrency)
        ]
        tasks.append(asyncio.create_task(self._monitor_queue(), name="queue-monitor"))
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            await self.ballast.stop()
            await self.runtime.stop()
            await self.cache.close()
            await self.pool.close()

    async def _consume(self, consumer: int) -> None:
        while True:
            try:
                item: Any = await self.cache.call(
                    "BRPOP", lambda r: r.brpop([FULFILMENT_QUEUE], timeout=1)
                )
            except CacheUnavailableError as exc:
                log(
                    logger,
                    logging.ERROR,
                    "cannot read from fulfilment queue",
                    consumer=consumer,
                    **error_fields(exc),
                )
                await asyncio.sleep(1)
                continue
            if item is None:
                continue
            await self._process(item[1])

    async def _monitor_queue(self) -> None:
        while True:
            try:
                depth = await self.cache.llen(FULFILMENT_QUEUE)
                dead = await self.cache.llen(FULFILMENT_DLQ)
                oldest: Any = await self.cache.call(
                    "LINDEX", lambda r: r.lindex(FULFILMENT_QUEUE, -1)
                )
                metrics.QUEUE_DEPTH.labels(QUEUE).set(depth)
                metrics.QUEUE_DEPTH.labels(f"{QUEUE}-dead-letter").set(dead)
                age = 0.0
                if oldest:
                    with contextlib.suppress(ValueError, KeyError, TypeError):
                        age = max(0.0, time.time() - float(json.loads(oldest)["first_enqueued_at"]))
                metrics.QUEUE_OLDEST_AGE.labels(QUEUE).set(age)
            except (CacheUnavailableError, RedisError):
                pass
            await asyncio.sleep(2)

    # ------------------------------------------------------------------ job
    async def _process(self, raw: str) -> None:
        try:
            job: dict[str, Any] = json.loads(raw)
        except ValueError:
            log(logger, logging.ERROR, "discarding malformed fulfilment job", raw=raw[:200])
            return
        job.setdefault("first_enqueued_at", job.get("enqueued_at", time.time()))
        tokens = (
            context.trace_id_var.set(job.get("trace_id") or context.new_trace_id()),
            context.span_id_var.set(context.new_span_id()),
            context.request_id_var.set(job.get("request_id")),
            context.endpoint_var.set(f"job {FULFILMENT_QUEUE}"),
        )
        metrics.WORKER_BUSY.inc()
        started = time.perf_counter()
        attempt = int(job.get("attempt", 1))
        try:
            await self._fulfil(job)
            metrics.WORKER_JOBS.labels(QUEUE, "success").inc()
            log(
                logger,
                logging.INFO,
                "order fulfilled",
                order_id=job.get("order_id"),
                attempt=attempt,
                duration_ms=round((time.perf_counter() - started) * 1000, 1),
                queue_wait_s=round(time.time() - float(job["first_enqueued_at"]), 2),
            )
        except Exception as exc:
            metrics.WORKER_FAILURES.labels(QUEUE, type(exc).__name__).inc()
            await self._retry_or_dead_letter(job, attempt, exc)
        finally:
            metrics.WORKER_DURATION.labels(QUEUE).observe(time.perf_counter() - started)
            metrics.WORKER_BUSY.dec()
            for var, token in zip(
                (
                    context.trace_id_var,
                    context.span_id_var,
                    context.request_id_var,
                    context.endpoint_var,
                ),
                tokens,
                strict=True,
            ):
                var.reset(token)

    async def _retry_or_dead_letter(
        self, job: dict[str, Any], attempt: int, exc: Exception
    ) -> None:
        order_id = job.get("order_id")
        try:
            if attempt < MAX_ATTEMPTS:
                await self.cache.lpush(
                    FULFILMENT_QUEUE, json.dumps({**job, "attempt": attempt + 1})
                )
                metrics.WORKER_JOBS.labels(QUEUE, "retried").inc()
                log(
                    logger,
                    logging.ERROR,
                    f"fulfilment of order {order_id} failed (attempt {attempt}/"
                    f"{MAX_ATTEMPTS}), re-queued",
                    order_id=order_id,
                    attempt=attempt,
                    **error_fields(exc),
                )
            else:
                await self.cache.lpush(FULFILMENT_DLQ, json.dumps(job))
                metrics.WORKER_JOBS.labels(QUEUE, "dead_lettered").inc()
                log(
                    logger,
                    logging.ERROR,
                    f"fulfilment of order {order_id} failed {MAX_ATTEMPTS} "
                    "times, moved to dead-letter queue",
                    order_id=order_id,
                    attempt=attempt,
                    **error_fields(exc),
                )
        except CacheUnavailableError as requeue_exc:
            log(
                logger,
                logging.CRITICAL,
                f"fulfilment job for order {order_id} lost: could not re-queue",
                order_id=order_id,
                **error_fields(requeue_exc),
            )

    async def _fulfil(self, job: dict[str, Any]) -> None:
        state = self.runtime.state
        seed = self.settings.seed
        label = state.flag("label_service")
        if label:
            await asyncio.sleep(float(label["timeout_s"]))
            if sampler.chance(seed, "label-service", float(label["failure_rate"])):
                raise LabelServiceTimeoutError(
                    f"POST https://labels.shipping.internal/v2/labels timed out after "
                    f"{float(label['timeout_s']):.1f}s"
                )
        for fault in state.faults_of(FaultType.WORKER_BACKLOG):
            p = fault.params
            await asyncio.sleep(p["delay_ms"] / 1000)
            if sampler.chance(fault.seed, fault.id, p["failure_rate"]):
                raise FulfilmentStepError(
                    "warehouse reservation was not confirmed within the step deadline"
                )
        for fault in state.faults_of(FaultType.DB_POOL_EXHAUSTION):
            if self.pool.leaked_count < self.pool.max_size and sampler.chance(
                fault.seed, fault.id, fault.params["leak_probability"]
            ):
                with contextlib.suppress(PoolTimeoutError):
                    await self.pool.leak_one("SELECT id FROM orders WHERE status = 'paid' LIMIT 1")
        # Normal work: pick, pack (30-80 ms), then persist.
        await asyncio.sleep((55 + sampler.jitter(seed, "pack-time", 25)) / 1000)
        async with self.pool.connection() as conn:
            await self.pool.execute(
                conn,
                "mark_order_fulfilled",
                "UPDATE orders SET status = 'fulfilled', updated_at = now() "
                "WHERE id = $1 AND status = 'paid'",
                job["order_id"],
            )


def run(settings: Settings) -> None:
    asyncio.run(Worker(settings).run())
