# Scenario B: Redis becomes unreachable

> **Simulated.** Category `cache_failure` · seed 6379 · definition
> [simulator/scenarios/redis-outage.yaml](../../simulator/scenarios/redis-outage.yaml)

## What happens

| t | Event |
|---|-------|
| 0 s | The network path to Redis (`shop-cache:6379`) becomes a black hole: Toxiproxy accepts connections but passes no data. Every Redis command waits for the 0.5 s client timeout. |

Effects by endpoint:

| Endpoint | Behaviour |
|----------|-----------|
| `GET /api/products`, `/api/products/{id}` | Cache lookup times out → **falls back to PostgreSQL**. Still 200, but ~0.5–1 s slower. |
| `GET /api/cart/{id}`, `POST /api/cart/{id}/items` | Carts live only in Redis → **503 `cache_unavailable`**. |
| `POST /api/orders` | Order row written and **payment taken**, then queuing the fulfilment job fails → orders-service 503 `queue_unavailable` → gateway **502**. |
| fulfilment-worker | Cannot `BRPOP` the queue; logs an error every second. |

## Trigger

```bash
uv run simctl trigger redis-outage
```

## Expected signals

| Where | Signal |
|-------|--------|
| Prometheus | `redis_up == 0` (redis-exporter, same network path as the services); alert `RedisDown` |
| Prometheus | `redis_command_errors_total{error="TimeoutError"}` on api-gateway, orders-service, fulfilment-worker; `redis_command_duration_seconds` p99 ≈ 0.5 s |
| Prometheus | `cache_requests_total{result="error"}`; catalog latency up while staying 200 |
| Prometheus | 503 on `/api/cart/{customer_id}`; 502 on `/api/orders`; `orders_created_total{result="enqueue_failed"}` |
| Loki | api-gateway WARNING `catalog cache unavailable, reading from database` (error type `CacheUnavailableError`, `Timeout reading from shop-cache:6379`) |
| Loki | orders-service ERROR `order N was paid but its fulfilment job could not be queued: …`; worker ERROR `cannot read from fulfilment queue` |
| Deployer | nothing in the window |

## Root cause

- **Category:** `cache_failure` (dependency). **Component:** `redis` (`shop-cache`). **Trigger:**
  none (no change); infrastructure failure.
- Partial impact is the clue: reads degrade gracefully, while everything that *requires* Redis
  (carts, queueing) fails.
- **Business impact worth flagging:** customers are charged for orders that never reach
  fulfilment (`enqueue_failed`). They need reconciliation after recovery.

## Clearing

`uv run simctl clear` removes the Toxiproxy toxic. Connections recover on the next command; errors
stop within seconds and `redis_up` returns to 1 on the next scrape. Orders paid during the outage
stay in status `paid` (not queued). That is realistic, and a follow-up item for the report.
