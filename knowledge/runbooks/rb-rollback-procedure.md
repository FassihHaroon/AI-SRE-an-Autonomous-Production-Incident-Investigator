---
id: "rb-rollback-procedure"
title: "Procedure: rolling back a deployment"
type: "procedure"
services: ["api-gateway", "orders-service", "fulfilment-worker"]
environment: "simulated-prod"
date: "2026-04-02"
tags: ["rollback", "deployment", "remediation"]
---

# Procedure: rolling back a deployment

## When to roll back
Roll back when a deployment or config change correlates with the onset of symptoms and the
mechanism is plausible (new slow query, leak, failing jobs, memory growth, failing canary).

## Steps
1. Identify the deployment id (`list_recent_deployments`) and confirm the previous version.
2. Request the rollback (`rollback_deployment_simulation`); a human must approve it.
3. Watch error rate and p99 latency of the service for 2 minutes.

## Risk
Medium. Rollbacks are reversible (roll forward again). Database migrations that are not
backward compatible make a rollback unsafe: check `changes_summary` first.
