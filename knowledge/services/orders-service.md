---
id: "svc-orders-service"
title: "Service: orders-service"
type: "service_architecture"
services: ["orders-service"]
environment: "simulated-prod"
date: "2026-09-01"
tags: ["architecture", "dependencies"]
---

# Service: orders-service

## Responsibilities

Places orders: writes the order in a transaction, charges the payments provider (2.5 s timeout), then queues a fulfilment job in Redis. Serves order lookups and order history. Pool of 8 DB connections, 2 s acquire timeout. Feature flag orders.audit_trail writes audit rows.

## Dependencies

PostgreSQL (shop-db), Redis (shop-cache), payments provider (third party)

## Owners and escalation

Team shop-orders; on-call rotation in the paging tool.
