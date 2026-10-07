from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol


@dataclass
class InventoryItem:
    sku: str
    title: str
    authors: list[str]
    format: str
    condition: str
    in_store_price_cad: Decimal
    online_price_cad: Decimal
    status: str
    created_at: datetime
    updated_at: datetime


class InventoryStore(Protocol):
    def add(self, item: InventoryItem, tenant_id: int = 1) -> None: ...
    def get(self, sku: str, tenant_id: int = 1) -> InventoryItem | None: ...
    def list_all(self, tenant_id: int = 1) -> list[InventoryItem]: ...
    def update_prices(self, sku: str, in_store: Decimal, online: Decimal, tenant_id: int = 1) -> None: ...
    def delist(self, sku: str, tenant_id: int = 1) -> None: ...


def _row_to_item(row: dict[str, Any]) -> InventoryItem:
    return InventoryItem(
        sku=row["sku"],
        title=row["title"],
        authors=row["authors"] if isinstance(row["authors"], list) else json.loads(row["authors"]),
        format=row["format"],
        condition=row["condition"],
        in_store_price_cad=Decimal(str(row["in_store_price_cad"])),
        online_price_cad=Decimal(str(row["online_price_cad"])),
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class PostgresInventoryStore:
    def __init__(self, dsn: str, *, pool_size: int = 3) -> None:
        from psycopg_pool import ConnectionPool
        from psycopg.rows import dict_row

        self.pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=pool_size,
            max_idle=60.0,
            timeout=30.0,
            kwargs={"row_factory": dict_row},
        )

    def add(self, item: InventoryItem, tenant_id: int = 1) -> None:
        from psycopg.types.json import Jsonb

        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO inventory (sku, tenant_id, title, authors, format, condition, in_store_price_cad, online_price_cad, status, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    item.sku,
                    tenant_id,
                    item.title,
                    Jsonb(item.authors),
                    item.format,
                    item.condition,
                    item.in_store_price_cad,
                    item.online_price_cad,
                    item.status,
                    item.created_at.astimezone(UTC),
                    item.updated_at.astimezone(UTC),
                ),
            )

    def get(self, sku: str, tenant_id: int = 1) -> InventoryItem | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM inventory WHERE sku = %s AND tenant_id = %s",
                (sku, tenant_id),
            ).fetchone()
            return _row_to_item(row) if row else None

    def list_all(self, tenant_id: int = 1) -> list[InventoryItem]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory WHERE tenant_id = %s ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
            return [_row_to_item(row) for row in rows]

    def update_prices(self, sku: str, in_store: Decimal, online: Decimal, tenant_id: int = 1) -> None:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE inventory SET in_store_price_cad = %s, online_price_cad = %s, updated_at = %s WHERE sku = %s AND tenant_id = %s",
                (in_store, online, datetime.now(UTC), sku, tenant_id),
            )

    def delist(self, sku: str, tenant_id: int = 1) -> None:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE inventory SET status = 'delisted', updated_at = %s WHERE sku = %s AND tenant_id = %s",
                (datetime.now(UTC), sku, tenant_id),
            )

    def close(self) -> None:
        self.pool.close()


class SQLiteInventoryStore:
    def __init__(self, path: str) -> None:
        import sqlite3
        import threading

        self.path = path
        self._lock = threading.Lock()
        with self._transaction() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inventory (
                    sku TEXT PRIMARY KEY,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    title TEXT NOT NULL,
                    authors TEXT NOT NULL,
                    format TEXT NOT NULL,
                    condition TEXT NOT NULL,
                    in_store_price_cad TEXT NOT NULL,
                    online_price_cad TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            try:
                conn.execute("ALTER TABLE inventory ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1")
            except sqlite3.OperationalError:
                pass

    @contextmanager
    def _transaction(self):
        import sqlite3

        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        with self._lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()

    def add(self, item: InventoryItem, tenant_id: int = 1) -> None:
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO inventory (sku, tenant_id, title, authors, format, condition, in_store_price_cad, online_price_cad, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.sku,
                    tenant_id,
                    item.title,
                    json.dumps(item.authors),
                    item.format,
                    item.condition,
                    str(item.in_store_price_cad),
                    str(item.online_price_cad),
                    item.status,
                    item.created_at.isoformat(),
                    item.updated_at.isoformat(),
                ),
            )

    def get(self, sku: str, tenant_id: int = 1) -> InventoryItem | None:
        import sqlite3

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        with self._lock:
            row = conn.execute(
                "SELECT * FROM inventory WHERE sku = ? AND tenant_id = ?",
                (sku, tenant_id),
            ).fetchone()
        conn.close()
        return _row_to_item(dict(row)) if row else None

    def list_all(self, tenant_id: int = 1) -> list[InventoryItem]:
        import sqlite3

        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        with self._lock:
            rows = conn.execute(
                "SELECT * FROM inventory WHERE tenant_id = ? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        conn.close()
        return [_row_to_item(dict(row)) for row in rows]

    def update_prices(self, sku: str, in_store: Decimal, online: Decimal, tenant_id: int = 1) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE inventory SET in_store_price_cad = ?, online_price_cad = ?, updated_at = ? WHERE sku = ? AND tenant_id = ?",
                (str(in_store), str(online), datetime.now(UTC).isoformat(), sku, tenant_id),
            )

    def delist(self, sku: str, tenant_id: int = 1) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE inventory SET status = 'delisted', updated_at = ? WHERE sku = ? AND tenant_id = ?",
                (datetime.now(UTC).isoformat(), sku, tenant_id),
            )
