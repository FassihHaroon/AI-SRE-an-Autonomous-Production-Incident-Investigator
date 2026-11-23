"""Fixtures for tests against the RUNNING simulated environment.

Start it first:  docker compose -f docker-compose.sim.yml up -d --build --wait
Run:             uv run poe test-sim

Every test starts from a reset environment (baseline versions, no faults, new epoch) and
clears its own faults afterwards.
"""

from __future__ import annotations

import time
from collections.abc import Iterator

import httpx
import pytest

from ai_sre_simulator.testing import (
    CHAOS,
    DEPLOYER,
    GATEWAY,
    LOKI,
    PROMETHEUS,
    Loki,
    Prometheus,
    Sim,
)


@pytest.fixture(scope="session")
def sim() -> Iterator[Sim]:
    clients = [httpx.Client(base_url=u, timeout=30) for u in (CHAOS, DEPLOYER, GATEWAY)]
    plain = httpx.Client(timeout=30)
    # Loki and Prometheus need a few seconds after a (re)start before they report ready.
    deadline = time.monotonic() + 90
    while True:
        try:
            clients[0].get("/healthz").raise_for_status()
            plain.get(f"{PROMETHEUS}/-/ready").raise_for_status()
            plain.get(f"{LOKI}/ready").raise_for_status()
            break
        except httpx.HTTPError as exc:
            if time.monotonic() > deadline:
                pytest.skip(
                    f"simulated environment not ready ({exc}); start it with "
                    "docker compose -f docker-compose.sim.yml up -d --build --wait"
                )
            time.sleep(3)
    yield Sim(clients[0], clients[1], clients[2], Prometheus(plain), Loki(plain))
    for c in [*clients, plain]:
        c.close()


@pytest.fixture
def fresh(sim: Sim) -> Iterator[Sim]:
    """Reset before the test, clear afterwards, and let the baseline settle."""
    sim.reset()
    # Let pool/latency windows from a previous test drain out of the short rate windows.
    sim.prom.wait_for(
        'max(db_pool_connections_in_use{service="orders-service"})',
        lambda v: v < 4,
        timeout=60,
        what="orders-service pool back to baseline",
    )
    yield sim
    sim.clear()
