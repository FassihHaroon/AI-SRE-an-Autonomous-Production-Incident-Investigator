# Scenario D: Upstream payments provider returns errors

> **Simulated.** Category `dependency_failure` · seed 503 · definition
> [simulator/scenarios/payments-provider-errors.yaml](../../simulator/scenarios/payments-provider-errors.yaml)

## What happens

| t | Event |
|---|-------|
| 0 s | The third-party payments provider (PSP) degrades: 40% of `POST /v1/charges` return **503**, and every call takes ~600 ms longer (~700 ms instead of ~80 ms). |

orders-service marks the order `payment_failed` and returns **502**; api-gateway passes the 502 on
for `POST /api/orders`. Browsing, carts and order lookups are unaffected.

The PSP is a third party: its own logs and metrics are **not** collected. The only evidence
is the shop's client-side telemetry.

## Trigger

```bash
uv run simctl trigger payments-provider-errors
```

## Expected signals

| Where | Signal |
|-------|--------|
| Prometheus | `http_client_requests_total{service="orders-service",peer="payments-provider",status="503"}` rising; alert `UpstreamErrors` |
| Prometheus | `http_client_request_duration_seconds{peer="payments-provider"}` p50 ~0.08 s to ~0.7 s |
| Prometheus | `orders_created_total{result="payment_failed"}`; 502 on orders-service `POST /orders` and api-gateway `POST /api/orders` only |
| Loki | orders-service ERROR `payment for order N failed: payments-provider returned HTTP 503 Service Unavailable` (`peer`, `upstream_status` fields) |
| Deployer | nothing in the window |

## Root cause

- **Category:** `dependency_failure`. **Component:** `payments-provider` (external). **Trigger:**
  none internal.
- The clue is that **errors originate in an outbound call**, start without any change, and affect
  exactly one flow.
- **Correct remediation:** none internally (do not roll back). Contact the provider, consider
  failover, retries with backoff and idempotency, and communicate customer impact.

## Clearing

`uv run simctl clear` removes the fault; the next charges succeed at normal latency.
