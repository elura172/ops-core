"""Postgres-backed order repository for multi-process deployments.

The SQLite repository serializes every writer behind ``BEGIN IMMEDIATE``. Here
two locks replace that single one:

- the ``processed_events`` primary key is the duplicate guard. A replayed or
  raced event loses on the conflict of its INSERT, never on anything a worker
  read earlier.
- ``SELECT ... FOR UPDATE`` on the order row serializes classification per
  order, so two different events for the same order cannot both classify
  against the same stale status and overwrite each other's fold.

``tests/test_order_contention.py`` proves both under contention, including the
regression each guard exists to prevent.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from app.domain.order_events import ApplyOutcome, Outcome, Resolution, classify
from app.domain.order_store import OrderEvent
from app.domain.orders import Order, OrderStatus
from app.observability.logging import get_logger

logger = get_logger("postgres_orders")


class PostgresOrderRepository:
    """Durable order state with the same semantics as OrderStore."""

    def __init__(self, dsn: str, *, pool_size: int = 3) -> None:
        self.pool: ConnectionPool[psycopg.Connection[DictRow]] = ConnectionPool(
            dsn,
            min_size=1,
            max_size=pool_size,
            max_idle=60.0,
            timeout=30.0,
            kwargs={"row_factory": dict_row},
            open=True,
        )

    def apply(self, event: OrderEvent, tenant_id: int = 1) -> ApplyOutcome:
        if not event.event_id or not event.order_id:
            raise ValueError("event_id and order_id are required")
        occurred_at = event.occurred_at.astimezone(UTC)
        with self.pool.connection() as connection, connection.transaction():
            # Fast path for replays: skip the locks entirely.
            seen = connection.execute(
                "SELECT 1 FROM processed_events WHERE event_id = %s AND tenant_id = %s",
                (event.event_id, tenant_id),
            ).fetchone()
            if seen:
                return ApplyOutcome(Outcome.DUPLICATE, event.order_id)

            connection.execute(
                "INSERT INTO orders (order_id, tenant_id, created_at, status, updated_at, "
                "created_at_estimated) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (order_id) DO NOTHING",
                (
                    event.order_id,
                    tenant_id,
                    occurred_at,
                    OrderStatus.RECEIVED.value,
                    occurred_at,
                    bool(classify(OrderStatus.RECEIVED, event.status).skipped),
                ),
            )
            row = connection.execute(
                "SELECT status FROM orders WHERE order_id = %s AND tenant_id = %s FOR UPDATE",
                (event.order_id, tenant_id),
            ).fetchone()
            assert row is not None
            current = OrderStatus(row["status"])

            decision = classify(current, event.status)
            outcome = {
                Resolution.APPLY: Outcome.APPLIED,
                Resolution.UNCHANGED: Outcome.UNCHANGED,
                Resolution.STALE: Outcome.STALE,
                Resolution.CONFLICT: Outcome.QUARANTINED,
            }[decision.resolution]

            cursor = connection.execute(
                "INSERT INTO processed_events (event_id, tenant_id, order_id, status, occurred_at, outcome) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING",
                (
                    event.event_id,
                    tenant_id,
                    event.order_id,
                    event.status.value,
                    occurred_at,
                    outcome.value,
                ),
            )
            if cursor.rowcount != 1:
                return ApplyOutcome(Outcome.DUPLICATE, event.order_id)

            if decision.resolution is Resolution.CONFLICT:
                connection.execute(
                    "INSERT INTO quarantined_events "
                    "(event_id, tenant_id, order_id, observed_status, local_status, occurred_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
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
                    tenant_id=tenant_id,
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
                        tenant_id=tenant_id,
                        event_id=event.event_id,
                        current=current.value,
                        incoming=event.status.value,
                    )
                return ApplyOutcome(outcome, event.order_id, current)

            connection.execute(
                "UPDATE orders SET status = %s, updated_at = %s WHERE order_id = %s AND tenant_id = %s",
                (event.status.value, occurred_at, event.order_id, tenant_id),
            )
            if decision.skipped:
                logger.warning(
                    "order.event.fast_forwarded",
                    order_id=event.order_id,
                    tenant_id=tenant_id,
                    event_id=event.event_id,
                    skipped=[status.value for status in decision.skipped],
                )
            return ApplyOutcome(Outcome.APPLIED, event.order_id, event.status, decision.skipped)

    def active(self, tenant_id: int = 1) -> list[Order]:
        with self.pool.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM orders WHERE tenant_id = %s AND status NOT IN ('DELIVERED', 'CANCELLED') "
                "ORDER BY created_at, order_id",
                (tenant_id,),
            ).fetchall()
        return [
            Order(
                order_id=row["order_id"],
                created_at=row["created_at"],
                status=OrderStatus(row["status"]),
                updated_at=row["updated_at"],
                created_at_estimated=row["created_at_estimated"],
            )
            for row in rows
        ]

    def quarantined(self, tenant_id: int = 1, *, include_resolved: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM quarantined_events WHERE tenant_id = %s"
        params: list[Any] = [tenant_id]
        if not include_resolved:
            query += " AND resolved = 0"
        with self.pool.connection() as connection:
            rows = connection.execute(query + " ORDER BY occurred_at", tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def resolve_quarantined(self, event_id: str, tenant_id: int = 1) -> bool:
        with self.pool.connection() as connection, connection.transaction():
            cursor = connection.execute(
                "UPDATE quarantined_events SET resolved = 1 WHERE event_id = %s AND tenant_id = %s AND resolved = 0",
                (event_id, tenant_id),
            )
            return cursor.rowcount == 1

    def close(self) -> None:
        self.pool.close()
