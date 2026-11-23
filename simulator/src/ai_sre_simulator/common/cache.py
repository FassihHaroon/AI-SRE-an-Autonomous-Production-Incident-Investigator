"""Instrumented Redis client (latency histogram + error counter per command)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from ai_sre_simulator.common import metrics

T = TypeVar("T")


class CacheUnavailableError(Exception):
    """Redis could not be reached or did not answer in time."""


class InstrumentedRedis:
    def __init__(self, url: str, timeout_s: float) -> None:
        self.client: aioredis.Redis = aioredis.from_url(
            url,
            socket_timeout=timeout_s,
            socket_connect_timeout=timeout_s,
            retry_on_timeout=False,
            decode_responses=True,
            health_check_interval=0,
        )
        self._url_host = url.split("@")[-1].split("//")[-1].split("/")[0]

    async def close(self) -> None:
        await self.client.aclose()

    async def call(self, command: str, op: Callable[[aioredis.Redis], Awaitable[T]]) -> T:
        started = time.perf_counter()
        try:
            return await op(self.client)
        except (RedisError, OSError) as exc:
            metrics.REDIS_ERRORS.labels(command, type(exc).__name__).inc()
            raise CacheUnavailableError(
                f"redis {command} failed against {self._url_host}: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            metrics.REDIS_DURATION.labels(command).observe(time.perf_counter() - started)

    # Convenience wrappers ---------------------------------------------------
    async def get(self, key: str) -> str | None:
        result: Any = await self.call("GET", lambda r: r.get(key))
        return None if result is None else str(result)

    async def set(self, key: str, value: str, ex: int) -> None:
        await self.call("SET", lambda r: r.set(key, value, ex=ex))

    async def lpush(self, key: str, value: str) -> int:
        result: Any = await self.call("LPUSH", lambda r: r.lpush(key, value))
        return int(result)

    async def llen(self, key: str) -> int:
        result: Any = await self.call("LLEN", lambda r: r.llen(key))
        return int(result)
