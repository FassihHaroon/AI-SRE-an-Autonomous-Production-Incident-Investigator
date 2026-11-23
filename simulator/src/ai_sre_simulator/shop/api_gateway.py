"""api-gateway: the public shop API.

Owns the product catalog (PostgreSQL, cache-aside in Redis) and shopping carts (Redis only), and
proxies order operations to orders-service.

Behaviour flags (set by deployments):

* ``canary`` (any HTTP service, handled in ``common.http``): a failed rollout left canary
  replicas of a new build serving a fraction of the traffic; those requests fail.
* ``recommendations_cache_kb``: an in-process recommendations cache with no eviction. Every
  product-detail request adds this many KB (memory leak).
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ai_sre_simulator.common import metrics
from ai_sre_simulator.common.cache import CacheUnavailableError, InstrumentedRedis
from ai_sre_simulator.common.db import InstrumentedPool, PoolTimeoutError
from ai_sre_simulator.common.faults import FaultType
from ai_sre_simulator.common.http import AppError, ServiceClient, UpstreamError, instrument_app
from ai_sre_simulator.common.logging import error_fields, log
from ai_sre_simulator.common.memory import LeakyCache, MemoryBallast
from ai_sre_simulator.common.runtime import Runtime, RuntimeState
from ai_sre_simulator.common.settings import Settings

logger = logging.getLogger("gateway")

CATALOG_TTL_S = 10


class CartItemIn(BaseModel):
    product_id: int = Field(ge=1)
    quantity: int = Field(default=1, ge=1, le=10)


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
    orders = ServiceClient("orders-service", settings.orders_url, timeout_s=5.0)
    ballast = MemoryBallast(runtime)
    recommendations = LeakyCache()

    async def leak(_fault_id: str) -> None:
        if pool.leaked_count < pool.max_size:
            with contextlib.suppress(PoolTimeoutError):
                await pool.leak_one("SELECT id FROM products ORDER BY id LIMIT 1")

    async def on_runtime_change(old: RuntimeState, new: RuntimeState) -> None:
        if not new.faults_of(FaultType.DB_POOL_EXHAUSTION) and pool.leaked_count:
            await pool.release_leaked()
        if not new.flag("recommendations_cache_kb"):
            recommendations.clear()

    runtime.on_change(on_runtime_change)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        await pool.open()
        await runtime.start()
        ballast.start()
        log(logger, logging.INFO, "api-gateway started")
        yield
        await ballast.stop()
        await runtime.stop()
        await orders.close()
        await cache.close()
        await pool.close()

    app = FastAPI(title="api-gateway (simulated)", lifespan=lifespan)
    instrument_app(app, runtime, leak_hook=leak)
    api = APIRouter(prefix="/api")

    async def cached_json(key: str, loader: Any) -> Any:
        try:
            raw = await cache.get(key)
        except CacheUnavailableError as exc:
            metrics.CACHE_REQUESTS.labels("catalog", "error").inc()
            log(
                logger,
                logging.WARNING,
                "catalog cache unavailable, reading from database",
                cache_key=key,
                **error_fields(exc),
            )
            return await loader()
        if raw is not None:
            metrics.CACHE_REQUESTS.labels("catalog", "hit").inc()
            return json.loads(raw)
        metrics.CACHE_REQUESTS.labels("catalog", "miss").inc()
        value = await loader()
        try:
            await cache.set(key, json.dumps(value, default=str), ex=CATALOG_TTL_S)
        except CacheUnavailableError as exc:
            log(
                logger,
                logging.WARNING,
                "could not populate catalog cache",
                cache_key=key,
                **error_fields(exc),
            )
        return value

    @api.get("/products")
    async def list_products() -> Any:
        async def load() -> list[dict[str, Any]]:
            async with pool.connection() as conn:
                rows = await pool.fetch(
                    conn,
                    "list_products",
                    "SELECT id, sku, name, price_cents, stock FROM products ORDER BY id LIMIT 50",
                )
            return [dict(r) for r in rows]

        return {"products": await cached_json("catalog:products", load)}

    @api.get("/products/{product_id}")
    async def get_product(product_id: int) -> Any:
        async def load() -> dict[str, Any] | None:
            async with pool.connection() as conn:
                row = await pool.fetchrow(
                    conn,
                    "get_product",
                    "SELECT id, sku, name, price_cents, stock FROM products WHERE id = $1",
                    product_id,
                )
                if row is None:
                    return None
                related = await pool.fetch(
                    conn,
                    "get_related_products",
                    "SELECT id, name, price_cents FROM products WHERE id <> $1 "
                    "ORDER BY abs(price_cents - $2), id LIMIT 4",
                    product_id,
                    row["price_cents"],
                )
            return {**dict(row), "related": [dict(r) for r in related]}

        product = await cached_json(f"catalog:product:{product_id}", load)
        if product is None:
            raise AppError(f"product {product_id} not found", status_code=404, code="not_found")
        leak_kb = runtime.state.flag("recommendations_cache_kb")
        if leak_kb:
            recommendations.add(int(leak_kb))
        return product

    @api.get("/cart/{customer_id}")
    async def get_cart(customer_id: int) -> Any:
        raw: Any = await cache.call("HGETALL", lambda r: r.hgetall(f"cart:{customer_id}"))
        items = {int(k): int(v) for k, v in dict(raw).items()}
        return {"customer_id": customer_id, "items": items}

    @api.post("/cart/{customer_id}/items", status_code=201)
    async def add_to_cart(customer_id: int, item: CartItemIn) -> Any:
        key = f"cart:{customer_id}"

        async def op(r: Any) -> Any:
            async with r.pipeline(transaction=True) as pipe:
                pipe.hincrby(key, str(item.product_id), item.quantity)
                pipe.expire(key, 3600)
                return await pipe.execute()

        await cache.call("HINCRBY", op)
        return {"customer_id": customer_id, "product_id": item.product_id}

    async def proxy(method: str, path: str, body: Any = None) -> Response:
        response = await orders.request(method, path, json=body)
        if response.status_code >= 500:
            try:
                detail = response.json().get("message", response.text)
            except ValueError:
                detail = response.text
            raise UpstreamError(
                "orders-service",
                f"orders-service returned HTTP {response.status_code} for {method} {path}: "
                f"{detail}",
                status=response.status_code,
            )
        return JSONResponse(response.json(), status_code=response.status_code)

    @api.post("/orders")
    async def place_order(request: Request) -> Response:
        return await proxy("POST", "/orders", await request.json())

    @api.get("/orders/{order_id}")
    async def get_order(order_id: int) -> Response:
        return await proxy("GET", f"/orders/{order_id}")

    @api.get("/customers/{customer_id}/orders")
    async def order_history(customer_id: int) -> Response:
        return await proxy("GET", f"/customers/{customer_id}/orders")

    app.include_router(api)
    return app
