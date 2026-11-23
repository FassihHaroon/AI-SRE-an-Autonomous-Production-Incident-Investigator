"""Instrumented asyncpg connection pool.

* Pool gauges: size, in use, idle, pending acquires; acquire latency; acquire timeouts.
* Per-query latency histogram and error counter, labelled by a stable query *name*.
* Fault hooks: ``leak_one()`` checks a connection out, opens a transaction and never returns
  it (``idle in transaction`` in ``pg_stat_activity``); ``plan_regression`` disables index
  scans for selected queries so they genuinely run as sequential scans.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import asynccontextmanager
from typing import Any

import asyncpg

from ai_sre_simulator.common import metrics
from ai_sre_simulator.common.logging import log

logger = logging.getLogger("db")


class PoolTimeoutError(Exception):
    """No connection became available within the acquire timeout."""


class DatabaseUnavailableError(Exception):
    """The database could not be reached or the query failed at the connection level."""


_CONNECTION_ERRORS: tuple[type[BaseException], ...] = (
    OSError,
    asyncpg.exceptions.ConnectionDoesNotExistError,
    asyncpg.exceptions.InterfaceError,
    asyncpg.exceptions.CannotConnectNowError,
    asyncpg.exceptions.TooManyConnectionsError,
)


class InstrumentedPool:
    def __init__(
        self,
        dsn: str,
        *,
        max_size: int,
        acquire_timeout_s: float,
        slow_queries: Callable[[], set[str]] | None = None,
        name: str = "primary",
        application_name: str | None = None,
    ) -> None:
        self._dsn = dsn
        self.max_size = max_size
        self._acquire_timeout = acquire_timeout_s
        self._slow_queries = slow_queries or (lambda: set())
        self._name = name
        self._server_settings = {"application_name": application_name} if application_name else None
        self._pool: asyncpg.Pool | None = None
        self._in_use = 0
        self._pending = 0
        self._leaked: list[tuple[asyncpg.pool.PoolConnectionProxy, Any]] = []
        metrics.DB_POOL_MAX.set(max_size)

    # ------------------------------------------------------------------ lifecycle
    async def open(self, retries: int = 30) -> None:
        for attempt in range(retries):
            try:
                self._pool = await asyncpg.create_pool(
                    self._dsn,
                    min_size=min(2, self.max_size),
                    max_size=self.max_size,
                    command_timeout=15,
                    timeout=5,
                    server_settings=self._server_settings,
                )
                self._update_gauges()
                return
            except (*_CONNECTION_ERRORS, asyncpg.PostgresError) as exc:
                if attempt == retries - 1:
                    raise
                log(
                    logger,
                    logging.WARNING,
                    "database not reachable yet, retrying",
                    attempt=attempt + 1,
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
                await asyncio.sleep(1)

    async def close(self) -> None:
        await self.release_leaked()
        if self._pool:
            await self._pool.close()

    @property
    def leaked_count(self) -> int:
        return len(self._leaked)

    # ------------------------------------------------------------------ gauges
    def _update_gauges(self) -> None:
        metrics.DB_POOL_IN_USE.set(self._in_use)
        metrics.DB_POOL_PENDING.set(self._pending)
        if self._pool is not None:
            metrics.DB_POOL_IDLE.set(self._pool.get_idle_size())

    # ------------------------------------------------------------------ acquire
    async def _acquire(self) -> asyncpg.pool.PoolConnectionProxy:
        if self._pool is None:
            raise DatabaseUnavailableError("pool is not open")
        self._pending += 1
        self._update_gauges()
        started = time.perf_counter()
        try:
            conn: asyncpg.pool.PoolConnectionProxy = await self._pool.acquire(
                timeout=self._acquire_timeout
            )
        except TimeoutError as exc:
            metrics.DB_POOL_TIMEOUTS.inc()
            raise PoolTimeoutError(
                f"timed out after {self._acquire_timeout:.1f}s waiting for a database connection "
                f"(pool size={self.max_size}, in use={self._in_use}, waiting={self._pending})"
            ) from exc
        except _CONNECTION_ERRORS as exc:
            raise DatabaseUnavailableError(f"could not connect to database: {exc}") from exc
        finally:
            self._pending -= 1
            metrics.DB_POOL_ACQUIRE.observe(time.perf_counter() - started)
        self._in_use += 1
        self._update_gauges()
        return conn

    async def _release(self, conn: asyncpg.pool.PoolConnectionProxy) -> None:
        if self._pool is None:
            return
        try:
            await self._pool.release(conn)
        finally:
            self._in_use -= 1
            self._update_gauges()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[asyncpg.pool.PoolConnectionProxy]:
        conn = await self._acquire()
        try:
            yield conn
        finally:
            await self._release(conn)

    # ------------------------------------------------------------------ queries
    async def _run(
        self,
        conn: asyncpg.pool.PoolConnectionProxy,
        name: str,
        method: str,
        sql: str,
        args: Iterable[Any],
    ) -> Any:
        slow = self._slow_queries()
        regressed = "*" in slow or name in slow
        started = time.perf_counter()
        try:
            if regressed and not conn.is_in_transaction():
                async with conn.transaction():
                    await conn.execute(
                        "SET LOCAL enable_indexscan = off; SET LOCAL enable_bitmapscan = off; "
                        "SET LOCAL enable_indexonlyscan = off"
                    )
                    return await getattr(conn, method)(sql, *args)
            return await getattr(conn, method)(sql, *args)
        except _CONNECTION_ERRORS as exc:
            metrics.DB_QUERY_ERRORS.labels(name, type(exc).__name__).inc()
            raise DatabaseUnavailableError(
                f"database connection failed during {name}: {exc}"
            ) from exc
        except asyncpg.PostgresError as exc:
            metrics.DB_QUERY_ERRORS.labels(name, type(exc).__name__).inc()
            raise
        finally:
            metrics.DB_QUERY_DURATION.labels(name).observe(time.perf_counter() - started)

    async def fetch(
        self, conn: asyncpg.pool.PoolConnectionProxy, name: str, sql: str, *args: Any
    ) -> list[asyncpg.Record]:
        result: list[asyncpg.Record] = await self._run(conn, name, "fetch", sql, args)
        return result

    async def fetchrow(
        self, conn: asyncpg.pool.PoolConnectionProxy, name: str, sql: str, *args: Any
    ) -> asyncpg.Record | None:
        result: asyncpg.Record | None = await self._run(conn, name, "fetchrow", sql, args)
        return result

    async def fetchval(
        self, conn: asyncpg.pool.PoolConnectionProxy, name: str, sql: str, *args: Any
    ) -> Any:
        return await self._run(conn, name, "fetchval", sql, args)

    async def execute(
        self, conn: asyncpg.pool.PoolConnectionProxy, name: str, sql: str, *args: Any
    ) -> str:
        result: str = await self._run(conn, name, "execute", sql, args)
        return result

    # ------------------------------------------------------------------ fault hooks
    async def leak_one(self, statement: str, *args: Any) -> None:
        """Check out a connection, start a transaction, run ``statement`` and never return it."""
        conn = await self._acquire()
        tx = conn.transaction()
        try:
            await tx.start()
            await conn.execute(statement, *args)
        except Exception:
            await self._release(conn)
            raise
        self._leaked.append((conn, tx))

    async def release_leaked(self) -> int:
        released = 0
        while self._leaked:
            conn, tx = self._leaked.pop()
            with contextlib.suppress(Exception):  # best effort; the pool resets it anyway
                await tx.rollback()
            await self._release(conn)
            released += 1
        return released
