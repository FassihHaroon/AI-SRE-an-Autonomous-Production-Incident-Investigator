"""Clients for tests and tools that run against the RUNNING simulated environment.

Signals are read through the same interfaces an SRE uses: the Prometheus HTTP API (PromQL),
the Loki HTTP API (LogQL) and the deployer's API. Faults are driven through the chaos API.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

PROMETHEUS = os.environ.get("SIM_PROMETHEUS_API", "http://127.0.0.1:9090")
LOKI = os.environ.get("SIM_LOKI_API", "http://127.0.0.1:3100")
CHAOS = os.environ.get("SIM_CHAOS_API", "http://127.0.0.1:8210")
DEPLOYER = os.environ.get("SIM_DEPLOYER_API", "http://127.0.0.1:8202")
GATEWAY = os.environ.get("SIM_GATEWAY_API", "http://127.0.0.1:8200")

# Rates in PromQL use short windows so effects show up quickly (scrape interval is 5 s).
WINDOW = "30s"


class Prometheus:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def vector(self, expr: str) -> list[dict[str, Any]]:
        r = self.client.get(f"{PROMETHEUS}/api/v1/query", params={"query": expr})
        r.raise_for_status()
        body = r.json()
        if body["status"] != "success":
            raise RuntimeError(f"prometheus query failed: {body}")
        result: list[dict[str, Any]] = body["data"]["result"]
        return result

    def value(self, expr: str) -> float | None:
        """First sample of an instant vector, or None when the vector is empty."""
        rows = self.vector(expr)
        return float(rows[0]["value"][1]) if rows else None

    def wait_for(
        self, expr: str, check: Callable[[float], bool], timeout: float = 90, what: str = ""
    ) -> float:
        deadline = time.monotonic() + timeout
        last: float | None = None
        while time.monotonic() < deadline:
            last = self.value(expr)
            if last is not None and check(last):
                return last
            time.sleep(3)
        raise AssertionError(f"timed out after {timeout}s waiting for {what or expr}; last={last}")

    def alerts(self) -> set[str]:
        r = self.client.get(f"{PROMETHEUS}/api/v1/alerts")
        r.raise_for_status()
        return {
            a["labels"]["alertname"] for a in r.json()["data"]["alerts"] if a["state"] == "firing"
        }


class Loki:
    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def lines(self, query: str, since_s: float = 120, limit: int = 200) -> list[dict[str, Any]]:
        """Parsed JSON log lines (non-JSON lines come back as {'raw': ...}), newest first."""
        now = time.time_ns()
        r = self.client.get(
            f"{LOKI}/loki/api/v1/query_range",
            params={
                "query": query,
                "start": now - int(since_s * 1e9),
                "end": now,
                "limit": limit,
                "direction": "backward",
            },
        )
        r.raise_for_status()
        out: list[dict[str, Any]] = []
        for stream in r.json()["data"]["result"]:
            for _ts, line in stream["values"]:
                try:
                    out.append({**json.loads(line), "_labels": stream["stream"]})
                except ValueError:
                    out.append({"raw": line, "_labels": stream["stream"]})
        return out

    def wait_for_lines(
        self, query: str, min_count: int = 1, timeout: float = 60, since_s: float = 180
    ) -> list[dict[str, Any]]:
        deadline = time.monotonic() + timeout
        found: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            found = self.lines(query, since_s=since_s)
            if len(found) >= min_count:
                return found
            time.sleep(3)
        raise AssertionError(
            f"timed out waiting for {min_count} log lines matching {query}; got {len(found)}"
        )


@dataclass
class Sim:
    chaos: httpx.Client
    deployer: httpx.Client
    gateway: httpx.Client
    prom: Prometheus
    loki: Loki

    def reset(self) -> dict[str, Any]:
        r = self.chaos.post("/api/v1/reset", json={"data": False})
        r.raise_for_status()
        result: dict[str, Any] = r.json()
        return result

    def clear(self) -> None:
        self.chaos.post("/api/v1/clear").raise_for_status()

    def trigger(self, scenario: str, speed: float = 1.0) -> dict[str, Any]:
        r = self.chaos.post(f"/api/v1/scenarios/{scenario}/trigger", json={"speed": speed})
        if r.status_code != 202:
            raise AssertionError(r.text)
        result: dict[str, Any] = r.json()
        return result

    def wait_run_applied(self, run_id: str, timeout: float = 60) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run: dict[str, Any] = self.chaos.get(f"/api/v1/runs/{run_id}").json()
            if run["status"] in ("active", "failed"):
                if run["status"] != "active":
                    raise AssertionError(f"scenario steps failed: {run}")
                return run
            time.sleep(1)
        raise AssertionError(f"run {run_id} did not finish applying its steps")

    def inject(self, fault_type: str, target: str, **params: Any) -> dict[str, Any]:
        r = self.chaos.post(
            "/api/v1/faults", json={"type": fault_type, "target": target, "params": params}
        )
        if r.status_code != 201:
            raise AssertionError(r.text)
        result: dict[str, Any] = r.json()
        return result

    def remove(self, fault_id: str) -> None:
        self.chaos.delete(f"/api/v1/faults/{fault_id}").raise_for_status()

    def deployments(self, limit: int = 10) -> list[dict[str, Any]]:
        r = self.deployer.get("/api/v1/deployments", params={"limit": limit})
        r.raise_for_status()
        result: list[dict[str, Any]] = r.json()
        return result

    def wait_deployment(
        self, deployment_id: str, status: str, timeout: float = 60
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        record: dict[str, Any] = {}
        while time.monotonic() < deadline:
            r = self.deployer.get(f"/api/v1/deployments/{deployment_id}")
            if r.status_code == 200:
                record = r.json()
                if record["status"] == status:
                    return record
            time.sleep(2)
        raise AssertionError(f"{deployment_id} did not reach status {status}: {record}")

    def version(self, service: str) -> str | None:
        rows = self.prom.vector(f'app_build_info{{service="{service}"}} == 1')
        return rows[0]["metric"]["version"] if len(rows) == 1 else None

    def wait_version(self, service: str, version: str, timeout: float = 60) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.version(service) == version:
                return
            time.sleep(2)
        raise AssertionError(
            f"{service} did not report version {version} (now {self.version(service)})"
        )
