# Scenario A: Orders deploy introduces an N+1 query

> **Simulated.** Category `bad_deploy` · seed 481 · definition
> [simulator/scenarios/orders-n-plus-one-deploy.yaml](../../simulator/scenarios/orders-n-plus-one-deploy.yaml)
> · the flagship scenario of [evaluation-plan.md](../evaluation-plan.md) § 2.2.

## What happens

| t | Event |
|---|-------|
| 0 s | `d-407` api-gateway 3.8.0 → 3.8.1 ("Checkout banner copy update; bump httpx"). Harmless: **red herring**. |
| 240 s | `d-408` orders-service 2.3.1 → 2.4.0 ("Order history: include line items for each order"). |
| ~250 s | Every `GET /customers/{id}/orders` now runs 1 + 20 queries. The per-order query `SELECT * FROM order_items WHERE order_id::text = $1` compares the column as text, so the index on `order_items.order_id` cannot be used: each call is a sequential scan of 600,000 rows. |
| ~260 s | The 8-connection orders-service pool is permanently busy, callers queue for connections, and after 2 s they get `db_pool_exhausted` (503). Every orders-backed endpoint suffers, not only order history. |

## Trigger

```bash
uv run simctl reset                                   # optional: repeatable ids and sampling
uv run simctl trigger orders-n-plus-one-deploy        # real time (culprit at +240 s)
uv run simctl trigger orders-n-plus-one-deploy --speed 20   # culprit at +12 s
```

## Expected signals

| Where | Signal |
|-------|--------|
| Deployer | `d-407` api-gateway 3.8.1, then `d-408` orders-service 2.4.0 four minutes later |
| Prometheus | `histogram_quantile(0.99, … http_server_request_duration_seconds_bucket{service="orders-service",route="/customers/{customer_id}/orders"})`: ~50 ms to many seconds (capped at 10 s) |
| Prometheus | new query name `db_query_duration_seconds{query="get_order_items_for_history"}`, p95 ≈ 0.5–1 s |
| Prometheus | `db_pool_connections_in_use{service="orders-service"}` = `db_pool_max_connections` (8); `db_pool_pending_acquires` > 0; `db_pool_acquire_timeouts_total` rising |
| Prometheus | 503 on orders-service, 502 on api-gateway for `/api/orders`, `/api/orders/{order_id}`, `/api/customers/{customer_id}/orders` |
| Prometheus | `app_build_info{service="orders-service",version="2.4.0"}`; alerts `DBPoolSaturated`, `DBPoolAcquireTimeouts`, `HighLatencyP99`, `HighErrorRate` |
| Loki | orders-service: `timed out after 2.0s waiting for a database connection (pool size=8, in use=8, waiting=N)` |
| Loki | postgres slow-query log: `duration: … ms  execute <unnamed>: SELECT * FROM order_items WHERE order_id::text = $1` |

Measured on the dev machine (run of 2026-11-23, speed 20): pool 8/8 with 12 waiters, order-history
p99 10 s, `get_order_items_for_history` p95 0.89 s, ~500 slow-query log lines/min, gateway 5xx 6%.

## Root cause

Ground truth (for evaluation; the investigator never sees this page):

- **Category:** `bad_deploy`. **Component:** `orders-service`. **Trigger:** `d-408` (2.4.0).
- **Mechanism:** N+1 query pattern combined with a type cast that defeats the `order_items(order_id)`
  index. Many slow queries hold connections for seconds, which exhausts the pool.
- **Red herring:** `d-407` (api-gateway 3.8.1) four minutes earlier; gateway errors are only
  pass-through 502s from orders-service.
- **Correct remediation:** roll back orders-service to 2.3.1. Restarting or growing the pool does
  not fix it.

## Clearing

`uv run simctl clear` records rollback deployments for both services (`orders-service 2.4.0 → 2.3.1`,
`api-gateway 3.8.1 → 3.8.0`). Within one poll interval the old query is back, the pool drains, and
latency returns to baseline in about 30 s. `uv run simctl reset` also removes the history
(back to `d-401…d-406`).
