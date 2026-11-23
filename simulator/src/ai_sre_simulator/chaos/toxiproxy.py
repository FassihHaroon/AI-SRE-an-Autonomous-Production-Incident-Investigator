"""Minimal Toxiproxy HTTP API client (https://github.com/Shopify/toxiproxy)."""

from __future__ import annotations

from typing import Any

import httpx

# Proxy name -> what it fronts. Defined in deploy/sim/toxiproxy/toxiproxy.json.
PROXY_FOR_TARGET = {"shop-db": "postgres", "shop-cache": "redis", "payments-provider": "payments"}


class Toxiproxy:
    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=5.0)

    async def close(self) -> None:
        await self._client.aclose()

    async def set_enabled(self, proxy: str, enabled: bool) -> None:
        r = await self._client.post(f"/proxies/{proxy}", json={"enabled": enabled})
        r.raise_for_status()

    async def add_toxic(
        self,
        proxy: str,
        name: str,
        toxic_type: str,
        attributes: dict[str, Any],
        stream: str = "downstream",
    ) -> None:
        r = await self._client.post(
            f"/proxies/{proxy}/toxics",
            json={
                "name": name,
                "type": toxic_type,
                "stream": stream,
                "toxicity": 1.0,
                "attributes": attributes,
            },
        )
        if r.status_code == 409:  # already present (re-apply after restart)
            return
        r.raise_for_status()

    async def remove_toxic(self, proxy: str, name: str) -> None:
        r = await self._client.delete(f"/proxies/{proxy}/toxics/{name}")
        if r.status_code != 404:
            r.raise_for_status()

    async def reset(self) -> None:
        """Enable every proxy and remove every toxic."""
        r = await self._client.post("/reset")
        r.raise_for_status()

    async def proxies(self) -> dict[str, Any]:
        r = await self._client.get("/proxies")
        r.raise_for_status()
        result: dict[str, Any] = r.json()
        return result
