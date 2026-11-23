"""Environment-driven settings shared by every simulated component (prefix ``SIM_``)."""

from __future__ import annotations

import os
from dataclasses import dataclass

from ai_sre_simulator import ENVIRONMENT


def _env(name: str, default: str) -> str:
    return os.environ.get(f"SIM_{name}", default)


@dataclass(frozen=True, slots=True)
class Settings:
    service: str
    environment: str
    log_level: str
    default_version: str
    seed: int
    # Data stores. Application services reach these through the toxiproxy aliases
    # (shop-db, shop-cache) so network faults can be injected; the control plane
    # connects to the real hosts directly.
    database_url: str
    control_database_url: str
    redis_url: str
    db_pool_size: int
    db_acquire_timeout_s: float
    redis_timeout_s: float
    # Peers
    orders_url: str
    payments_url: str
    deployer_url: str
    chaos_url: str
    toxiproxy_url: str
    gateway_url: str
    # Runtime
    poll_interval_s: float
    worker_concurrency: int
    loadgen_rps: float
    port: int

    @classmethod
    def from_env(cls, service: str) -> Settings:
        return cls(
            service=service,
            environment=_env("ENVIRONMENT", ENVIRONMENT),
            log_level=_env("LOG_LEVEL", "INFO"),
            default_version=_env("DEFAULT_VERSION", "0.0.0"),
            seed=int(_env("SEED", "1337")),
            database_url=_env("DATABASE_URL", "postgresql://shop:simulated-only@shop-db:5432/shop"),
            control_database_url=_env(
                "CONTROL_DATABASE_URL", "postgresql://shop:simulated-only@postgres:5432/simcontrol"
            ),
            redis_url=_env("REDIS_URL", "redis://shop-cache:6379/0"),
            db_pool_size=int(_env("DB_POOL_SIZE", "10")),
            db_acquire_timeout_s=float(_env("DB_ACQUIRE_TIMEOUT_S", "2.0")),
            redis_timeout_s=float(_env("REDIS_TIMEOUT_S", "0.5")),
            orders_url=_env("ORDERS_URL", "http://orders-service:8000"),
            payments_url=_env("PAYMENTS_URL", "http://payments-api:8080"),
            deployer_url=_env("DEPLOYER_URL", "http://deployer:8000"),
            chaos_url=_env("CHAOS_URL", "http://chaos:8000"),
            toxiproxy_url=_env("TOXIPROXY_URL", "http://toxiproxy:8474"),
            gateway_url=_env("GATEWAY_URL", "http://api-gateway:8000"),
            poll_interval_s=float(_env("POLL_INTERVAL_S", "1.0")),
            worker_concurrency=int(_env("WORKER_CONCURRENCY", "2")),
            loadgen_rps=float(_env("LOADGEN_RPS", "10")),
            port=int(_env("PORT", "8000")),
        )
