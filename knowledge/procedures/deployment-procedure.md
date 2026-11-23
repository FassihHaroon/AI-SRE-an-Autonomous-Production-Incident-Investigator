---
id: "proc-deploy"
title: "Deployment procedure"
type: "procedure"
services: ["api-gateway", "orders-service", "fulfilment-worker"]
environment: "simulated-prod"
date: "2026-08-15"
tags: ["deployment", "canary", "rollback"]
---

# Deployment procedure

## Canary
Every api-gateway release starts with canary replicas taking 25% of traffic. Canary analysis fails
the rollout when the canary error rate exceeds 5%. A failed rollout must be aborted (canaries scaled
to zero): a `failed` status alone does not remove the canaries.

## Config changes
Feature flags are deployed as `config_change` records. They can be rolled back like deployments.

## Freeze
No deployments during an active SEV1/SEV2 except rollbacks.
