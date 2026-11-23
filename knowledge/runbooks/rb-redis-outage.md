---
id: "rb-redis-outage"
title: "Runbook: Redis unavailable"
type: "runbook"
services: ["api-gateway", "orders-service", "fulfilment-worker"]
environment: "simulated-prod"
date: "2026-05-11"
tags: ["redis", "cache", "timeouts", "queue"]
---

# Runbook: Redis (shop-cache) unavailable

## Symptoms
- `redis_up == 0`; `redis_command_errors_total` rising on every service at the same moment.
- Catalog reads slower (database fallback) but successful; carts return 503.
- Orders fail with `queue_unavailable` after payment: customers are charged but orders are not queued.

## Remediation
- Restore connectivity to Redis; escalate to the platform team. Rolling back services does not help.
- After recovery: reconcile orders in status `paid` that were never fulfilled.

## Verification
`redis_up == 1`, no Redis command errors for 5 minutes, fulfilment queue draining.
