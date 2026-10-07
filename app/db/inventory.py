from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
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
    section: str = "General"
    shelf: str = ""
    last_audited_at: datetime | None = None


@dataclass
class ShelfAuditRecord:
    id: str
    tenant_id: int
    section: str
    shelf: str
    audited_by: str
    total_expected: int
    total_scanned: int
    verified_count: int
    missing_count: int
    misplaced_count: int
    discrepancies: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class InventoryStore(Protocol):
    def add(self, item: InventoryItem, tenant_id: int = 1) -> None: ...
    def get(self, sku: str, tenant_id: int = 1) -> InventoryItem | None: ...
    def list_all(self, tenant_id: int = 1) -> list[InventoryItem]: ...
    def list_by_location(self, section: str, shelf: str, tenant_id: int = 1) -> list[InventoryItem]: ...
    def update_prices(self, sku: str, in_store: Decimal, online: Decimal, tenant_id: int = 1) -> None: ...
    def update_location(self, sku: str, section: str, shelf: str, tenant_id: int = 1) -> None: ...
    def mark_audited(self, sku: str, audited_at: datetime | None = None, tenant_id: int = 1) -> None: ...
    def delist(self, sku: str, tenant_id: int = 1) -> None: ...
    def delete(self, sku: str, tenant_id: int = 1) -> bool: ...
    def record_shelf_audit(self, audit: ShelfAuditRecord) -> None: ...
    def list_shelf_audits(self, tenant_id: int = 1, limit: int = 50) -> list[ShelfAuditRecord]: ...


def _parse_dt(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val)
        except Exception:
            pass
    return datetime.now(UTC)


def _row_to_item(row: dict[str, Any]) -> InventoryItem:
    audited_val = row.get("last_audited_at")
    return InventoryItem(
        sku=row["sku"],
        title=row["title"],
        authors=row["authors"] if isinstance(row["authors"], list) else json.loads(row["authors"]),
        format=row["format"],
        condition=row["condition"],
        in_store_price_cad=Decimal(str(row["in_store_price_cad"])),
        online_price_cad=Decimal(str(row["online_price_cad"])),
        status=row["status"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
        section=str(row.get("section") or "General"),
        shelf=str(row.get("shelf") or ""),
        last_audited_at=_parse_dt(audited_val) if audited_val else None,
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
                "INSERT INTO inventory (sku, tenant_id, title, authors, format, condition, in_store_price_cad, online_price_cad, status, created_at, updated_at, section, shelf, last_audited_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
                    item.section,
                    item.shelf,
                    item.last_audited_at.astimezone(UTC) if item.last_audited_at else None,
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

    def list_by_location(self, section: str, shelf: str, tenant_id: int = 1) -> list[InventoryItem]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory WHERE tenant_id = %s AND section = %s AND shelf = %s AND status = 'active' ORDER BY title ASC",
                (tenant_id, section, shelf),
            ).fetchall()
            return [_row_to_item(row) for row in rows]

    def update_prices(self, sku: str, in_store: Decimal, online: Decimal, tenant_id: int = 1) -> None:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE inventory SET in_store_price_cad = %s, online_price_cad = %s, updated_at = %s WHERE sku = %s AND tenant_id = %s",
                (in_store, online, datetime.now(UTC), sku, tenant_id),
            )

    def update_location(self, sku: str, section: str, shelf: str, tenant_id: int = 1) -> None:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE inventory SET section = %s, shelf = %s, updated_at = %s WHERE sku = %s AND tenant_id = %s",
                (section, shelf, datetime.now(UTC), sku, tenant_id),
            )

    def mark_audited(self, sku: str, audited_at: datetime | None = None, tenant_id: int = 1) -> None:
        ts = audited_at or datetime.now(UTC)
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE inventory SET last_audited_at = %s, updated_at = %s WHERE sku = %s AND tenant_id = %s",
                (ts, datetime.now(UTC), sku, tenant_id),
            )

    def delist(self, sku: str, tenant_id: int = 1) -> None:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE inventory SET status = 'delisted', updated_at = %s WHERE sku = %s AND tenant_id = %s",
                (datetime.now(UTC), sku, tenant_id),
            )

    def delete(self, sku: str, tenant_id: int = 1) -> bool:
        with self.pool.connection() as conn, conn.transaction():
            res = conn.execute(
                "DELETE FROM inventory WHERE sku = %s AND tenant_id = %s",
                (sku, tenant_id),
            )
            return bool(res.rowcount and res.rowcount > 0)

    def record_shelf_audit(self, audit: ShelfAuditRecord) -> None:
        from psycopg.types.json import Jsonb

        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO shelf_audits (id, tenant_id, section, shelf, audited_by, total_expected, total_scanned, verified_count, missing_count, misplaced_count, discrepancies, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    audit.id,
                    audit.tenant_id,
                    audit.section,
                    audit.shelf,
                    audit.audited_by,
                    audit.total_expected,
                    audit.total_scanned,
                    audit.verified_count,
                    audit.missing_count,
                    audit.misplaced_count,
                    Jsonb(audit.discrepancies),
                    audit.created_at.astimezone(UTC),
                ),
            )

    def list_shelf_audits(self, tenant_id: int = 1, limit: int = 50) -> list[ShelfAuditRecord]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM shelf_audits WHERE tenant_id = %s ORDER BY created_at DESC LIMIT %s",
                (tenant_id, limit),
            ).fetchall()
            return [
                ShelfAuditRecord(
                    id=row["id"],
                    tenant_id=row["tenant_id"],
                    section=row["section"],
                    shelf=row["shelf"],
                    audited_by=row["audited_by"],
                    total_expected=row["total_expected"],
                    total_scanned=row["total_scanned"],
                    verified_count=row["verified_count"],
                    missing_count=row["missing_count"],
                    misplaced_count=row["misplaced_count"],
                    discrepancies=row["discrepancies"] if isinstance(row["discrepancies"], list) else json.loads(row["discrepancies"]),
                    created_at=_parse_dt(row["created_at"]),
                )
                for row in rows
            ]

    def close(self) -> None:
        self.pool.close()


class SQLiteInventoryStore:
    def __init__(self, path: str) -> None:
        import sqlite3
        import threading

        self.path = path
        self._lock = threading.Lock()
        self._mem_conn = None
        if self.path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
            self._mem_conn.row_factory = sqlite3.Row

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
                    updated_at TEXT NOT NULL,
                    section TEXT NOT NULL DEFAULT 'General',
                    shelf TEXT NOT NULL DEFAULT '',
                    last_audited_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shelf_audits (
                    id TEXT PRIMARY KEY,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    section TEXT NOT NULL,
                    shelf TEXT NOT NULL,
                    audited_by TEXT NOT NULL,
                    total_expected INTEGER NOT NULL DEFAULT 0,
                    total_scanned INTEGER NOT NULL DEFAULT 0,
                    verified_count INTEGER NOT NULL DEFAULT 0,
                    missing_count INTEGER NOT NULL DEFAULT 0,
                    misplaced_count INTEGER NOT NULL DEFAULT 0,
                    discrepancies TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """
            )
            for col, col_def in [
                ("tenant_id", "INTEGER NOT NULL DEFAULT 1"),
                ("section", "TEXT NOT NULL DEFAULT 'General'"),
                ("shelf", "TEXT NOT NULL DEFAULT ''"),
                ("last_audited_at", "TEXT"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE inventory ADD COLUMN {col} {col_def}")
                except sqlite3.OperationalError:
                    pass

    @contextmanager
    def _transaction(self):
        import sqlite3

        if self._mem_conn is not None:
            with self._lock:
                self._mem_conn.execute("BEGIN IMMEDIATE")
                try:
                    yield self._mem_conn
                    self._mem_conn.execute("COMMIT")
                except Exception:
                    self._mem_conn.execute("ROLLBACK")
                    raise
        else:
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
                "INSERT INTO inventory (sku, tenant_id, title, authors, format, condition, in_store_price_cad, online_price_cad, status, created_at, updated_at, section, shelf, last_audited_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    item.section,
                    item.shelf,
                    item.last_audited_at.isoformat() if item.last_audited_at else None,
                ),
            )

    def get(self, sku: str, tenant_id: int = 1) -> InventoryItem | None:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM inventory WHERE sku = ? AND tenant_id = ?",
                (sku, tenant_id),
            ).fetchone()
            return _row_to_item(dict(row)) if row else None

    def list_all(self, tenant_id: int = 1) -> list[InventoryItem]:
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory WHERE tenant_id = ? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
            return [_row_to_item(dict(row)) for row in rows]

    def list_by_location(self, section: str, shelf: str, tenant_id: int = 1) -> list[InventoryItem]:
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory WHERE tenant_id = ? AND section = ? AND shelf = ? AND status = 'active' ORDER BY title ASC",
                (tenant_id, section, shelf),
            ).fetchall()
            return [_row_to_item(dict(row)) for row in rows]

    def update_prices(self, sku: str, in_store: Decimal, online: Decimal, tenant_id: int = 1) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE inventory SET in_store_price_cad = ?, online_price_cad = ?, updated_at = ? WHERE sku = ? AND tenant_id = ?",
                (str(in_store), str(online), datetime.now(UTC).isoformat(), sku, tenant_id),
            )

    def update_location(self, sku: str, section: str, shelf: str, tenant_id: int = 1) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE inventory SET section = ?, shelf = ?, updated_at = ? WHERE sku = ? AND tenant_id = ?",
                (section, shelf, datetime.now(UTC).isoformat(), sku, tenant_id),
            )

    def mark_audited(self, sku: str, audited_at: datetime | None = None, tenant_id: int = 1) -> None:
        ts = (audited_at or datetime.now(UTC)).isoformat()
        with self._transaction() as conn:
            conn.execute(
                "UPDATE inventory SET last_audited_at = ?, updated_at = ? WHERE sku = ? AND tenant_id = ?",
                (ts, datetime.now(UTC).isoformat(), sku, tenant_id),
            )

    def delist(self, sku: str, tenant_id: int = 1) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE inventory SET status = 'delisted', updated_at = ? WHERE sku = ? AND tenant_id = ?",
                (datetime.now(UTC).isoformat(), sku, tenant_id),
            )

    def delete(self, sku: str, tenant_id: int = 1) -> bool:
        with self._transaction() as conn:
            res = conn.execute(
                "DELETE FROM inventory WHERE sku = ? AND tenant_id = ?",
                (sku, tenant_id),
            )
            return bool(res.rowcount and res.rowcount > 0)

    def record_shelf_audit(self, audit: ShelfAuditRecord) -> None:
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO shelf_audits (id, tenant_id, section, shelf, audited_by, total_expected, total_scanned, verified_count, missing_count, misplaced_count, discrepancies, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    audit.id,
                    audit.tenant_id,
                    audit.section,
                    audit.shelf,
                    audit.audited_by,
                    audit.total_expected,
                    audit.total_scanned,
                    audit.verified_count,
                    audit.missing_count,
                    audit.misplaced_count,
                    json.dumps(audit.discrepancies),
                    audit.created_at.isoformat(),
                ),
            )

    def list_shelf_audits(self, tenant_id: int = 1, limit: int = 50) -> list[ShelfAuditRecord]:
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM shelf_audits WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
            return [
                ShelfAuditRecord(
                    id=row["id"],
                    tenant_id=row["tenant_id"],
                    section=row["section"],
                    shelf=row["shelf"],
                    audited_by=row["audited_by"],
                    total_expected=row["total_expected"],
                    total_scanned=row["total_scanned"],
                    verified_count=row["verified_count"],
                    missing_count=row["missing_count"],
                    misplaced_count=row["misplaced_count"],
                    discrepancies=json.loads(row["discrepancies"]) if isinstance(row["discrepancies"], str) else row["discrepancies"],
                    created_at=_parse_dt(row["created_at"]),
                )
                for row in rows
            ]

    def close(self) -> None:
        if self._mem_conn is not None:
            with self._lock:
                self._mem_conn.close()
                self._mem_conn = None
