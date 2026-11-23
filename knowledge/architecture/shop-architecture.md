---
id: "arch-shop"
title: "Shop architecture overview"
type: "architecture"
services: ["api-gateway", "orders-service", "fulfilment-worker"]
environment: "simulated-prod"
date: "2026-09-01"
tags: ["architecture", "topology"]
---

# Shop architecture overview

## Request path
Users call api-gateway. Catalog reads hit Redis first, then PostgreSQL. Order placement goes
api-gateway -> orders-service -> PostgreSQL -> payments provider -> Redis fulfilment queue ->
fulfilment-worker.

## Data stores
PostgreSQL 16 (shop-db) holds customers, products, orders and order_items (indexed on order_id).
Redis 7 (shop-cache) holds the catalog cache, carts and the fulfilment queue.

## Failure domains
A Redis outage breaks carts and order queueing but not catalog reads. A slow database saturates the
small connection pools first (orders-service has 8 connections).
