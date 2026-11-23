---
id: "rb-memory-leak"
title: "Runbook: memory leak and OOM restarts"
type: "runbook"
services: ["api-gateway", "orders-service", "fulfilment-worker"]
environment: "simulated-prod"
date: "2026-07-01"
tags: ["memory", "oom", "leak", "restart"]
---

# Runbook: memory leak and OOM restarts

## Symptoms
- `process_resident_memory_bytes` grows linearly; restarts when the container limit is reached.

## Remediation
- If growth started with a deployment: roll back.
- A restart is a temporary mitigation only; memory grows again until the leak is fixed.
