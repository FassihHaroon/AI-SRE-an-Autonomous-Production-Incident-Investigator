---
id: "rb-db-pool-exhaustion"
title: "Runbook: database connection pool exhaustion"
type: "runbook"
services: ["orders-service", "api-gateway", "fulfilment-worker"]
environment: "simulated-prod"
date: "2026-06-20"
tags: ["postgres", "connection pool", "timeouts", "503"]
supersedes: "rb-db-pool-exhaustion-2024"
---

# Runbook: database connection pool exhaustion

## Symptoms
- `timed out after 2.0s waiting for a database connection` in service logs.
- `db_pool_connections_in_use == db_pool_max_connections`, `db_pool_pending_acquires > 0`.
- 503 `db_pool_exhausted` from the service; 502 from api-gateway for the affected routes.

## Diagnosis
1. Check whether queries are slow: `get_slow_queries`, `get_database_metrics`. Slow statements
   (sequential scans, N+1 patterns) hold connections for seconds.
2. Check for leaks: `get_database_health`. Many sessions `idle in transaction` from one
   application while query latency is normal means connections are not returned.
3. Correlate with deployments and config changes in the last hour.

## Remediation
- If a deployment or config change introduced the slow query or the leak: **roll back** the
  service (`rollback_deployment_simulation`). This is the fix.
- Restarting the service releases leaked connections only temporarily; the leak refills the pool.
- Do not increase the pool size as a first response: it moves the load to the database.

## Verification
Pool utilisation below 80%, no pending acquires, 5xx rate back to baseline within 2 minutes.
