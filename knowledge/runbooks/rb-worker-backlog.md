---
id: "rb-worker-backlog"
title: "Runbook: fulfilment queue backlog"
type: "runbook"
services: ["fulfilment-worker"]
environment: "simulated-prod"
date: "2026-05-30"
tags: ["queue", "backlog", "worker", "dead-letter"]
---

# Runbook: fulfilment queue backlog

## Symptoms
- `queue_depth{queue="fulfilment"}` and `queue_oldest_job_age_seconds` rising steadily.
- `worker_job_failures_total` by reason; API healthy.

## Diagnosis
- If failures started with a fulfilment-worker deployment: roll the worker back.
- If jobs succeed but slowly and traffic grew: scale the worker (`scale_service_simulation`).

## Remediation
- Roll back the worker when a deploy caused the failures; then re-drive the dead-letter queue.
- Never clear the fulfilment queue: `clear_queue_simulation` loses customer orders (irreversible).
