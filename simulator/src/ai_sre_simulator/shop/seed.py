"""Create the shop schema and load deterministic seed data (idempotent).

Data is generated in SQL from ``generate_series`` with fixed arithmetic (no ``random()``), so
every environment starts with identical rows: 2,000 customers, 500 products, 120,000 historical
orders and 600,000 order items. The volume is deliberate. It makes a lost index visible as a
real sequential scan.
"""

from __future__ import annotations

import asyncio
import logging
import time

import asyncpg

from ai_sre_simulator.common.logging import log
from ai_sre_simulator.common.settings import Settings

logger = logging.getLogger("seed")

SEED_ORDERS = 120_000
ITEMS_PER_ORDER = 5

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

CREATE TABLE IF NOT EXISTS customers (
    id          integer PRIMARY KEY,
    name        text NOT NULL,
    email       text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS products (
    id           integer PRIMARY KEY,
    sku          text NOT NULL UNIQUE,
    name         text NOT NULL,
    price_cents  integer NOT NULL,
    stock        integer NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id           bigserial PRIMARY KEY,
    customer_id  integer NOT NULL,
    status       text NOT NULL,
    total_cents  integer NOT NULL,
    payment_ref  text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS order_items (
    id           bigserial PRIMARY KEY,
    order_id     bigint NOT NULL,
    product_id   integer NOT NULL,
    quantity     integer NOT NULL,
    price_cents  integer NOT NULL
);

CREATE TABLE IF NOT EXISTS order_audit (
    id          bigserial PRIMARY KEY,
    order_id    bigint NOT NULL,
    action      text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);
"""

DATA = f"""
INSERT INTO customers (id, name, email)
SELECT g, 'Customer ' || g, 'customer' || g || '@example.test'
FROM generate_series(1, 2000) AS g;

INSERT INTO products (id, sku, name, price_cents, stock)
SELECT g, 'SKU-' || lpad(g::text, 5, '0'), 'Product ' || g, 500 + (g * 7919) % 19500,
       100 + (g * 31) % 900
FROM generate_series(1, 500) AS g;

-- Line items are a pure function of (order, line): product 1 + ((o*5+k)*7907) % 500,
-- quantity 1 + (o+k) % 3. Bulk-loaded before secondary indexes and foreign keys exist.
INSERT INTO order_items (order_id, product_id, quantity, price_cents)
SELECT o, p.id, 1 + (o + k) % 3, p.price_cents
FROM generate_series(1, {SEED_ORDERS}) AS o
CROSS JOIN generate_series(1, {ITEMS_PER_ORDER}) AS k
JOIN products p ON p.id = 1 + ((o::bigint * 5 + k) * 7907) % 500;

INSERT INTO orders (id, customer_id, status, total_cents, payment_ref, created_at, updated_at)
SELECT t.order_id, 1 + (t.order_id * 7919) % 2000, 'fulfilled', t.total,
       'ch_seed_' || t.order_id,
       now() - make_interval(mins => ({SEED_ORDERS} - t.order_id)::int),
       now() - make_interval(mins => ({SEED_ORDERS} - t.order_id)::int)
FROM (SELECT order_id, sum(quantity * price_cents) AS total
      FROM order_items GROUP BY order_id) AS t;

SELECT setval('orders_id_seq', {SEED_ORDERS});
SELECT setval('order_items_id_seq', (SELECT max(id) FROM order_items));
"""

# Secondary indexes and foreign keys: created after the bulk load (much faster than
# maintaining them row by row) and re-asserted on every start (idempotent).
CONSTRAINTS = """
CREATE INDEX IF NOT EXISTS orders_customer_created_idx ON orders (customer_id, created_at DESC);
CREATE INDEX IF NOT EXISTS order_items_order_id_idx ON order_items (order_id);
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'orders_customer_id_fkey') THEN
        ALTER TABLE orders ADD CONSTRAINT orders_customer_id_fkey
            FOREIGN KEY (customer_id) REFERENCES customers(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'order_items_order_id_fkey') THEN
        ALTER TABLE order_items ADD CONSTRAINT order_items_order_id_fkey
            FOREIGN KEY (order_id) REFERENCES orders(id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'order_items_product_id_fkey') THEN
        ALTER TABLE order_items ADD CONSTRAINT order_items_product_id_fkey
            FOREIGN KEY (product_id) REFERENCES products(id);
    END IF;
END $$;
"""

RESET_LIVE_DATA = f"""
DELETE FROM order_audit;
DELETE FROM order_items WHERE order_id > {SEED_ORDERS};
DELETE FROM orders WHERE id > {SEED_ORDERS};
SELECT setval('orders_id_seq', {SEED_ORDERS});
SELECT setval('order_items_id_seq', (SELECT max(id) FROM order_items));
"""


async def seed(dsn: str) -> None:
    for attempt in range(60):
        try:
            conn = await asyncpg.connect(dsn, timeout=5)
            break
        except (OSError, asyncpg.PostgresError):
            if attempt == 59:
                raise
            await asyncio.sleep(1)
    try:
        await conn.execute(SCHEMA)
        if await conn.fetchval("SELECT count(*) FROM products"):
            await conn.execute(CONSTRAINTS)
            log(logger, logging.INFO, "shop data already present, skipping seed")
            return
        started = time.perf_counter()
        async with conn.transaction():
            await conn.execute(DATA)
            await conn.execute(CONSTRAINTS)
        await conn.execute("ANALYZE")
        log(
            logger,
            logging.INFO,
            "shop data seeded",
            orders=SEED_ORDERS,
            order_items=SEED_ORDERS * ITEMS_PER_ORDER,
            duration_s=round(time.perf_counter() - started, 1),
        )
    finally:
        await conn.close()


def run(settings: Settings) -> None:
    asyncio.run(seed(settings.database_url))
