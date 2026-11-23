---
id: "svc-api-gateway"
title: "Service: api-gateway"
type: "service_architecture"
services: ["api-gateway"]
environment: "simulated-prod"
date: "2026-09-01"
tags: ["architecture", "dependencies"]
---

# Service: api-gateway

## Responsibilities

Public shop API. Owns the product catalog (PostgreSQL with Redis cache-aside) and carts (Redis only). Proxies order operations to orders-service with a 5 s timeout. Pool of 10 DB connections. Container memory limit 320 MiB.

## Dependencies

orders-service, PostgreSQL (shop-db), Redis (shop-cache)

## Owners and escalation

Team shop-api; on-call rotation in the paging tool.
