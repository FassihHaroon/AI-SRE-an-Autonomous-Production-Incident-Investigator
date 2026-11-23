# Scenario H: Network latency to the database host

> **Simulated.** Category `infrastructure` · seed 5432 · definition
> [simulator/scenarios/db-network-latency.yaml](../../simulator/scenarios/db-network-latency.yaml)

## What happens

| t | Event |
|---|-------|
| 0 s | The network path to PostgreSQL (`shop-db:5432`) adds 120 ms ± 30 ms to traffic from the database. |

Every query from every service is ~120 ms slower *as seen by the client*. Endpoints slow down in
proportion to the number of queries they run: order placement (5 queries) becomes much slower,
catalog reads served from Redis barely change. Connections are held longer, so pool utilisation
rises. The database server itself is idle and healthy.

## Trigger

```bash
uv run simctl trigger db-network-latency
```

## Expected signals

| Where | Signal |
|-------|--------|
| Prometheus | `db_query_duration_seconds` p50 up by ~0.12 s for **every** query name on **every** service |
| Prometheus | `http_server_request_duration_seconds` up in proportion to queries per request (`POST /orders` the most); alert `HighLatencyP99` |
| Prometheus | `db_pool_connections_in_use` higher; error rate mostly unchanged |
| Prometheus | PostgreSQL side normal: no rise in active sessions or slow statements (the postgres slow-query log stays quiet) |
| Deployer | nothing in the window |

## Root cause

- **Category:** `infrastructure` (network). **Component:** path to `postgres`. **Trigger:** none.
- **Clue:** a uniform per-query latency offset across all services and query shapes, while the
  database reports itself healthy. That points to the network, not to query plans or a deploy.
- **Correct remediation:** escalate to the infrastructure/network owner. Rolling back services
  does nothing.

## Clearing

`uv run simctl clear` removes the latency toxic; query latency returns to baseline immediately.
