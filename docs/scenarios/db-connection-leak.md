# Scenario E: Audit-trail flag leaks DB connections

> **Simulated.** Category `config_change` · seed 77 · definition
> [simulator/scenarios/db-connection-leak.yaml](../../simulator/scenarios/db-connection-leak.yaml)

## What happens

| t | Event |
|---|-------|
| 0 s | `d-407` orders-service **config change** (version stays 2.3.1): "Enable feature flag orders.audit_trail=true". |
| ~3 s | Order lookups (`GET /orders/{id}`) now write an audit row on a second connection. For ~30% of lookups that connection is never committed or returned: it stays **idle in transaction**. |
| ~1–2 min | Leaked connections accumulate (~0.15/s at normal traffic) until all 8 pool slots are gone. From then on **every** orders-service endpoint waits 2 s for a connection and fails with 503 `db_pool_exhausted`. |

Query latency itself stays normal. The database is not slow; the pool is empty.

## Trigger

```bash
uv run simctl trigger db-connection-leak
```

## Expected signals

| Where | Signal |
|-------|--------|
| Deployer | `d-407` orders-service, `kind=config_change`, version 2.3.1 → 2.3.1 |
| Prometheus | `db_pool_connections_in_use{service="orders-service"}` climbs **stepwise** to 8 and stays there at unchanged traffic |
| Prometheus | `pg_stat_activity_count{datname="shop",state="idle in transaction"}` grows (postgres-exporter) |
| Prometheus | `db_pool_acquire_timeouts_total`; 503s on **all** orders-service routes; gateway 502s |
| Prometheus | `db_query_duration_seconds` unchanged (contrast with scenario A) |
| Loki | orders-service ERROR `timed out after 2.0s waiting for a database connection (pool size=8, in use=8, waiting=N)` |

## Root cause

- **Category:** `config_change` (resource exhaustion is the mechanism). **Component:**
  `orders-service`. **Trigger:** `d-407` (feature flag `orders.audit_trail`).
- **Distinguishing it from scenario A:** pool exhaustion *without* slow queries, with idle-in-
  transaction sessions growing, after a config change rather than a version change.
- **Correct remediation:** disable the flag / roll back the config. A restart would release the
  connections only until the leak refills the pool.

## Clearing

`uv run simctl clear` records a rollback of the config. On their next poll the services roll back
and return every leaked connection; idle-in-transaction sessions drop to 0 and the pool recovers
within seconds.
