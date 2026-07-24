from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.domain.order_store import OrderEvent, OrderStore
from app.domain.orders import OrderStatus


class SQLiteOrderRepository:
    """Durable local repository with the same semantics as OrderStore."""

    def __init__(self, path: str | Path) -> None:
        self.connection = sqlite3.connect(str(path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS orders (
              order_id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              status TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS processed_events (
              event_id TEXT PRIMARY KEY,
              order_id TEXT NOT NULL,
              status TEXT NOT NULL,
              occurred_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def apply(self, event: OrderEvent) -> bool:
        existing = self.connection.execute(
            "SELECT 1 FROM processed_events WHERE event_id = ?", (event.event_id,)
        ).fetchone()
        if existing:
            return False
        row = self.connection.execute(
            "SELECT status, created_at FROM orders WHERE order_id = ?", (event.order_id,)
        ).fetchone()
        if row is None:
            created_at = event.occurred_at.astimezone(timezone.utc).isoformat()
            current = OrderStatus.RECEIVED
            self.connection.execute(
                "INSERT INTO orders(order_id, created_at, status, updated_at) VALUES (?, ?, ?, ?)",
                (event.order_id, created_at, current.value, created_at),
            )
        else:
            current = OrderStatus(row["status"])
        if event.status != current:
            from app.domain.orders import ALLOWED_TRANSITIONS
            if event.status not in ALLOWED_TRANSITIONS[current]:
                self.connection.rollback()
                raise ValueError(f"invalid order transition: {current} -> {event.status}")
        updated = event.occurred_at.astimezone(timezone.utc).isoformat()
        self.connection.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ?",
            (event.status.value, updated, event.order_id),
        )
        self.connection.execute(
            "INSERT INTO processed_events(event_id, order_id, status, occurred_at) VALUES (?, ?, ?, ?)",
            (event.event_id, event.order_id, event.status.value, updated),
        )
        self.connection.commit()
        return True

    def close(self) -> None:
        self.connection.close()
