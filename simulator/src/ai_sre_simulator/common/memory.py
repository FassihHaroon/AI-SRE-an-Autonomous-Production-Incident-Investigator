"""Real resident-memory growth for the ``memory_pressure`` fault and leaky builds.

Chunks are filled with non-zero bytes so the pages are actually committed (RSS grows). Chunks
are larger than the allocator's mmap threshold, so releasing them returns memory to the OS.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from ai_sre_simulator.common.faults import FaultType
from ai_sre_simulator.common.runtime import Runtime

logger = logging.getLogger("memory")

_MB = 1024 * 1024


class MemoryBallast:
    """Holds memory on behalf of active ``memory_pressure`` faults."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime
        self._chunks: list[bytes] = []
        self._task: asyncio.Task[None] | None = None

    @property
    def held_mb(self) -> float:
        return sum(len(c) for c in self._chunks) / _MB

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="memory-ballast")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._chunks.clear()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            faults = self._runtime.state.faults_of(FaultType.MEMORY_PRESSURE)
            if not faults:
                if self._chunks:
                    self._chunks.clear()
                continue
            params = faults[0].params
            if self.held_mb + params["mb_per_second"] <= params["max_mb"]:
                self._chunks.append(b"\x5a" * int(params["mb_per_second"] * _MB))


class LeakyCache:
    """An unbounded in-process cache, as shipped by a buggy build. Grows per request."""

    def __init__(self) -> None:
        self._entries: dict[int, bytes] = {}
        self._next = 0

    @property
    def size_mb(self) -> float:
        return sum(len(v) for v in self._entries.values()) / _MB

    def add(self, kilobytes: int) -> None:
        self._entries[self._next] = b"\x5a" * (kilobytes * 1024)
        self._next += 1

    def clear(self) -> None:
        self._entries.clear()
