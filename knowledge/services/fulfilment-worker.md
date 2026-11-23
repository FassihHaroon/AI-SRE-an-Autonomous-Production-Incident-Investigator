---
id: "svc-fulfilment-worker"
title: "Service: fulfilment-worker"
type: "service_architecture"
services: ["fulfilment-worker"]
environment: "simulated-prod"
date: "2026-09-01"
tags: ["architecture", "dependencies"]
---

# Service: fulfilment-worker

## Responsibilities

Consumes queue:fulfilment with 2 consumers per replica, marks orders fulfilled, calls the shipping labels API. Retries failed jobs 5 times, then moves them to the dead-letter queue.

## Dependencies

PostgreSQL (shop-db), Redis (shop-cache), shipping labels API

## Owners and escalation

Team shop-fulfilment; on-call rotation in the paging tool.
