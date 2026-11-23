"""Entrypoint: ``python -m ai_sre_simulator <component>``.

Components: api-gateway, orders-service, fulfilment-worker, payments-provider, deployer, chaos,
loadgen, seed.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable

import uvicorn
from fastapi import FastAPI

from ai_sre_simulator.common.logging import configure_logging
from ai_sre_simulator.common.settings import Settings

COMPONENTS = (
    "api-gateway",
    "orders-service",
    "fulfilment-worker",
    "payments-provider",
    "deployer",
    "chaos",
    "loadgen",
    "seed",
)


def _serve(factory: Callable[[Settings], FastAPI], settings: Settings) -> None:
    uvicorn.run(
        factory(settings),
        host="0.0.0.0",  # noqa: S104 - container port; compose binds host ports to 127.0.0.1
        port=settings.port,
        log_config=None,
        access_log=False,
        timeout_keep_alive=30,
    )


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in COMPONENTS:
        raise SystemExit(f"usage: python -m ai_sre_simulator {{{'|'.join(COMPONENTS)}}}")
    component = args[0]
    settings = Settings.from_env(component)
    configure_logging(component, settings.environment, settings.log_level, settings.default_version)

    if component == "api-gateway":
        from ai_sre_simulator.shop import api_gateway

        _serve(api_gateway.create_app, settings)
    elif component == "orders-service":
        from ai_sre_simulator.shop import orders_service

        _serve(orders_service.create_app, settings)
    elif component == "payments-provider":
        from ai_sre_simulator.shop import payments_provider

        _serve(payments_provider.create_app, settings)
    elif component == "deployer":
        from ai_sre_simulator.deployer import app as deployer_app

        _serve(deployer_app.create_app, settings)
    elif component == "chaos":
        from ai_sre_simulator.chaos import app as chaos_app

        _serve(chaos_app.create_app, settings)
    elif component == "fulfilment-worker":
        from ai_sre_simulator.shop import worker

        asyncio.run(worker.Worker(settings).run())
    elif component == "loadgen":
        from ai_sre_simulator.loadgen import main as loadgen

        loadgen.run(settings)
    else:
        from ai_sre_simulator.shop import seed

        seed.run(settings)


if __name__ == "__main__":
    main()
