from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol


@dataclass
class Collector:
    id: str
    tenant_id: int
    name: str
    email: str = ""
    phone: str = ""
    notes: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class WantItem:
    id: str
    collector_id: str
    tenant_id: int
    title: str
    author: str = ""
    max_price_cad: Decimal | None = None
    min_condition: str | None = None
    binding: str | None = None
    keywords: str = ""
    status: str = "active"  # "active", "fulfilled", "cancelled"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class CollectorHold:
    id: str
    tenant_id: int
    sku: str
    want_item_id: str
    collector_id: str
    held_at: datetime
    expires_at: datetime
    status: str = "active"  # "active", "claimed", "released", "expired"
    notes: str = ""
    book_title: str = ""
    book_author: str = ""
    collector_name: str = ""
    price_cad: Decimal | None = None
    claimed_at: datetime | None = None
    released_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class WantListStore(Protocol):
    def create_collector(self, collector: Collector) -> Collector: ...
    def get_collector(self, collector_id: str, tenant_id: int = 1) -> Collector | None: ...
    def list_collectors(self, tenant_id: int = 1, query: str = "") -> list[Collector]: ...
    def update_collector(self, collector_id: str, tenant_id: int = 1, **kwargs: Any) -> Collector | None: ...
    def delete_collector(self, collector_id: str, tenant_id: int = 1) -> bool: ...

    def create_want(self, want: WantItem) -> WantItem: ...
    def get_want(self, want_id: str, tenant_id: int = 1) -> WantItem | None: ...
    def list_wants(self, tenant_id: int = 1, collector_id: str | None = None, status: str | None = None) -> list[WantItem]: ...
    def update_want(self, want_id: str, tenant_id: int = 1, **kwargs: Any) -> WantItem | None: ...
    def delete_want(self, want_id: str, tenant_id: int = 1) -> bool: ...

    def create_hold(self, hold: CollectorHold) -> CollectorHold: ...
    def get_hold(self, hold_id: str, tenant_id: int = 1) -> CollectorHold | None: ...
    def get_active_hold_for_sku(self, sku: str, tenant_id: int = 1) -> CollectorHold | None: ...
    def list_holds(self, tenant_id: int = 1, status: str | None = None) -> list[CollectorHold]: ...
    def update_hold(self, hold_id: str, tenant_id: int = 1, **kwargs: Any) -> CollectorHold | None: ...


def _parse_dt(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val.astimezone(UTC) if val.tzinfo else val.replace(tzinfo=UTC)
    if isinstance(val, str):
        try:
            parsed = datetime.fromisoformat(val)
            return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except Exception:
            pass
    return datetime.now(UTC)


def _row_to_collector(row: dict[str, Any] | Any) -> Collector:
    d = dict(row)
    return Collector(
        id=str(d["id"]),
        tenant_id=int(d["tenant_id"]),
        name=str(d["name"]),
        email=str(d.get("email") or ""),
        phone=str(d.get("phone") or ""),
        notes=str(d.get("notes") or ""),
        created_at=_parse_dt(d["created_at"]),
        updated_at=_parse_dt(d["updated_at"]),
    )


def _row_to_want(row: dict[str, Any] | Any) -> WantItem:
    d = dict(row)
    max_price = Decimal(str(d["max_price_cad"])) if d.get("max_price_cad") is not None else None
    return WantItem(
        id=str(d["id"]),
        collector_id=str(d["collector_id"]),
        tenant_id=int(d["tenant_id"]),
        title=str(d["title"]),
        author=str(d.get("author") or ""),
        max_price_cad=max_price,
        min_condition=str(d["min_condition"]) if d.get("min_condition") else None,
        binding=str(d["binding"]) if d.get("binding") else None,
        keywords=str(d.get("keywords") or ""),
        status=str(d.get("status") or "active"),
        created_at=_parse_dt(d["created_at"]),
        updated_at=_parse_dt(d["updated_at"]),
    )


def _row_to_hold(row: dict[str, Any] | Any) -> CollectorHold:
    d = dict(row)
    price = Decimal(str(d["price_cad"])) if d.get("price_cad") is not None else None
    return CollectorHold(
        id=str(d["id"]),
        tenant_id=int(d["tenant_id"]),
        sku=str(d["sku"]),
        want_item_id=str(d["want_item_id"]),
        collector_id=str(d["collector_id"]),
        held_at=_parse_dt(d["held_at"]),
        expires_at=_parse_dt(d["expires_at"]),
        status=str(d.get("status") or "active"),
        notes=str(d.get("notes") or ""),
        book_title=str(d.get("book_title") or ""),
        book_author=str(d.get("book_author") or ""),
        collector_name=str(d.get("collector_name") or ""),
        price_cad=price,
        claimed_at=_parse_dt(d["claimed_at"]) if d.get("claimed_at") else None,
        released_at=_parse_dt(d["released_at"]) if d.get("released_at") else None,
        created_at=_parse_dt(d["created_at"]),
    )


class SQLiteWantListStore:
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
                CREATE TABLE IF NOT EXISTS collectors (
                    id TEXT PRIMARY KEY,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS want_items (
                    id TEXT PRIMARY KEY,
                    collector_id TEXT NOT NULL,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    title TEXT NOT NULL,
                    author TEXT NOT NULL DEFAULT '',
                    max_price_cad TEXT,
                    min_condition TEXT,
                    binding TEXT,
                    keywords TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(collector_id) REFERENCES collectors(id) ON DELETE CASCADE
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS collector_holds (
                    id TEXT PRIMARY KEY,
                    tenant_id INTEGER NOT NULL DEFAULT 1,
                    sku TEXT NOT NULL,
                    want_item_id TEXT NOT NULL,
                    collector_id TEXT NOT NULL,
                    held_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    notes TEXT NOT NULL DEFAULT '',
                    book_title TEXT NOT NULL DEFAULT '',
                    book_author TEXT NOT NULL DEFAULT '',
                    collector_name TEXT NOT NULL DEFAULT '',
                    price_cad TEXT,
                    claimed_at TEXT,
                    released_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(collector_id) REFERENCES collectors(id),
                    FOREIGN KEY(want_item_id) REFERENCES want_items(id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_collectors_tenant ON collectors(tenant_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wants_tenant_status ON want_items(tenant_id, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_wants_collector ON want_items(collector_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_holds_tenant_status ON collector_holds(tenant_id, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_holds_sku ON collector_holds(sku)")

            # Seed sample collectors for tenant 1 if empty
            cur = conn.execute("SELECT COUNT(*) as cnt FROM collectors WHERE tenant_id = 1")
            row = cur.fetchone()
            if row and row["cnt"] == 0:
                self._seed_sample_data(conn)

    def _seed_sample_data(self, conn: Any) -> None:
        now_str = datetime.now(UTC).isoformat()
        samples = [
            (
                "col-arthur-pendelton",
                1,
                "Dr. Arthur Pendelton",
                "arthur.pendelton@sympatico.ca",
                "(613) 555-0144",
                "Early Canadiana collector, Hugh MacLennan firsts, Bank St. regular.",
                now_str,
                now_str,
            ),
            (
                "col-eleanor-vance",
                1,
                "Prof. Eleanor Vance",
                "eleanor.vance@carleton.ca",
                "(613) 555-0182",
                "Carleton English literature faculty. Collecting Robertson Davies firsts in jacket.",
                now_str,
                now_str,
            ),
            (
                "col-michael-davies",
                1,
                "Michael Davies",
                "mdavies@ottawa-history.org",
                "(613) 555-0199",
                "Arctic exploration, polar journals, Canadian geographic expeditions.",
                now_str,
                now_str,
            ),
        ]
        conn.executemany(
            "INSERT INTO collectors (id, tenant_id, name, email, phone, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            samples,
        )
        sample_wants = [
            (
                "wnt-two-solitudes",
                "col-arthur-pendelton",
                1,
                "Two Solitudes",
                "Hugh MacLennan",
                "175.00",
                "Good",
                "Hardcover",
                "Collins 1945 first edition with dust jacket preferred.",
                "active",
                now_str,
                now_str,
            ),
            (
                "wnt-fifth-business",
                "col-eleanor-vance",
                1,
                "Fifth Business",
                "Robertson Davies",
                "120.00",
                "Very Good",
                "Hardcover",
                "Macmillan 1970 first edition.",
                "active",
                now_str,
                now_str,
            ),
            (
                "wnt-friendly-arctic",
                "col-michael-davies",
                1,
                "The Friendly Arctic",
                "Vilhjalmur Stefansson",
                "250.00",
                "Good",
                "Hardcover",
                "Macmillan 1921 with original folding map intact.",
                "active",
                now_str,
                now_str,
            ),
        ]
        conn.executemany(
            "INSERT INTO want_items (id, collector_id, tenant_id, title, author, max_price_cad, min_condition, binding, keywords, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            sample_wants,
        )

    @contextmanager
    def _transaction(self) -> Any:
        import sqlite3

        if self._mem_conn:
            with self._lock:
                yield self._mem_conn
            return

        with self._lock:
            conn = sqlite3.connect(self.path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            try:
                with conn:
                    yield conn
            finally:
                conn.close()

    # Collector operations
    def create_collector(self, collector: Collector) -> Collector:
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO collectors (id, tenant_id, name, email, phone, notes, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    collector.id,
                    collector.tenant_id,
                    collector.name,
                    collector.email,
                    collector.phone,
                    collector.notes,
                    collector.created_at.isoformat(),
                    collector.updated_at.isoformat(),
                ),
            )
        return collector

    def get_collector(self, collector_id: str, tenant_id: int = 1) -> Collector | None:
        with self._transaction() as conn:
            cur = conn.execute(
                "SELECT * FROM collectors WHERE id = ? AND tenant_id = ?",
                (collector_id, tenant_id),
            )
            row = cur.fetchone()
            return _row_to_collector(row) if row else None

    def list_collectors(self, tenant_id: int = 1, query: str = "") -> list[Collector]:
        with self._transaction() as conn:
            if query:
                q = f"%{query}%"
                cur = conn.execute(
                    "SELECT * FROM collectors WHERE tenant_id = ? AND (name LIKE ? OR email LIKE ? OR phone LIKE ? OR notes LIKE ?) ORDER BY name ASC",
                    (tenant_id, q, q, q, q),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM collectors WHERE tenant_id = ? ORDER BY name ASC",
                    (tenant_id,),
                )
            return [_row_to_collector(r) for r in cur.fetchall()]

    def update_collector(self, collector_id: str, tenant_id: int = 1, **kwargs: Any) -> Collector | None:
        current = self.get_collector(collector_id, tenant_id=tenant_id)
        if not current:
            return None
        allowed = {"name", "email", "phone", "notes"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return current
        updates["updated_at"] = datetime.now(UTC).isoformat()
        clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [collector_id, tenant_id]
        with self._transaction() as conn:
            conn.execute(f"UPDATE collectors SET {clause} WHERE id = ? AND tenant_id = ?", params)
        return self.get_collector(collector_id, tenant_id=tenant_id)

    def delete_collector(self, collector_id: str, tenant_id: int = 1) -> bool:
        with self._transaction() as conn:
            cur = conn.execute(
                "DELETE FROM collectors WHERE id = ? AND tenant_id = ?",
                (collector_id, tenant_id),
            )
            return cur.rowcount > 0

    # Want operations
    def create_want(self, want: WantItem) -> WantItem:
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO want_items (id, collector_id, tenant_id, title, author, max_price_cad, min_condition, binding, keywords, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    want.id,
                    want.collector_id,
                    want.tenant_id,
                    want.title,
                    want.author,
                    str(want.max_price_cad) if want.max_price_cad is not None else None,
                    want.min_condition,
                    want.binding,
                    want.keywords,
                    want.status,
                    want.created_at.isoformat(),
                    want.updated_at.isoformat(),
                ),
            )
        return want

    def get_want(self, want_id: str, tenant_id: int = 1) -> WantItem | None:
        with self._transaction() as conn:
            cur = conn.execute(
                "SELECT * FROM want_items WHERE id = ? AND tenant_id = ?",
                (want_id, tenant_id),
            )
            row = cur.fetchone()
            return _row_to_want(row) if row else None

    def list_wants(self, tenant_id: int = 1, collector_id: str | None = None, status: str | None = None) -> list[WantItem]:
        query = "SELECT * FROM want_items WHERE tenant_id = ?"
        params: list[Any] = [tenant_id]
        if collector_id:
            query += " AND collector_id = ?"
            params.append(collector_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        with self._transaction() as conn:
            cur = conn.execute(query, params)
            return [_row_to_want(r) for r in cur.fetchall()]

    def update_want(self, want_id: str, tenant_id: int = 1, **kwargs: Any) -> WantItem | None:
        current = self.get_want(want_id, tenant_id=tenant_id)
        if not current:
            return None
        allowed = {"title", "author", "max_price_cad", "min_condition", "binding", "keywords", "status"}
        updates: dict[str, Any] = {}
        for k, v in kwargs.items():
            if k in allowed:
                if k == "max_price_cad" and v is not None:
                    updates[k] = str(v)
                else:
                    updates[k] = v
        if not updates:
            return current
        updates["updated_at"] = datetime.now(UTC).isoformat()
        clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [want_id, tenant_id]
        with self._transaction() as conn:
            conn.execute(f"UPDATE want_items SET {clause} WHERE id = ? AND tenant_id = ?", params)
        return self.get_want(want_id, tenant_id=tenant_id)

    def delete_want(self, want_id: str, tenant_id: int = 1) -> bool:
        with self._transaction() as conn:
            cur = conn.execute(
                "DELETE FROM want_items WHERE id = ? AND tenant_id = ?",
                (want_id, tenant_id),
            )
            return cur.rowcount > 0

    # Hold operations
    def create_hold(self, hold: CollectorHold) -> CollectorHold:
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO collector_holds (id, tenant_id, sku, want_item_id, collector_id, held_at, expires_at, status, notes, book_title, book_author, collector_name, price_cad, claimed_at, released_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    hold.id,
                    hold.tenant_id,
                    hold.sku,
                    hold.want_item_id,
                    hold.collector_id,
                    hold.held_at.isoformat(),
                    hold.expires_at.isoformat(),
                    hold.status,
                    hold.notes,
                    hold.book_title,
                    hold.book_author,
                    hold.collector_name,
                    str(hold.price_cad) if hold.price_cad is not None else None,
                    hold.claimed_at.isoformat() if hold.claimed_at else None,
                    hold.released_at.isoformat() if hold.released_at else None,
                    hold.created_at.isoformat(),
                ),
            )
        return hold

    def get_hold(self, hold_id: str, tenant_id: int = 1) -> CollectorHold | None:
        with self._transaction() as conn:
            cur = conn.execute(
                "SELECT * FROM collector_holds WHERE id = ? AND tenant_id = ?",
                (hold_id, tenant_id),
            )
            row = cur.fetchone()
            return _row_to_hold(row) if row else None

    def get_active_hold_for_sku(self, sku: str, tenant_id: int = 1) -> CollectorHold | None:
        with self._transaction() as conn:
            cur = conn.execute(
                "SELECT * FROM collector_holds WHERE sku = ? AND tenant_id = ? AND status = 'active'",
                (sku, tenant_id),
            )
            row = cur.fetchone()
            return _row_to_hold(row) if row else None

    def list_holds(self, tenant_id: int = 1, status: str | None = None) -> list[CollectorHold]:
        query = "SELECT * FROM collector_holds WHERE tenant_id = ?"
        params: list[Any] = [tenant_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY held_at DESC"
        with self._transaction() as conn:
            cur = conn.execute(query, params)
            return [_row_to_hold(r) for r in cur.fetchall()]

    def update_hold(self, hold_id: str, tenant_id: int = 1, **kwargs: Any) -> CollectorHold | None:
        current = self.get_hold(hold_id, tenant_id=tenant_id)
        if not current:
            return None
        allowed = {"status", "notes", "claimed_at", "released_at", "expires_at"}
        updates: dict[str, Any] = {}
        for k, v in kwargs.items():
            if k in allowed:
                if isinstance(v, datetime):
                    updates[k] = v.isoformat()
                else:
                    updates[k] = v
        if not updates:
            return current
        clause = ", ".join(f"{k} = ?" for k in updates)
        params = list(updates.values()) + [hold_id, tenant_id]
        with self._transaction() as conn:
            conn.execute(f"UPDATE collector_holds SET {clause} WHERE id = ? AND tenant_id = ?", params)
        return self.get_hold(hold_id, tenant_id=tenant_id)


class PostgresWantListStore:
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
        self._ensure_sample_data()

    def _ensure_sample_data(self) -> None:
        try:
            with self.pool.connection() as conn, conn.transaction():
                cur = conn.execute("SELECT COUNT(*) as cnt FROM collectors WHERE tenant_id = 1")
                row = cur.fetchone()
                if row and row["cnt"] == 0:
                    now = datetime.now(UTC)
                    conn.execute(
                        "INSERT INTO collectors (id, tenant_id, name, email, phone, notes, created_at, updated_at) VALUES "
                        "(%s, 1, %s, %s, %s, %s, %s, %s), "
                        "(%s, 1, %s, %s, %s, %s, %s, %s), "
                        "(%s, 1, %s, %s, %s, %s, %s, %s)",
                        (
                            "col-arthur-pendelton", "Dr. Arthur Pendelton", "arthur.pendelton@sympatico.ca", "(613) 555-0144",
                            "Early Canadiana collector, Hugh MacLennan firsts, Bank St. regular.", now, now,
                            "col-eleanor-vance", "Prof. Eleanor Vance", "eleanor.vance@carleton.ca", "(613) 555-0182",
                            "Carleton English literature faculty. Collecting Robertson Davies firsts in jacket.", now, now,
                            "col-michael-davies", "Michael Davies", "mdavies@ottawa-history.org", "(613) 555-0199",
                            "Arctic exploration, polar journals, Canadian geographic expeditions.", now, now,
                        ),
                    )
                    conn.execute(
                        "INSERT INTO want_items (id, collector_id, tenant_id, title, author, max_price_cad, min_condition, binding, keywords, status, created_at, updated_at) VALUES "
                        "(%s, %s, 1, %s, %s, %s, %s, %s, %s, 'active', %s, %s), "
                        "(%s, %s, 1, %s, %s, %s, %s, %s, %s, 'active', %s, %s), "
                        "(%s, %s, 1, %s, %s, %s, %s, %s, %s, 'active', %s, %s)",
                        (
                            "wnt-two-solitudes", "col-arthur-pendelton", "Two Solitudes", "Hugh MacLennan", Decimal("175.00"), "Good", "Hardcover", "Collins 1945 first edition with dust jacket preferred.", now, now,
                            "wnt-fifth-business", "col-eleanor-vance", "Fifth Business", "Robertson Davies", Decimal("120.00"), "Very Good", "Hardcover", "Macmillan 1970 first edition.", now, now,
                            "wnt-friendly-arctic", "col-michael-davies", "The Friendly Arctic", "Vilhjalmur Stefansson", Decimal("250.00"), "Good", "Hardcover", "Macmillan 1921 with original folding map intact.", now, now,
                        ),
                    )
        except Exception:
            pass

    # Collector operations
    def create_collector(self, collector: Collector) -> Collector:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO collectors (id, tenant_id, name, email, phone, notes, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    collector.id,
                    collector.tenant_id,
                    collector.name,
                    collector.email,
                    collector.phone,
                    collector.notes,
                    collector.created_at,
                    collector.updated_at,
                ),
            )
        return collector

    def get_collector(self, collector_id: str, tenant_id: int = 1) -> Collector | None:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "SELECT * FROM collectors WHERE id = %s AND tenant_id = %s",
                (collector_id, tenant_id),
            )
            row = cur.fetchone()
            return _row_to_collector(row) if row else None

    def list_collectors(self, tenant_id: int = 1, query: str = "") -> list[Collector]:
        with self.pool.connection() as conn:
            if query:
                q = f"%{query}%"
                cur = conn.execute(
                    "SELECT * FROM collectors WHERE tenant_id = %s AND (name ILIKE %s OR email ILIKE %s OR phone ILIKE %s OR notes ILIKE %s) ORDER BY name ASC",
                    (tenant_id, q, q, q, q),
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM collectors WHERE tenant_id = %s ORDER BY name ASC",
                    (tenant_id,),
                )
            return [_row_to_collector(r) for r in cur.fetchall()]

    def update_collector(self, collector_id: str, tenant_id: int = 1, **kwargs: Any) -> Collector | None:
        current = self.get_collector(collector_id, tenant_id=tenant_id)
        if not current:
            return None
        allowed = {"name", "email", "phone", "notes"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return current
        updates["updated_at"] = datetime.now(UTC)
        clause = ", ".join(f"{k} = %s" for k in updates)
        params = list(updates.values()) + [collector_id, tenant_id]
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(f"UPDATE collectors SET {clause} WHERE id = %s AND tenant_id = %s", params)
        return self.get_collector(collector_id, tenant_id=tenant_id)

    def delete_collector(self, collector_id: str, tenant_id: int = 1) -> bool:
        with self.pool.connection() as conn, conn.transaction():
            cur = conn.execute(
                "DELETE FROM collectors WHERE id = %s AND tenant_id = %s",
                (collector_id, tenant_id),
            )
            return cur.rowcount > 0

    # Want operations
    def create_want(self, want: WantItem) -> WantItem:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO want_items (id, collector_id, tenant_id, title, author, max_price_cad, min_condition, binding, keywords, status, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    want.id,
                    want.collector_id,
                    want.tenant_id,
                    want.title,
                    want.author,
                    want.max_price_cad,
                    want.min_condition,
                    want.binding,
                    want.keywords,
                    want.status,
                    want.created_at,
                    want.updated_at,
                ),
            )
        return want

    def get_want(self, want_id: str, tenant_id: int = 1) -> WantItem | None:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "SELECT * FROM want_items WHERE id = %s AND tenant_id = %s",
                (want_id, tenant_id),
            )
            row = cur.fetchone()
            return _row_to_want(row) if row else None

    def list_wants(self, tenant_id: int = 1, collector_id: str | None = None, status: str | None = None) -> list[WantItem]:
        query = "SELECT * FROM want_items WHERE tenant_id = %s"
        params: list[Any] = [tenant_id]
        if collector_id:
            query += " AND collector_id = %s"
            params.append(collector_id)
        if status:
            query += " AND status = %s"
            params.append(status)
        query += " ORDER BY created_at DESC"
        with self.pool.connection() as conn:
            cur = conn.execute(query, params)
            return [_row_to_want(r) for r in cur.fetchall()]

    def update_want(self, want_id: str, tenant_id: int = 1, **kwargs: Any) -> WantItem | None:
        current = self.get_want(want_id, tenant_id=tenant_id)
        if not current:
            return None
        allowed = {"title", "author", "max_price_cad", "min_condition", "binding", "keywords", "status"}
        updates: dict[str, Any] = {}
        for k, v in kwargs.items():
            if k in allowed:
                updates[k] = v
        if not updates:
            return current
        updates["updated_at"] = datetime.now(UTC)
        clause = ", ".join(f"{k} = %s" for k in updates)
        params = list(updates.values()) + [want_id, tenant_id]
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(f"UPDATE want_items SET {clause} WHERE id = %s AND tenant_id = %s", params)
        return self.get_want(want_id, tenant_id=tenant_id)

    def delete_want(self, want_id: str, tenant_id: int = 1) -> bool:
        with self.pool.connection() as conn, conn.transaction():
            cur = conn.execute(
                "DELETE FROM want_items WHERE id = %s AND tenant_id = %s",
                (want_id, tenant_id),
            )
            return cur.rowcount > 0

    # Hold operations
    def create_hold(self, hold: CollectorHold) -> CollectorHold:
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO collector_holds (id, tenant_id, sku, want_item_id, collector_id, held_at, expires_at, status, notes, book_title, book_author, collector_name, price_cad, claimed_at, released_at, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    hold.id,
                    hold.tenant_id,
                    hold.sku,
                    hold.want_item_id,
                    hold.collector_id,
                    hold.held_at,
                    hold.expires_at,
                    hold.status,
                    hold.notes,
                    hold.book_title,
                    hold.book_author,
                    hold.collector_name,
                    hold.price_cad,
                    hold.claimed_at,
                    hold.released_at,
                    hold.created_at,
                ),
            )
        return hold

    def get_hold(self, hold_id: str, tenant_id: int = 1) -> CollectorHold | None:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "SELECT * FROM collector_holds WHERE id = %s AND tenant_id = %s",
                (hold_id, tenant_id),
            )
            row = cur.fetchone()
            return _row_to_hold(row) if row else None

    def get_active_hold_for_sku(self, sku: str, tenant_id: int = 1) -> CollectorHold | None:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "SELECT * FROM collector_holds WHERE sku = %s AND tenant_id = %s AND status = 'active'",
                (sku, tenant_id),
            )
            row = cur.fetchone()
            return _row_to_hold(row) if row else None

    def list_holds(self, tenant_id: int = 1, status: str | None = None) -> list[CollectorHold]:
        query = "SELECT * FROM collector_holds WHERE tenant_id = %s"
        params: list[Any] = [tenant_id]
        if status:
            query += " AND status = %s"
            params.append(status)
        query += " ORDER BY held_at DESC"
        with self.pool.connection() as conn:
            cur = conn.execute(query, params)
            return [_row_to_hold(r) for r in cur.fetchall()]

    def update_hold(self, hold_id: str, tenant_id: int = 1, **kwargs: Any) -> CollectorHold | None:
        current = self.get_hold(hold_id, tenant_id=tenant_id)
        if not current:
            return None
        allowed = {"status", "notes", "claimed_at", "released_at", "expires_at"}
        updates: dict[str, Any] = {}
        for k, v in kwargs.items():
            if k in allowed:
                updates[k] = v
        if not updates:
            return current
        clause = ", ".join(f"{k} = %s" for k in updates)
        params = list(updates.values()) + [hold_id, tenant_id]
        with self.pool.connection() as conn, conn.transaction():
            conn.execute(f"UPDATE collector_holds SET {clause} WHERE id = %s AND tenant_id = %s", params)
        return self.get_hold(hold_id, tenant_id=tenant_id)
