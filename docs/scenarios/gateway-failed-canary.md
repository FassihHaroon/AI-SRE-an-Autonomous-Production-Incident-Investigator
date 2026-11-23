# Scenario F: api-gateway rollout fails canary analysis

> **Simulated.** Category `failed_deploy` · seed 390 · definition
> [simulator/scenarios/gateway-failed-canary.yaml](../../simulator/scenarios/gateway-failed-canary.yaml)

## What happens

| t | Event |
|---|-------|
| 0 s | `d-407` api-gateway 3.8.0 → 3.9.0 starts ("Make orders-service timeout configurable"). Canary replicas of 3.9.0 immediately take **25%** of gateway traffic. They reject their own configuration and fail every request with 500. |
| 20 s | Canary analysis marks `d-407` **failed** (`canary analysis failed: canary error rate above 5% threshold; rollout halted with canary replicas still serving traffic`). The canaries are not scaled down. |

The running stable version stays 3.8.0; only the canary share fails.

## Trigger

```bash
uv run simctl trigger gateway-failed-canary
```

## Expected signals

| Where | Signal |
|-------|--------|
| Deployer | `d-407` api-gateway 3.9.0: `in_progress`, then `failed` with the canary failure reason |
| Prometheus | ~25% 500s on api-gateway, spread across **every** `/api/*` route; alert `HighErrorRate` |
| Prometheus | orders-service, PostgreSQL and Redis healthy; `app_build_info` still 3.8.0 |
| Loki | api-gateway ERROR lines with `"version": "3.9.0"` and `error.type = ConfigError`: `invalid value for ORDERS_SERVICE_TIMEOUT_MS: '2s' (expected an integer number of milliseconds)`; successful requests log `"version": "3.8.0"` |
| HTTP | failing responses carry `X-Served-Version: 3.9.0` |

## Root cause

- **Category:** `failed_deploy`. **Component:** `api-gateway`. **Trigger:** `d-407` (3.9.0 canary).
- **Clue:** errors are uniform across routes and only on the new version (log `version` field),
  while all dependencies are healthy.
- **Correct remediation:** abort the rollout / scale canaries to zero (a rollback to 3.8.0).

## Clearing

`uv run simctl clear` records `d-408` (`rollback`, "Abort rollout of 3.9.0; scale canary replicas to
zero"), and the 500s stop on the next poll.

The same mechanism is available as a single fault: `simctl inject failed_deployment --target
api-gateway --param version=3.9.1 --param canary_fraction=0.5`.
