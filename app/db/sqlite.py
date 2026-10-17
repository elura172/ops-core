from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.domain.order_events import ApplyOutcome, Outcome, Resolution, classify
from app.domain.order_store import OrderEvent
from app.domain.orders import Order, OrderStatus
from app.observability.logging import get_logger

logger = get_logger("sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
  order_id TEXT PRIMARY KEY,
  tenant_id INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  status TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS processed_events (
  event_id TEXT PRIMARY KEY,
  tenant_id INTEGER NOT NULL DEFAULT 1,
  order_id TEXT NOT NULL,
  status TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  outcome TEXT NOT NULL DEFAULT 'applied'
);
CREATE TABLE IF NOT EXISTS quarantined_events (
  event_id TEXT PRIMARY KEY,
  tenant_id INTEGER NOT NULL DEFAULT 1,
  order_id TEXT NOT NULL,
  observed_status TEXT NOT NULL,
  local_status TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  resolved INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_processed_events_order ON processed_events(order_id);
"""


class SQLiteOrderRepository:
    """Durable local repository with the same semantics as OrderStore.

    Every event is folded inside a single ``BEGIN IMMEDIATE`` transaction, so the
    duplicate check and the write cannot interleave with another writer, process,
    or worker thread.
    """

    def __init__(self, path: str | Path) -> None:
        if str(path) == ":memory:":
            logger.warning("sqlite.memory_database", detail="order state will not survive restart")
        self.connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 30000")
        if str(path) != ":memory:":
            self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.executescript(SCHEMA)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(orders)")}
            if "tenant_id" not in columns:
                self.connection.execute("ALTER TABLE orders ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1")
                self.connection.execute("ALTER TABLE processed_events ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1")
                self.connection.execute(
                    "ALTER TABLE quarantined_events ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1"
                )
            if "created_at_estimated" not in columns:
                # Legacy timestamps have no provenance; conservatively exclude them.
                self.connection.execute("ALTER TABLE orders ADD COLUMN created_at_estimated INTEGER NOT NULL DEFAULT 1")
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def apply(self, event: OrderEvent, tenant_id: int = 1) -> ApplyOutcome:
        if not event.event_id or not event.order_id:
            raise ValueError("event_id and order_id are required")
        occurred_at = event.occurred_at.astimezone(UTC).isoformat()
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                outcome = self._apply_locked(event, occurred_at, tenant_id)
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise
            self.connection.execute("COMMIT")
        return outcome

    def _apply_locked(self, event: OrderEvent, occurred_at: str, tenant_id: int = 1) -> ApplyOutcome:
        seen = self.connection.execute(
            "SELECT 1 FROM processed_events WHERE event_id = ? AND tenant_id = ?",
            (event.event_id, tenant_id),
        ).fetchone()
        if seen:
            return ApplyOutcome(Outcome.DUPLICATE, event.order_id)

        row = self.connection.execute(
            "SELECT status FROM orders WHERE order_id = ? AND tenant_id = ?",
            (event.order_id, tenant_id),
        ).fetchone()
        if row is None:
            current = OrderStatus.RECEIVED
            self.connection.execute(
                "INSERT INTO orders(order_id, tenant_id, created_at, status, updated_at, "
                "created_at_estimated) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.order_id,
                    tenant_id,
                    occurred_at,
                    current.value,
                    occurred_at,
                    bool(classify(current, event.status).skipped),
                ),
            )
        else:
            current = OrderStatus(row["status"])

        decision = classify(current, event.status)
        outcome = {
            Resolution.APPLY: Outcome.APPLIED,
            Resolution.UNCHANGED: Outcome.UNCHANGED,
            Resolution.STALE: Outcome.STALE,
            Resolution.CONFLICT: Outcome.QUARANTINED,
        }[decision.resolution]

        self.connection.execute(
            "INSERT INTO processed_events(event_id, tenant_id, order_id, status, occurred_at, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                tenant_id,
                event.order_id,
                event.status.value,
                occurred_at,
                outcome.value,
            ),
        )

        if decision.resolution is Resolution.CONFLICT:
            self.connection.execute(
                "INSERT INTO quarantined_events"
                "(event_id, tenant_id, order_id, observed_status, local_status, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.event_id,
                    tenant_id,
                    event.order_id,
                    event.status.value,
                    current.value,
                    occurred_at,
                ),
            )
            logger.error(
                "order.event.quarantined",
                order_id=event.order_id,
                event_id=event.event_id,
                current=current.value,
                incoming=event.status.value,
            )
            return ApplyOutcome(Outcome.QUARANTINED, event.order_id, current)

        if decision.resolution is not Resolution.APPLY:
            if decision.resolution is Resolution.STALE:
                logger.info(
                    "order.event.stale",
                    order_id=event.order_id,
                    event_id=event.event_id,
                    current=current.value,
                    incoming=event.status.value,
                )
            return ApplyOutcome(outcome, event.order_id, current)

        self.connection.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ? AND tenant_id = ?",
            (event.status.value, occurred_at, event.order_id, tenant_id),
        )
        if decision.skipped:
            logger.warning(
                "order.event.fast_forwarded",
                order_id=event.order_id,
                event_id=event.event_id,
                skipped=[status.value for status in decision.skipped],
            )
        return ApplyOutcome(Outcome.APPLIED, event.order_id, event.status, decision.skipped)

    def active(self, tenant_id: int = 1) -> list[Order]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT * FROM orders WHERE status NOT IN ('DELIVERED', 'CANCELLED') AND tenant_id = ? "
                "ORDER BY created_at, order_id",
                (tenant_id,),
            ).fetchall()
        return [
            Order(
                order_id=row["order_id"],
                created_at=datetime.fromisoformat(row["created_at"]),
                status=OrderStatus(row["status"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                created_at_estimated=bool(row["created_at_estimated"]),
            )
            for row in rows
        ]

    def quarantined(self, tenant_id: int = 1, *, include_resolved: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM quarantined_events WHERE tenant_id = ?"
        params = [tenant_id]
        if not include_resolved:
            query += " AND resolved = 0"
        with self._lock:
            rows = self.connection.execute(query + " ORDER BY occurred_at", params).fetchall()
        return [dict(row) for row in rows]

    def resolve_quarantined(self, event_id: str, tenant_id: int = 1) -> bool:
        """Mark a conflicting event as handled. Returns False if it was not open."""
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = self.connection.execute(
                    "UPDATE quarantined_events SET resolved = 1 WHERE event_id = ? AND tenant_id = ? AND resolved = 0",
                    (event_id, tenant_id),
                )
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise
            self.connection.execute("COMMIT")
        return cursor.rowcount == 1

    def close(self) -> None:
        self.connection.close()
