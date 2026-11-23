---
id: "rb-db-pool-exhaustion-2024"
title: "Runbook (2024, superseded): database connection pool exhaustion"
type: "runbook"
services: ["orders-service"]
environment: "simulated-prod"
date: "2024-03-02"
tags: ["postgres", "connection pool", "restart"]
---

# Runbook (2024): database connection pool exhaustion

> Superseded by rb-db-pool-exhaustion (2026). Kept for history.

## Remediation
- Restart orders-service. This clears the pool and usually resolves the incident.
- If it recurs, double the pool size (`DB_POOL_SIZE`).
