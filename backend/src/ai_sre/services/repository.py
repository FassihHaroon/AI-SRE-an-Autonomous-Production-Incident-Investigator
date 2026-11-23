"""Persistence for investigations, their event log and application settings.

Two implementations with the same interface: PostgreSQL (production) and in-memory (tests and a
fallback when the database is unreachable at startup).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

import asyncpg

INVESTIGATIONS_DDL = """
CREATE TABLE IF NOT EXISTS investigations (
    id          text PRIMARY KEY,
    question    text NOT NULL,
    status      text NOT NULL,
    parent_id   text,
    severity    text,
    reasoner    text,
    limits      jsonb,
    root_cause  jsonb,
    approval    jsonb,
    report      jsonb,
    error       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_investigations_created ON investigations (created_at DESC);
CREATE TABLE IF NOT EXISTS investigation_events (
    id                bigserial PRIMARY KEY,
    investigation_id  text NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
    seq               integer NOT NULL,
    type              text NOT NULL,
    payload           jsonb NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (investigation_id, seq)
);
CREATE TABLE IF NOT EXISTS app_settings (
    key         text PRIMARY KEY,
    value       jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
)
"""

_JSON_FIELDS = ("limits", "root_cause", "approval", "report")
_UPDATABLE = {"status", "severity", "root_cause", "approval", "report", "error"}


class Repository(Protocol):
    async def create(self, record: dict[str, Any]) -> None: ...
    async def update(self, inv_id: str, **fields: Any) -> None: ...
    async def get(self, inv_id: str) -> dict[str, Any] | None: ...
    async def list_recent(self, limit: int) -> list[dict[str, Any]]: ...
    async def append_event(
        self, inv_id: str, type_: str, payload: dict[str, Any]
    ) -> dict[str, Any]: ...
    async def events_after(self, inv_id: str, seq: int) -> list[dict[str, Any]]: ...
    async def get_setting(self, key: str) -> Any: ...
    async def put_setting(self, key: str, value: Any) -> None: ...
    async def close(self) -> None: ...


def _now() -> datetime:
    return datetime.now(UTC)


class MemoryRepository:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.settings: dict[str, Any] = {}

    async def create(self, record: dict[str, Any]) -> None:
        now = _now()
        self.records[record["id"]] = {
            "parent_id": None,
            "severity": None,
            "root_cause": None,
            "approval": None,
            "report": None,
            "error": None,
            **record,
            "created_at": now,
            "updated_at": now,
        }
        self.events[record["id"]] = []

    async def update(self, inv_id: str, **fields: Any) -> None:
        self.records[inv_id].update(
            {k: v for k, v in fields.items() if k in _UPDATABLE}, updated_at=_now()
        )

    async def get(self, inv_id: str) -> dict[str, Any] | None:
        rec = self.records.get(inv_id)
        return dict(rec) if rec else None

    async def list_recent(self, limit: int) -> list[dict[str, Any]]:
        return sorted(
            (dict(r) for r in self.records.values()), key=lambda r: r["created_at"], reverse=True
        )[:limit]

    async def append_event(
        self, inv_id: str, type_: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        log = self.events.setdefault(inv_id, [])
        event = {"seq": len(log) + 1, "type": type_, "payload": payload, "created_at": _now()}
        log.append(event)
        return event

    async def events_after(self, inv_id: str, seq: int) -> list[dict[str, Any]]:
        return [e for e in self.events.get(inv_id, []) if e["seq"] > seq]

    async def get_setting(self, key: str) -> Any:
        return self.settings.get(key)

    async def put_setting(self, key: str, value: Any) -> None:
        self.settings[key] = value

    async def close(self) -> None:
        return None


class PostgresRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str) -> PostgresRepository:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, timeout=5)
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT count(*) FROM investigations")  # fails fast if not migrated
        return cls(pool)

    @staticmethod
    def _row(row: asyncpg.Record | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = dict(row)
        for key in _JSON_FIELDS:
            if isinstance(out.get(key), str):
                out[key] = json.loads(out[key])
        return out

    async def create(self, record: dict[str, Any]) -> None:
        await self._pool.execute(
            "INSERT INTO investigations (id, question, status, parent_id, reasoner, limits) VALUES ($1,$2,$3,$4,$5,$6)",
            record["id"],
            record["question"],
            record["status"],
            record.get("parent_id"),
            record.get("reasoner"),
            json.dumps(record.get("limits")),
        )

    async def update(self, inv_id: str, **fields: Any) -> None:
        sets, args = [], []
        for key, value in fields.items():
            if key not in _UPDATABLE:
                continue
            args.append(json.dumps(value, default=str) if key in _JSON_FIELDS else value)
            sets.append(f"{key} = ${len(args)}")
        if not sets:
            return
        args.append(inv_id)
        await self._pool.execute(
            f"UPDATE investigations SET {', '.join(sets)}, updated_at = now() WHERE id = ${len(args)}",  # noqa: S608 - whitelisted columns
            *args,
        )

    async def get(self, inv_id: str) -> dict[str, Any] | None:
        return self._row(
            await self._pool.fetchrow("SELECT * FROM investigations WHERE id = $1", inv_id)
        )

    async def list_recent(self, limit: int) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            "SELECT * FROM investigations ORDER BY created_at DESC LIMIT $1", limit
        )
        return [r for r in (self._row(x) for x in rows) if r is not None]

    async def append_event(
        self, inv_id: str, type_: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", inv_id)
            seq = await conn.fetchval(
                "SELECT coalesce(max(seq), 0) + 1 FROM investigation_events WHERE investigation_id = $1",
                inv_id,
            )
            row = await conn.fetchrow(
                "INSERT INTO investigation_events (investigation_id, seq, type, payload) VALUES ($1,$2,$3,$4) "
                "RETURNING seq, type, payload, created_at",
                inv_id,
                seq,
                type_,
                json.dumps(payload, default=str),
            )
        if row is None:
            raise RuntimeError("event insert returned no row")
        return {
            "seq": row["seq"],
            "type": row["type"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
        }

    async def events_after(self, inv_id: str, seq: int) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            "SELECT seq, type, payload, created_at FROM investigation_events WHERE investigation_id = $1 AND seq > $2 "
            "ORDER BY seq",
            inv_id,
            seq,
        )
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    async def get_setting(self, key: str) -> Any:
        value = await self._pool.fetchval("SELECT value FROM app_settings WHERE key = $1", key)
        return json.loads(value) if isinstance(value, str) else value

    async def put_setting(self, key: str, value: Any) -> None:
        await self._pool.execute(
            "INSERT INTO app_settings (key, value) VALUES ($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()",
            key,
            json.dumps(value),
        )

    async def close(self) -> None:
        await self._pool.close()
