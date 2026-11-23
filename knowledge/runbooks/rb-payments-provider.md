---
id: "rb-payments-provider"
title: "Runbook: payments provider degradation"
type: "runbook"
services: ["orders-service"]
environment: "simulated-prod"
date: "2026-02-14"
tags: ["payments", "third party", "upstream", "502"]
---

# Runbook: payments provider (PSP) degradation

## Symptoms
- `http_client_requests_total{peer="payments-provider",status=~"5.."}` rising; slower charges.
- 502 only on order placement; no internal deployment.

## Remediation
- Contact the provider; check their status page. Consider pausing checkout or failover.
- Do not roll back internal services: the cause is external.
