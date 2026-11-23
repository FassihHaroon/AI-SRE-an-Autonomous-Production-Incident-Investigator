"""orders-service: places orders (DB + payments + fulfilment queue) and serves order history.

Behaviour flags (set by deployments):

* ``order_history_query``: ``joined`` (default, one query) or ``per_order_items`` (v2.4.0:
  one extra query per order whose ``order_id::text`` comparison defeats the index, so every
  order in the history costs a sequential scan of ``order_items``).
* ``audit_trail``: when true, order lookups write an audit row on a second connection. The
  code path forgets to commit/release that connection for some lookups (connection leak).
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel, Field

from ai_sre_simulator.common import context, metrics
from ai_sre_simulator.common.cache import CacheUnavailableError, InstrumentedRedis
from ai_sre_simulator.common.db import InstrumentedPool, PoolTimeoutError
from ai_sre_simulator.common.determinism import sampler
from ai_sre_simulator.common.faults import FaultType
from ai_sre_simulator.common.http import AppError, ServiceClient, UpstreamError, instrument_app
from ai_sre_simulator.common.logging import log
from ai_sre_simulator.common.memory import MemoryBallast
from ai_sre_simulator.common.runtime import Runtime, RuntimeState
from ai_sre_simulator.common.settings import Settings
from ai_sre_simulator.shop import FULFILMENT_QUEUE

logger = logging.getLogger("orders")

AUDIT_LEAK_PROBABILITY = 0.3


class OrderItemIn(BaseModel):
    product_id: int = Field(ge=1)
    quantity: int = Field(ge=1, le=10)


class OrderIn(BaseModel):
    customer_id: int = Field(ge=1)
    items: list[OrderItemIn] = Field(min_length=1, max_length=10)


def create_app(settings: Settings) -> FastAPI:
    runtime = Runtime(settings)

    def slow_queries() -> set[str]:
        names: set[str] = set()
        for fault in runtime.state.faults_of(FaultType.SLOW_DB_QUERIES):
            names.update(fault.params["queries"])
        return names

    pool = InstrumentedPool(
        settings.database_url,
        max_size=settings.db_pool_size,
        acquire_timeout_s=settings.db_acquire_timeout_s,
        slow_queries=slow_queries,
    )
    cache = InstrumentedRedis(settings.redis_url, settings.redis_timeout_s)
    payments = ServiceClient("payments-provider", settings.payments_url, timeout_s=2.5)
    ballast = MemoryBallast(runtime)

    async def leak(fault_id: str) -> None:
        faults = runtime.state.faults_of(FaultType.DB_POOL_EXHAUSTION)
        limit = min(
            next((f.params["max_leaked"] for f in faults if f.params["max_leaked"]), pool.max_size),
            pool.max_size,
        )
        if pool.leaked_count >= limit:
            return
        with contextlib.suppress(PoolTimeoutError):
            await pool.leak_one("SELECT id, status FROM orders ORDER BY id DESC LIMIT 1")

    async def on_runtime_change(old: RuntimeState, new: RuntimeState) -> None:
        leaking = bool(new.faults_of(FaultType.DB_POOL_EXHAUSTION)) or bool(new.flag("audit_trail"))
        if not leaking and pool.leaked_count:
            released = await pool.release_leaked()
            log(logger, logging.INFO, "returned connections to the pool", released=released)

    runtime.on_change(on_runtime_change)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await pool.open()
        await runtime.start()
        ballast.start()
        log(logger, logging.INFO, "orders-service started", pool_size=pool.max_size)
        yield
        await ballast.stop()
        await runtime.stop()
        await payments.close()
        await cache.close()
        await pool.close()

    app = FastAPI(title="orders-service (simulated)", lifespan=lifespan)
    instrument_app(app, runtime, leak_hook=leak)

    async def set_status(order_id: int, status: str, payment_ref: str | None = None) -> None:
        async with pool.connection() as conn:
            await pool.execute(
                conn,
                "update_order_status",
                "UPDATE orders SET status = $2, payment_ref = COALESCE($3, payment_ref), "
                "updated_at = now() WHERE id = $1",
                order_id,
                status,
                payment_ref,
            )

    @app.post("/orders", status_code=201)
    async def create_order(order: OrderIn) -> dict[str, Any]:
        product_ids = [i.product_id for i in order.items]
        async with pool.connection() as conn:
            rows = await pool.fetch(
                conn,
                "get_product_prices",
                "SELECT id, price_cents FROM products WHERE id = ANY($1::int[])",
                product_ids,
            )
            prices = {r["id"]: r["price_cents"] for r in rows}
            missing = sorted(set(product_ids) - prices.keys())
            if missing:
                raise AppError(
                    f"unknown products: {missing}", status_code=422, code="unknown_product"
                )
            total = sum(prices[i.product_id] * i.quantity for i in order.items)
            async with conn.transaction():
                order_id: int = await pool.fetchval(
                    conn,
                    "insert_order",
                    "INSERT INTO orders (customer_id, status, total_cents) "
                    "VALUES ($1, 'pending_payment', $2) RETURNING id",
                    order.customer_id,
                    total,
                )
                await pool.execute(
                    conn,
                    "insert_order_items",
                    "INSERT INTO order_items (order_id, product_id, quantity, price_cents) "
                    "SELECT $1, p, q, c FROM unnest($2::int[], $3::int[], $4::int[]) AS t(p, q, c)",
                    order_id,
                    product_ids,
                    [i.quantity for i in order.items],
                    [prices[i.product_id] for i in order.items],
                )

        try:
            response = await payments.request(
                "POST",
                "/v1/charges",
                json={"amount_cents": total, "currency": "USD", "reference": f"order-{order_id}"},
            )
        except UpstreamError:
            metrics.ORDERS.labels("payment_failed").inc()
            await set_status(order_id, "payment_failed")
            raise
        if response.status_code >= 400:
            metrics.ORDERS.labels("payment_failed").inc()
            await set_status(order_id, "payment_failed")
            raise UpstreamError(
                "payments-provider",
                f"payment for order {order_id} failed: payments-provider returned HTTP "
                f"{response.status_code} {response.reason_phrase}",
                status=response.status_code,
            )
        charge_id = str(response.json().get("charge_id"))
        await set_status(order_id, "paid", charge_id)

        job = {
            "order_id": order_id,
            "attempt": 1,
            "first_enqueued_at": time.time(),
            "trace_id": context.trace_id_var.get(),
            "request_id": context.request_id_var.get(),
        }
        try:
            await cache.lpush(FULFILMENT_QUEUE, json.dumps(job))
        except CacheUnavailableError as exc:
            metrics.ORDERS.labels("enqueue_failed").inc()
            raise AppError(
                f"order {order_id} was paid but its fulfilment job could not be queued: {exc}",
                status_code=503,
                code="queue_unavailable",
            ) from exc
        metrics.QUEUE_ENQUEUED.labels("fulfilment").inc()
        metrics.ORDERS.labels("success").inc()
        log(
            logger,
            logging.INFO,
            "order placed",
            order_id=order_id,
            total_cents=total,
            customer_id=order.customer_id,
        )
        return {"order_id": order_id, "status": "paid", "total_cents": total}

    @app.get("/orders/{order_id}")
    async def get_order(order_id: int) -> dict[str, Any]:
        if runtime.state.flag("audit_trail") and sampler.chance(
            runtime.state.seed, "audit-leak", AUDIT_LEAK_PROBABILITY
        ):
            with contextlib.suppress(PoolTimeoutError):
                await pool.leak_one(
                    "INSERT INTO order_audit (order_id, action) VALUES ($1, 'viewed')", order_id
                )
        async with pool.connection() as conn:
            row = await pool.fetchrow(
                conn,
                "get_order",
                "SELECT id, customer_id, status, total_cents, payment_ref, created_at "
                "FROM orders WHERE id = $1",
                order_id,
            )
            if row is None:
                raise AppError(f"order {order_id} not found", status_code=404, code="not_found")
            items = await pool.fetch(
                conn,
                "get_order_items",
                "SELECT product_id, quantity, price_cents FROM order_items WHERE order_id = $1",
                order_id,
            )
        return {**dict(row), "items": [dict(i) for i in items]}

    @app.get("/customers/{customer_id}/orders")
    async def order_history(customer_id: int, limit: int = 20) -> dict[str, Any]:
        limit = max(1, min(limit, 50))
        mode = runtime.state.flag("order_history_query", "joined")
        async with pool.connection() as conn:
            if mode == "per_order_items":
                orders = await pool.fetch(
                    conn,
                    "get_customer_orders",
                    "SELECT id, status, total_cents, created_at FROM orders "
                    "WHERE customer_id = $1 ORDER BY created_at DESC LIMIT $2",
                    customer_id,
                    limit,
                )
                result = []
                for o in orders:
                    items = await pool.fetch(
                        conn,
                        "get_order_items_for_history",
                        "SELECT * FROM order_items WHERE order_id::text = $1",
                        str(o["id"]),
                    )
                    result.append({**dict(o), "items": [dict(i) for i in items]})
            else:
                rows = await pool.fetch(
                    conn,
                    "get_customer_order_history",
                    "SELECT o.id, o.status, o.total_cents, o.created_at, count(i.id) AS item_count "
                    "FROM orders o LEFT JOIN order_items i ON i.order_id = o.id "
                    "WHERE o.customer_id = $1 GROUP BY o.id ORDER BY o.created_at DESC LIMIT $2",
                    customer_id,
                    limit,
                )
                result = [dict(r) for r in rows]
        return {"customer_id": customer_id, "orders": result}

    return app
