from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager, suppress
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
    batch_id: str = "batch-24"
    batch_name: str = "Autumn Accessions"
    condition_notes: str = ""
    collation: str = ""
    publisher: str = ""
    year: int = 0
    held_until: str | None = None
    hold_slip_code: str | None = None
    patron_info: str = ""
    spine_image_url: str = ""


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
    def relist(self, sku: str, tenant_id: int = 1) -> None: ...
    def delete(self, sku: str, tenant_id: int = 1) -> bool: ...
    def get_batch(self, skus: list[str], tenant_id: int = 1) -> list[InventoryItem]: ...
    def batch_delist(self, skus: list[str], tenant_id: int = 1) -> int: ...
    def batch_relist(self, skus: list[str], tenant_id: int = 1) -> int: ...
    def batch_update_location(self, skus: list[str], section: str, shelf: str, tenant_id: int = 1) -> int: ...
    def batch_delete(self, skus: list[str], tenant_id: int = 1) -> int: ...
    def batch_update_prices(self, updates: dict[str, tuple[Decimal, Decimal]], tenant_id: int = 1) -> int: ...
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
        batch_id=str(row.get("batch_id") or "batch-24"),
        batch_name=str(row.get("batch_name") or "Autumn Accessions"),
        condition_notes=str(row.get("condition_notes") or ""),
        collation=str(row.get("collation") or ""),
        publisher=str(row.get("publisher") or ""),
        year=int(row.get("year") or 0),
        held_until=row.get("held_until"),
        hold_slip_code=row.get("hold_slip_code"),
        patron_info=str(row.get("patron_info") or ""),
        spine_image_url=str(row.get("spine_image_url") or ""),
    )


class PostgresInventoryStore:
    def __init__(self, dsn: str, *, pool_size: int = 3) -> None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

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
                "INSERT INTO inventory (sku, tenant_id, title, authors, format, condition, "
                "in_store_price_cad, online_price_cad, status, created_at, updated_at, section, "
                'shelf, last_audited_at, batch_id, batch_name, condition_notes, "collation", '
                "publisher, year, held_until, hold_slip_code, patron_info, spine_image_url) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (sku) DO UPDATE SET "
                "tenant_id = EXCLUDED.tenant_id, title = EXCLUDED.title, authors = EXCLUDED.authors, "
                "format = EXCLUDED.format, condition = EXCLUDED.condition, "
                "in_store_price_cad = EXCLUDED.in_store_price_cad, online_price_cad = EXCLUDED.online_price_cad, "
                "status = EXCLUDED.status, updated_at = EXCLUDED.updated_at, "
                "section = EXCLUDED.section, shelf = EXCLUDED.shelf, last_audited_at = EXCLUDED.last_audited_at, "
                "batch_id=EXCLUDED.batch_id, batch_name=EXCLUDED.batch_name, "
                'condition_notes=EXCLUDED.condition_notes, "collation"=EXCLUDED."collation", '
                "publisher=EXCLUDED.publisher, year=EXCLUDED.year, "
                "held_until=EXCLUDED.held_until, hold_slip_code=EXCLUDED.hold_slip_code, "
                "patron_info=EXCLUDED.patron_info, spine_image_url=EXCLUDED.spine_image_url",
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
                    item.batch_id,
                    item.batch_name,
                    item.condition_notes,
                    item.collation,
                    item.publisher,
                    item.year,
                    item.held_until,
                    item.hold_slip_code,
                    item.patron_info,
                    item.spine_image_url,
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
            rows: Any = conn.execute(
                "SELECT * FROM inventory WHERE tenant_id = %s ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
            return [_row_to_item(row) for row in rows]

    def list_by_location(self, section: str, shelf: str, tenant_id: int = 1) -> list[InventoryItem]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory WHERE tenant_id = %s AND section = %s AND shelf = %s "
                "AND status = 'active' ORDER BY title ASC",
                (tenant_id, section, shelf),
            ).fetchall()
            return [_row_to_item(row) for row in rows]

    def update_prices(self, sku: str, in_store: Decimal, online: Decimal, tenant_id: int = 1) -> None:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "UPDATE inventory SET in_store_price_cad = %s, online_price_cad = %s, "
                "updated_at = %s WHERE sku = %s AND tenant_id = %s",
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

    def relist(self, sku: str, tenant_id: int = 1) -> None:
        with self.pool.connection() as conn, conn.transaction():
            from app.integrations.database import clear_for_relist

            clear_for_relist(conn, True, tenant_id, [sku])
            conn.execute(
                "UPDATE inventory SET status = 'active', updated_at = %s WHERE sku = %s AND tenant_id = %s",
                (datetime.now(UTC), sku, tenant_id),
            )

    def delete(self, sku: str, tenant_id: int = 1) -> bool:
        with self.pool.connection() as conn, conn.transaction():
            res = conn.execute(
                "DELETE FROM inventory WHERE sku = %s AND tenant_id = %s",
                (sku, tenant_id),
            )
            return bool(res.rowcount and res.rowcount > 0)

    def get_batch(self, skus: list[str], tenant_id: int = 1) -> list[InventoryItem]:
        if not skus:
            return []
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM inventory WHERE sku = ANY(%s) AND tenant_id = %s",
                (skus, tenant_id),
            ).fetchall()
            return [_row_to_item(dict(r)) for r in rows]

    def batch_delist(self, skus: list[str], tenant_id: int = 1) -> int:
        if not skus:
            return 0
        with self.pool.connection() as conn, conn.transaction():
            res = conn.execute(
                "UPDATE inventory SET status = 'delisted', updated_at = %s WHERE sku = ANY(%s) AND tenant_id = %s",
                (datetime.now(UTC), skus, tenant_id),
            )
            return res.rowcount or 0

    def batch_relist(self, skus: list[str], tenant_id: int = 1) -> int:
        if not skus:
            return 0
        with self.pool.connection() as conn, conn.transaction():
            from app.integrations.database import clear_for_relist

            clear_for_relist(conn, True, tenant_id, skus)
            res = conn.execute(
                "UPDATE inventory SET status = 'active', updated_at = %s WHERE sku = ANY(%s) AND tenant_id = %s",
                (datetime.now(UTC), skus, tenant_id),
            )
            return res.rowcount or 0

    def batch_update_location(self, skus: list[str], section: str, shelf: str, tenant_id: int = 1) -> int:
        if not skus:
            return 0
        with self.pool.connection() as conn, conn.transaction():
            res = conn.execute(
                "UPDATE inventory SET section = %s, shelf = %s, updated_at = %s WHERE sku = ANY(%s) AND tenant_id = %s",
                (section, shelf, datetime.now(UTC), skus, tenant_id),
            )
            return res.rowcount or 0

    def batch_delete(self, skus: list[str], tenant_id: int = 1) -> int:
        if not skus:
            return 0
        with self.pool.connection() as conn, conn.transaction():
            res = conn.execute(
                "DELETE FROM inventory WHERE sku = ANY(%s) AND tenant_id = %s",
                (skus, tenant_id),
            )
            return res.rowcount or 0

    def batch_update_prices(self, updates: dict[str, tuple[Decimal, Decimal]], tenant_id: int = 1) -> int:
        if not updates:
            return 0
        now = datetime.now(UTC)
        count = 0
        with self.pool.connection() as conn, conn.transaction():
            for sku, (in_store, online) in updates.items():
                res = conn.execute(
                    "UPDATE inventory SET in_store_price_cad = %s, online_price_cad = %s, "
                    "updated_at = %s WHERE sku = %s AND tenant_id = %s",
                    (in_store, online, now, sku, tenant_id),
                )
                count += res.rowcount or 0
        return count

    def record_shelf_audit(self, audit: ShelfAuditRecord) -> None:
        from psycopg.types.json import Jsonb

        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO shelf_audits (id, tenant_id, section, shelf, audited_by, total_expected, "
                "total_scanned, verified_count, missing_count, misplaced_count, discrepancies, created_at) "
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
            rows: Any = conn.execute(
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
                    discrepancies=row["discrepancies"]
                    if isinstance(row["discrepancies"], list)
                    else json.loads(row["discrepancies"]),
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
            self._mem_conn.execute("PRAGMA recursive_triggers=ON")

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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkout_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    checkout_id TEXT NOT NULL,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS checkout_reservation_items (
                    reservation_id TEXT NOT NULL,
                    sku TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    price_amount TEXT NOT NULL,
                    price_currency TEXT NOT NULL DEFAULT 'CAD',
                    PRIMARY KEY (reservation_id, sku)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS custom_site_publications (
                    sku TEXT NOT NULL,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    revision INTEGER NOT NULL DEFAULT 1,
                    publication TEXT NOT NULL,
                    availability TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    last_body_digest TEXT,
                    last_payload_json TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    last_published_at TEXT,
                    PRIMARY KEY (sku, tenant_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS channel_sync_outbox (
                    event_id TEXT PRIMARY KEY,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    order_id TEXT NOT NULL,
                    reservation_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    channel_results_json TEXT NOT NULL DEFAULT '[]',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS integration_connections (
                    key_id TEXT PRIMARY KEY,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    connection_type TEXT NOT NULL DEFAULT 'custom_site',
                    direction TEXT NOT NULL,
                    secret_key TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                )
                """
            )
            for col, col_def in [
                ("tenant_id", "INTEGER NOT NULL DEFAULT 1"),
                ("section", "TEXT NOT NULL DEFAULT 'General'"),
                ("shelf", "TEXT NOT NULL DEFAULT ''"),
                ("last_audited_at", "TEXT"),
                ("batch_id", "TEXT NOT NULL DEFAULT 'batch-24'"),
                ("batch_name", "TEXT NOT NULL DEFAULT 'Autumn Accessions'"),
                ("condition_notes", "TEXT NOT NULL DEFAULT ''"),
                ("collation", "TEXT NOT NULL DEFAULT ''"),
                ("publisher", "TEXT NOT NULL DEFAULT ''"),
                ("year", "INTEGER NOT NULL DEFAULT 0"),
                ("held_until", "TEXT"),
                ("hold_slip_code", "TEXT"),
                ("patron_info", "TEXT NOT NULL DEFAULT ''"),
                ("spine_image_url", "TEXT NOT NULL DEFAULT ''"),
            ]:
                with suppress(sqlite3.OperationalError):
                    conn.execute(f"ALTER TABLE inventory ADD COLUMN {col} {col_def}")

        from app.integrations.database import install_sqlite

        with self._transaction() as conn:
            install_sqlite(conn)

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
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
            conn.execute("PRAGMA recursive_triggers=ON")
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
            existing = conn.execute("SELECT tenant_id FROM inventory WHERE sku=?", (item.sku,)).fetchone()
            if existing and existing["tenant_id"] != tenant_id:
                raise ValueError("SKU belongs to another workspace")
            conn.execute(
                "INSERT OR REPLACE INTO inventory (sku, tenant_id, title, authors, format, condition, "
                "in_store_price_cad, online_price_cad, status, created_at, updated_at, section, shelf, "
                "last_audited_at, batch_id, batch_name, condition_notes, collation, publisher, year, "
                "held_until, hold_slip_code, patron_info, spine_image_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                    item.batch_id,
                    item.batch_name,
                    item.condition_notes,
                    item.collation,
                    item.publisher,
                    item.year,
                    item.held_until,
                    item.hold_slip_code,
                    item.patron_info,
                    item.spine_image_url,
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
                "SELECT * FROM inventory WHERE tenant_id = ? AND section = ? AND shelf = ? "
                "AND status = 'active' ORDER BY title ASC",
                (tenant_id, section, shelf),
            ).fetchall()
            return [_row_to_item(dict(row)) for row in rows]

    def update_prices(self, sku: str, in_store: Decimal, online: Decimal, tenant_id: int = 1) -> None:
        with self._transaction() as conn:
            conn.execute(
                "UPDATE inventory SET in_store_price_cad = ?, online_price_cad = ?, "
                "updated_at = ? WHERE sku = ? AND tenant_id = ?",
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

    def relist(self, sku: str, tenant_id: int = 1) -> None:
        with self._transaction() as conn:
            from app.integrations.database import clear_for_relist

            clear_for_relist(conn, False, tenant_id, [sku])
            conn.execute(
                "UPDATE inventory SET status = 'active', updated_at = ? WHERE sku = ? AND tenant_id = ?",
                (datetime.now(UTC).isoformat(), sku, tenant_id),
            )

    def delete(self, sku: str, tenant_id: int = 1) -> bool:
        with self._transaction() as conn:
            res = conn.execute(
                "DELETE FROM inventory WHERE sku = ? AND tenant_id = ?",
                (sku, tenant_id),
            )
            return bool(res.rowcount and res.rowcount > 0)

    def get_batch(self, skus: list[str], tenant_id: int = 1) -> list[InventoryItem]:
        if not skus:
            return []
        placeholders = ",".join("?" for _ in skus)
        with self._transaction() as conn:
            rows = conn.execute(
                f"SELECT * FROM inventory WHERE sku IN ({placeholders}) AND tenant_id = ?",
                (*skus, tenant_id),
            ).fetchall()
            return [_row_to_item(dict(r)) for r in rows]

    def batch_delist(self, skus: list[str], tenant_id: int = 1) -> int:
        if not skus:
            return 0
        placeholders = ",".join("?" for _ in skus)
        with self._transaction() as conn:
            res = conn.execute(
                f"UPDATE inventory SET status = 'delisted', updated_at = ? WHERE sku IN ({placeholders}) "
                "AND tenant_id = ?",
                (datetime.now(UTC).isoformat(), *skus, tenant_id),
            )
            return res.rowcount or 0

    def batch_relist(self, skus: list[str], tenant_id: int = 1) -> int:
        if not skus:
            return 0
        placeholders = ",".join("?" for _ in skus)
        with self._transaction() as conn:
            from app.integrations.database import clear_for_relist

            clear_for_relist(conn, False, tenant_id, skus)
            res = conn.execute(
                f"UPDATE inventory SET status = 'active', updated_at = ? WHERE sku IN ({placeholders}) "
                "AND tenant_id = ?",
                (datetime.now(UTC).isoformat(), *skus, tenant_id),
            )
            return res.rowcount or 0

    def batch_update_location(self, skus: list[str], section: str, shelf: str, tenant_id: int = 1) -> int:
        if not skus:
            return 0
        placeholders = ",".join("?" for _ in skus)
        with self._transaction() as conn:
            res = conn.execute(
                f"UPDATE inventory SET section = ?, shelf = ?, updated_at = ? WHERE sku IN ({placeholders}) "
                "AND tenant_id = ?",
                (section, shelf, datetime.now(UTC).isoformat(), *skus, tenant_id),
            )
            return res.rowcount or 0

    def batch_delete(self, skus: list[str], tenant_id: int = 1) -> int:
        if not skus:
            return 0
        placeholders = ",".join("?" for _ in skus)
        with self._transaction() as conn:
            res = conn.execute(
                f"DELETE FROM inventory WHERE sku IN ({placeholders}) AND tenant_id = ?",
                (*skus, tenant_id),
            )
            return res.rowcount or 0

    def batch_update_prices(self, updates: dict[str, tuple[Decimal, Decimal]], tenant_id: int = 1) -> int:
        if not updates:
            return 0
        now = datetime.now(UTC).isoformat()
        count = 0
        with self._transaction() as conn:
            for sku, (in_store, online) in updates.items():
                res = conn.execute(
                    "UPDATE inventory SET in_store_price_cad = ?, online_price_cad = ?, "
                    "updated_at = ? WHERE sku = ? AND tenant_id = ?",
                    (str(in_store), str(online), now, sku, tenant_id),
                )
                count += res.rowcount or 0
        return count

    def record_shelf_audit(self, audit: ShelfAuditRecord) -> None:
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO shelf_audits (id, tenant_id, section, shelf, audited_by, total_expected, "
                "total_scanned, verified_count, missing_count, misplaced_count, discrepancies, created_at) "
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
                    discrepancies=json.loads(row["discrepancies"])
                    if isinstance(row["discrepancies"], str)
                    else row["discrepancies"],
                    created_at=_parse_dt(row["created_at"]),
                )
                for row in rows
            ]

    def create_hold(
        self,
        sku: str,
        slip_code: str,
        patron_name: str,
        patron_email: str,
        patron_phone: str = "",
        pickup_note: str = "",
        held_until: str = "",
        tenant_id: int = 1,
    ) -> bool:
        patron_dict = {
            "name": patron_name,
            "email": patron_email,
            "phone": patron_phone,
            "note": pickup_note,
        }
        with self._transaction() as conn:
            cur = conn.execute(
                "UPDATE inventory SET status = 'held', held_until = ?, hold_slip_code = ?, "
                "patron_info = ?, updated_at = ? WHERE sku = ? AND tenant_id = ?",
                (
                    held_until,
                    slip_code,
                    json.dumps(patron_dict),
                    datetime.now(UTC).isoformat(),
                    sku,
                    tenant_id,
                ),
            )
            return bool(cur.rowcount and cur.rowcount > 0)

    def release_hold(self, sku: str, tenant_id: int = 1) -> bool:
        with self._transaction() as conn:
            cur = conn.execute(
                "UPDATE inventory SET status = 'active', held_until = NULL, hold_slip_code = NULL, "
                "patron_info = '', updated_at = ? WHERE sku = ? AND tenant_id = ?",
                (datetime.now(UTC).isoformat(), sku, tenant_id),
            )
            return bool(cur.rowcount and cur.rowcount > 0)

    def list_expired_holds(self, tenant_id: int = 1) -> list[str]:
        now_iso = datetime.now(UTC).isoformat()
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT sku FROM inventory WHERE tenant_id = ? AND status = 'held' "
                "AND held_until IS NOT NULL AND held_until < ?",
                (tenant_id, now_iso),
            ).fetchall()
            return [row["sku"] for row in rows]

    def create_checkout_reservation(
        self,
        reservation_id: str,
        checkout_id: str,
        expires_at: str,
        items: list[dict[str, Any]],
        tenant_id: int = 1,
    ) -> bool:
        now_iso = datetime.now(UTC).isoformat()
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO checkout_reservations (reservation_id, checkout_id, tenant_id, status, "
                "expires_at, created_at) VALUES (?, ?, ?, 'active', ?, ?)",
                (reservation_id, checkout_id, tenant_id, expires_at, now_iso),
            )
            for item in items:
                conn.execute(
                    "INSERT INTO checkout_reservation_items (reservation_id, sku, quantity, "
                    "price_amount, price_currency) VALUES (?, ?, ?, ?, ?)",
                    (
                        reservation_id,
                        item["sku"],
                        item.get("quantity", 1),
                        str(item["unit_price"]["amount"]),
                        item["unit_price"].get("currency", "CAD"),
                    ),
                )
            return True

    def get_checkout_reservation(self, reservation_id: str, tenant_id: int = 1) -> dict[str, Any] | None:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM checkout_reservations WHERE reservation_id = ? AND tenant_id = ?",
                (reservation_id, tenant_id),
            ).fetchone()
            if not row:
                return None
            items_rows = conn.execute(
                "SELECT * FROM checkout_reservation_items WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchall()
            items = [
                {
                    "sku": ir["sku"],
                    "quantity": ir["quantity"],
                    "unit_price": {"amount": ir["price_amount"], "currency": ir["price_currency"]},
                }
                for ir in items_rows
            ]
            return {
                "reservation_id": row["reservation_id"],
                "checkout_id": row["checkout_id"],
                "status": row["status"],
                "expires_at": row["expires_at"],
                "created_at": row["created_at"],
                "items": items,
            }

    def get_active_reservation_for_checkout(self, checkout_id: str, tenant_id: int = 1) -> dict[str, Any] | None:
        now_iso = datetime.now(UTC).isoformat()
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT reservation_id FROM checkout_reservations WHERE checkout_id = ? AND tenant_id = ? "
                "AND status = 'active' AND expires_at > ?",
                (checkout_id, tenant_id, now_iso),
            ).fetchone()
            if not row:
                return None
            return self.get_checkout_reservation(row["reservation_id"], tenant_id=tenant_id)

    def is_sku_reserved(self, sku: str, tenant_id: int = 1) -> bool:
        now_iso = datetime.now(UTC).isoformat()
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT cri.sku
                FROM checkout_reservation_items cri
                JOIN checkout_reservations cr ON cri.reservation_id = cr.reservation_id
                WHERE cri.sku = ? AND cr.tenant_id = ? AND cr.status = 'active' AND cr.expires_at > ?
                LIMIT 1
                """,
                (sku, tenant_id, now_iso),
            ).fetchone()
            return bool(row)

    def release_checkout_reservation(self, reservation_id: str, tenant_id: int = 1) -> str:
        now_iso = datetime.now(UTC).isoformat()
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT status, expires_at FROM checkout_reservations WHERE reservation_id = ? AND tenant_id = ?",
                (reservation_id, tenant_id),
            ).fetchone()
            if not row:
                return "not_found"
            if row["expires_at"] <= now_iso:
                conn.execute(
                    "UPDATE checkout_reservations SET status = 'expired' WHERE reservation_id = ?",
                    (reservation_id,),
                )
                return "expired"
            conn.execute(
                "UPDATE checkout_reservations SET status = 'released' WHERE reservation_id = ?",
                (reservation_id,),
            )
            return "released"

    def consume_checkout_reservation(self, reservation_id: str, order_id: str, tenant_id: int = 1) -> bool:
        now_iso = datetime.now(UTC).isoformat()
        with self._transaction() as conn:
            res = conn.execute(
                "SELECT * FROM checkout_reservations WHERE reservation_id = ? AND tenant_id = ?",
                (reservation_id, tenant_id),
            ).fetchone()
            if not res or res["status"] != "active" or res["expires_at"] <= now_iso:
                return False

            conn.execute(
                "UPDATE checkout_reservations SET status = 'consumed' WHERE reservation_id = ?",
                (reservation_id,),
            )
            items = conn.execute(
                "SELECT sku FROM checkout_reservation_items WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchall()
            for it in items:
                conn.execute(
                    "UPDATE inventory SET status = 'sold', updated_at = ? WHERE sku = ? AND tenant_id = ?",
                    (now_iso, it["sku"], tenant_id),
                )
            return True

    def get_custom_site_publication(self, sku: str, tenant_id: int = 1) -> dict[str, Any] | None:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM custom_site_publications WHERE sku = ? AND tenant_id = ?",
                (sku, tenant_id),
            ).fetchone()
            return dict(row) if row else None

    def record_custom_site_publication(
        self,
        sku: str,
        revision: int,
        publication: str,
        availability: str,
        quantity: int,
        body_digest: str,
        payload_json: str,
        status: str = "published",
        tenant_id: int = 1,
    ) -> None:
        now_iso = datetime.now(UTC).isoformat()
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO custom_site_publications (
                    sku, tenant_id, revision, publication, availability, quantity,
                    last_body_digest, last_payload_json, status, last_published_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sku, tenant_id) DO UPDATE SET
                    revision = excluded.revision,
                    publication = excluded.publication,
                    availability = excluded.availability,
                    quantity = excluded.quantity,
                    last_body_digest = excluded.last_body_digest,
                    last_payload_json = excluded.last_payload_json,
                    status = excluded.status,
                    last_published_at = excluded.last_published_at
                """,
                (
                    sku,
                    tenant_id,
                    revision,
                    publication,
                    availability,
                    quantity,
                    body_digest,
                    payload_json,
                    status,
                    now_iso,
                ),
            )

    def queue_channel_sync(
        self,
        event_id: str,
        order_id: str,
        reservation_id: str,
        event_type: str,
        channel_results: list[dict[str, Any]] | None = None,
        tenant_id: int = 1,
    ) -> None:
        now_iso = datetime.now(UTC).isoformat()
        cr_json = json.dumps(
            channel_results
            or [
                {"channel": "abebooks", "status": "pending"},
                {"channel": "square", "status": "pending"},
                {"channel": "ebay", "status": "pending"},
            ]
        )
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO channel_sync_outbox (
                    event_id, tenant_id, order_id, reservation_id, event_type,
                    status, channel_results_json, attempts, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?, 0, ?)
                """,
                (event_id, tenant_id, order_id, reservation_id, event_type, cr_json, now_iso),
            )

    def get_channel_sync(self, event_id: str, tenant_id: int = 1) -> dict[str, Any] | None:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM channel_sync_outbox WHERE event_id = ? AND tenant_id = ?",
                (event_id, tenant_id),
            ).fetchone()
            if not row:
                return None
            return {
                "event_id": row["event_id"],
                "order_id": row["order_id"],
                "reservation_id": row["reservation_id"],
                "event_type": row["event_type"],
                "status": row["status"],
                "channel_results": json.loads(row["channel_results_json"]),
                "attempts": row["attempts"],
                "last_attempt_at": row["last_attempt_at"],
                "last_error": row["last_error"],
                "created_at": row["created_at"],
            }

    def update_channel_sync(
        self,
        event_id: str,
        status: str,
        channel_results: list[dict[str, Any]],
        last_error: str | None = None,
        tenant_id: int = 1,
    ) -> None:
        now_iso = datetime.now(UTC).isoformat()
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE channel_sync_outbox
                SET status = ?, channel_results_json = ?, attempts = attempts + 1, last_attempt_at = ?, last_error = ?
                WHERE event_id = ? AND tenant_id = ?
                """,
                (status, json.dumps(channel_results), now_iso, last_error, event_id, tenant_id),
            )

    def get_integration_connection(self, key_id: str) -> dict[str, Any] | None:
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM integration_connections WHERE key_id = ? AND is_active = 1",
                (key_id,),
            ).fetchone()
            return dict(row) if row else None

    def save_integration_connection(
        self,
        key_id: str,
        secret_key: str,
        direction: str,
        connection_type: str = "custom_site",
        tenant_id: int = 1,
    ) -> None:
        now_iso = datetime.now(UTC).isoformat()
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO integration_connections (
                    key_id, tenant_id, connection_type, direction, secret_key, is_active, created_at
                )
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(key_id) DO UPDATE SET secret_key = excluded.secret_key, is_active = 1
                """,
                (key_id, tenant_id, connection_type, direction, secret_key, now_iso),
            )

    def close(self) -> None:
        if self._mem_conn is not None:
            with self._lock:
                self._mem_conn.close()
                self._mem_conn = None
