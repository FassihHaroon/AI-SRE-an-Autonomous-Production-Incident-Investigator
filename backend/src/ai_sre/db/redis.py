"""Redis client factory (job queue, live event streams and caches in later phases)."""

from __future__ import annotations

from redis.asyncio import Redis


def create_redis(redis_url: str, timeout_seconds: float = 2.0) -> Redis:
    return Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
    )
