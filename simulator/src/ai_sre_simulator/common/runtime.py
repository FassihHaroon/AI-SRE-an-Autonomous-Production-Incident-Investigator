"""Runtime state of a simulated service: deployed version, behaviour flags, active faults.

A background task polls the deployer (``/internal/runtime/{service}``) and the chaos controller
(``/internal/faults/{service}``) every ``poll_interval_s``. These control-plane calls use a
plain client, so they never show up in the service's own HTTP metrics or logs. If a poll fails,
the last known state is kept.

The chaos controller also reports an ``epoch`` that changes on every environment reset. A new
epoch resets the process's seeded sampler, so a scenario triggered right after a reset makes
the same per-request decisions every time.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from ai_sre_simulator.common.determinism import sampler
from ai_sre_simulator.common.faults import FaultType, RuntimeFault
from ai_sre_simulator.common.logging import log, set_service_version
from ai_sre_simulator.common.metrics import BUILD_INFO
from ai_sre_simulator.common.settings import Settings

logger = logging.getLogger("runtime")

ChangeHook = Callable[["RuntimeState", "RuntimeState"], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RuntimeState:
    version: str
    seed: int
    flags: dict[str, Any] = field(default_factory=dict)
    faults: tuple[RuntimeFault, ...] = ()
    epoch: int = 0
    replicas: int = 1

    def flag(self, name: str, default: Any = None) -> Any:
        return self.flags.get(name, default)

    def faults_of(self, fault_type: FaultType) -> list[RuntimeFault]:
        return [f for f in self.faults if f.type == fault_type]

    def fault_ids(self) -> frozenset[str]:
        return frozenset(f.id for f in self.faults)


class Runtime:
    def __init__(self, settings: Settings, *, track_deployments: bool = True) -> None:
        self._settings = settings
        self._track_deployments = track_deployments
        self.state = RuntimeState(version=settings.default_version, seed=settings.seed)
        self._hooks: list[ChangeHook] = []
        self._task: asyncio.Task[None] | None = None
        self._client = httpx.AsyncClient(timeout=2.0)
        self._restart_seen: int | None = None
        self._set_build_info(None, self.state.version)

    @property
    def version(self) -> str:
        return self.state.version

    def on_change(self, hook: ChangeHook) -> None:
        self._hooks.append(hook)

    async def start(self) -> None:
        # Best effort: get the real deployed version before serving traffic.
        for _ in range(10):
            if await self.refresh():
                break
            await asyncio.sleep(1)
        self._task = asyncio.create_task(self._loop(), name="runtime-poller")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._client.aclose()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._settings.poll_interval_s)
            await self.refresh()

    async def refresh(self) -> bool:
        service = self._settings.service
        try:
            version, flags = self.state.version, self.state.flags
            if self._track_deployments:
                runtime = await self._client.get(
                    f"{self._settings.deployer_url}/internal/runtime/{service}"
                )
                runtime.raise_for_status()
                body = runtime.json()
                version, flags = body["version"], body.get("flags") or {}
            faults_resp = await self._client.get(
                f"{self._settings.chaos_url}/internal/faults/{service}"
            )
            faults_resp.raise_for_status()
            payload = faults_resp.json()
            epoch = int(payload["epoch"])
            faults = tuple(RuntimeFault.model_validate(f) for f in payload["faults"])
            restart_generation = int(payload.get("restart_generation", 0))
            replicas = int(payload.get("replicas", 1))
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            return False
        self._check_restart(restart_generation)
        new = RuntimeState(
            version=version,
            seed=self._settings.seed,
            flags=flags,
            faults=faults,
            epoch=epoch,
            replicas=replicas,
        )
        old = self.state
        if new != old:
            if new.epoch != old.epoch:
                sampler.reset()
            for removed in old.fault_ids() - new.fault_ids():
                sampler.forget(removed)
            self.state = new
            if new.version != old.version:
                self._set_build_info(old.version, new.version)
                log(
                    logger,
                    logging.INFO,
                    f"now running version {new.version}",
                    previous_version=old.version,
                )
            for hook in self._hooks:
                try:
                    await hook(old, new)
                except Exception:
                    logger.exception("runtime change hook failed")
        return True

    def _check_restart(self, generation: int) -> None:
        """A restart requested by the orchestrator: exit; Docker's restart policy starts a fresh
        process (memory, connections and in-process state are really gone)."""
        if self._restart_seen is None:
            self._restart_seen = generation  # first poll after start: baseline, not a request
            return
        if generation > self._restart_seen:
            self._restart_seen = generation
            log(
                logger,
                logging.WARNING,
                "restart requested by orchestrator, shutting down",
                restart_generation=generation,
            )
            for handler in logging.getLogger().handlers:
                handler.flush()
            asyncio.get_running_loop().call_later(0.3, os._exit, 0)

    def _set_build_info(self, old: str | None, new: str) -> None:
        if self._track_deployments:
            set_service_version(new)
        if old is not None:
            with contextlib.suppress(KeyError):
                BUILD_INFO.remove(old)
        BUILD_INFO.labels(version=new).set(1)
