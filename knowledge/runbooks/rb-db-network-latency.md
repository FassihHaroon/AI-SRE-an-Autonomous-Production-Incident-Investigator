---
id: "rb-db-network-latency"
title: "Runbook: network latency to the database"
type: "runbook"
services: ["api-gateway", "orders-service", "fulfilment-worker"]
environment: "simulated-prod"
date: "2026-03-19"
tags: ["latency", "network", "postgres"]
---

# Runbook: network latency to the database

## Symptoms
- Every query on every service slower by a similar amount; database itself idle.

## Remediation
- Escalate to the network/infrastructure team. Service rollbacks do not help.
