"""Open-loop, seeded traffic generator.

Requests are scheduled at a fixed rate (``SIM_LOADGEN_RPS``) whether or not earlier requests
have finished, like real users: when the system slows down, load does not back off, and in-flight
requests pile up. The *sequence* of requests (endpoint mix, customer and product ids) comes from
``random.Random(seed)`` and restarts on every environment reset (chaos epoch), so the same
scenario sees the same traffic. A ``traffic_surge`` fault multiplies the rate.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import Counter
from dataclasses import dataclass
from typing import Any

import httpx

from ai_sre_simulator.common.faults import FaultType
from ai_sre_simulator.common.logging import log
from ai_sre_simulator.common.runtime import Runtime, RuntimeState
from ai_sre_simulator.common.settings import Settings
from ai_sre_simulator.shop.seed import SEED_ORDERS

logger = logging.getLogger("loadgen")

CUSTOMERS = 2000
PRODUCTS = 500
MAX_IN_FLIGHT = 300

# (name, weight). Weights sum to 100.
MIX: tuple[tuple[str, int], ...] = (
    ("browse_products", 35),
    ("view_product", 20),
    ("view_cart", 10),
    ("add_to_cart", 5),
    ("order_history", 15),
    ("place_order", 10),
    ("view_order", 5),
)


@dataclass(frozen=True, slots=True)
class PlannedRequest:
    name: str
    method: str
    path: str
    json: dict[str, Any] | None = None


def plan_request(rng: random.Random) -> PlannedRequest:
    """Draw the next request. Pure function of the RNG state (unit-tested for determinism)."""
    name = rng.choices([m[0] for m in MIX], weights=[m[1] for m in MIX])[0]
    customer = rng.randint(1, CUSTOMERS)
    if name == "browse_products":
        return PlannedRequest(name, "GET", "/api/products")
    if name == "view_product":
        return PlannedRequest(name, "GET", f"/api/products/{rng.randint(1, PRODUCTS)}")
    if name == "view_cart":
        return PlannedRequest(name, "GET", f"/api/cart/{customer}")
    if name == "add_to_cart":
        body = {"product_id": rng.randint(1, PRODUCTS), "quantity": rng.randint(1, 3)}
        return PlannedRequest(name, "POST", f"/api/cart/{customer}/items", body)
    if name == "order_history":
        return PlannedRequest(name, "GET", f"/api/customers/{customer}/orders")
    if name == "place_order":
        items = [
            {"product_id": pid, "quantity": rng.randint(1, 3)}
            for pid in rng.sample(range(1, PRODUCTS + 1), rng.randint(1, 3))
        ]
        return PlannedRequest(
            name, "POST", "/api/orders", {"customer_id": customer, "items": items}
        )
    return PlannedRequest(name, "GET", f"/api/orders/{rng.randint(1, SEED_ORDERS)}")


class LoadGenerator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.rng = random.Random(settings.seed)  # noqa: S311 - simulation, not security
        self.runtime = Runtime(settings, track_deployments=False)
        self.runtime.on_change(self._on_change)
        self.client = httpx.AsyncClient(
            base_url=settings.gateway_url,
            timeout=15.0,
            limits=httpx.Limits(max_connections=MAX_IN_FLIGHT, max_keepalive_connections=50),
            headers={"User-Agent": "simshop-loadgen/0.2 (simulated users)"},
        )
        self.in_flight = 0
        self.stats: Counter[str] = Counter()

    async def _on_change(self, old: RuntimeState, new: RuntimeState) -> None:
        if new.epoch != old.epoch:
            self.rng = random.Random(self.settings.seed)  # noqa: S311
            log(logger, logging.INFO, "environment reset: traffic sequence restarted")

    def rate(self) -> float:
        multiplier = 1.0
        for fault in self.runtime.state.faults_of(FaultType.TRAFFIC_SURGE):
            multiplier = max(multiplier, float(fault.params["multiplier"]))
        return self.settings.loadgen_rps * multiplier

    async def _fire(self, req: PlannedRequest) -> None:
        self.in_flight += 1
        try:
            response = await self.client.request(req.method, req.path, json=req.json)
            self.stats[f"{req.name}:{response.status_code}"] += 1
        except httpx.HTTPError as exc:
            self.stats[f"{req.name}:{type(exc).__name__}"] += 1
        finally:
            self.in_flight -= 1

    async def _report(self) -> None:
        while True:
            await asyncio.sleep(30)
            log(
                logger,
                logging.INFO,
                "traffic summary (last 30s)",
                rps=self.rate(),
                in_flight=self.in_flight,
                results=dict(self.stats),
            )
            self.stats.clear()

    async def run(self) -> None:
        await self.runtime.start()
        log(
            logger,
            logging.INFO,
            "load generator started",
            rps=self.rate(),
            target=self.settings.gateway_url,
        )
        reporter = asyncio.create_task(self._report())
        tasks: set[asyncio.Task[None]] = set()
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        try:
            while True:
                next_at += 1.0 / self.rate()
                delay = next_at - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                else:
                    next_at = loop.time()  # fell behind (e.g. host paused): do not burst
                req = plan_request(self.rng)
                if self.in_flight >= MAX_IN_FLIGHT:
                    self.stats[f"{req.name}:dropped_client_saturated"] += 1
                    continue
                task = asyncio.create_task(self._fire(req))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        finally:
            reporter.cancel()
            await self.runtime.stop()
            await self.client.aclose()


def run(settings: Settings) -> None:
    asyncio.run(LoadGenerator(settings).run())
