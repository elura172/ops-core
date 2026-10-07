"""Postgres-backed review store for multi-process deployments.

The SQLite store serializes writers with one connection behind one lock, so its
guarantees only hold inside a single process. Here every worker gets its own
pooled connection and the at-most-once guarantee rests on one statement: the
conditional ``UPDATE ... WHERE status = 'APPROVED'`` in ``claim_for_execution``.
Under READ COMMITTED a losing racer blocks on the winner's row lock, re-evaluates
the predicate after the winner commits, matches zero rows, and is refused.
``tests/test_review_contention.py`` proves this under contention and proves the
guard is load-bearing by removing it and watching the double-fire.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.domain.audit import AuditEvent
from app.observability.logging import get_logger
from app.review.queue import AlreadyExecuted, ReviewItem, ReviewStatus, _merge_payload, _validate_claim

logger = get_logger("review_store")


def _row_to_item(row: dict[str, Any]) -> ReviewItem:
    return ReviewItem(
        review_id=row["review_id"],
        operation=row["operation"],
        payload=row["payload"],
        status=ReviewStatus(row["status"]),
        reviewer=row["reviewer"],
        reason=row["reason"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        executed_by=row["executed_by"],
        executed_at=row["executed_at"],
        result=row["result"],
        failure=row["failure"],
    )


class PostgresReviewStore:
    """Postgres-backed review store."""

    def __init__(self, dsn: str, *, pool_size: int = 3) -> None:
        self.pool = ConnectionPool(
            dsn,
            min_size=1,
            max_size=pool_size,
            max_idle=60.0,
            timeout=30.0,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )

    @contextmanager
    def _transaction(self) -> Iterator[psycopg.Connection[DictRow]]:
        with self.pool.connection() as connection:
            with connection.transaction():
                yield connection

    def _fetch(self, connection: psycopg.Connection[DictRow], review_id: str, tenant_id: int = 1) -> ReviewItem:
        row = connection.execute(
            "SELECT * FROM reviews WHERE review_id = %s AND tenant_id = %s",
            (review_id, tenant_id),
        ).fetchone()
        if row is None:
            raise KeyError(review_id)
        return _row_to_item(row)

    def submit(self, item: ReviewItem, tenant_id: int = 1) -> ReviewItem:
        with self.pool.connection() as connection:
            connection.execute(
                "INSERT INTO reviews (review_id, tenant_id, operation, payload, status, reviewer, reason, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    item.review_id,
                    tenant_id,
                    item.operation,
                    Jsonb(_json_safe(item.payload)),
                    item.status.value,
                    item.reviewer,
                    item.reason,
                    item.created_at,
                ),
            )
        return item

    def get(self, review_id: str, tenant_id: int = 1) -> ReviewItem | None:
        with self.pool.connection() as connection:
            connection.row_factory = dict_row
            row = connection.execute(
                "SELECT * FROM reviews WHERE review_id = %s AND tenant_id = %s",
                (review_id, tenant_id),
            ).fetchone()
        return _row_to_item(row) if row else None

    def list_items(self, pending_only: bool = False, tenant_id: int = 1) -> list[ReviewItem]:
        query = "SELECT * FROM reviews WHERE tenant_id = %s"
        params: list[Any] = [tenant_id]
        if pending_only:
            query += " AND status = %s"
            params.append(ReviewStatus.PENDING.value)
        with self.pool.connection() as connection:
            connection.row_factory = dict_row
            rows = connection.execute(query + " ORDER BY created_at", tuple(params)).fetchall()
        return [_row_to_item(row) for row in rows]

    def pending(self, tenant_id: int = 1) -> list[ReviewItem]:
        return self.list_items(pending_only=True, tenant_id=tenant_id)

    def decide(
        self,
        review_id: str,
        *,
        approve: bool,
        reviewer: str,
        reason: str = "",
        payload_update: dict[str, Any] | None = None,
        tenant_id: int = 1,
    ) -> ReviewItem:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        status = ReviewStatus.APPROVED if approve else ReviewStatus.REJECTED
        with self._transaction() as connection:
            item = self._fetch(connection, review_id, tenant_id)
            if item.status != ReviewStatus.PENDING:
                raise ValueError("review item has already been decided")
            if payload_update:
                _merge_payload(item.payload, payload_update)
                cursor = connection.execute(
                    "UPDATE reviews SET status = %s, reviewer = %s, reason = %s, decided_at = %s, payload = %s "
                    "WHERE review_id = %s AND tenant_id = %s AND status = %s",
                    (
                        status.value,
                        reviewer,
                        reason,
                        datetime.now(UTC),
                        Jsonb(_json_safe(item.payload)),
                        review_id,
                        tenant_id,
                        ReviewStatus.PENDING.value,
                    ),
                )
            else:
                cursor = connection.execute(
                    "UPDATE reviews SET status = %s, reviewer = %s, reason = %s, decided_at = %s "
                    "WHERE review_id = %s AND tenant_id = %s AND status = %s",
                    (
                        status.value,
                        reviewer,
                        reason,
                        datetime.now(UTC),
                        review_id,
                        tenant_id,
                        ReviewStatus.PENDING.value,
                    ),
                )
            if cursor.rowcount != 1:
                raise ValueError("review item has already been decided")
            self._record(
                connection,
                AuditEvent(
                    "review.decide",
                    reviewer,
                    status.value.lower(),
                    review_id,
                    {"operation": item.operation},
                ),
                tenant_id=tenant_id,
            )
            return self._fetch(connection, review_id, tenant_id)

    def claim_for_execution(self, review_id: str, actor: str, tenant_id: int = 1) -> ReviewItem:
        with self._transaction() as connection:
            item = self._fetch(connection, review_id, tenant_id)
            _validate_claim(item)
            cursor = connection.execute(
                "UPDATE reviews SET status = %s, executed_by = %s, executed_at = %s "
                "WHERE review_id = %s AND tenant_id = %s AND status = %s",
                (
                    ReviewStatus.EXECUTING.value,
                    actor,
                    datetime.now(UTC),
                    review_id,
                    tenant_id,
                    ReviewStatus.APPROVED.value,
                ),
            )
            if cursor.rowcount != 1:
                lost = self._fetch(connection, review_id, tenant_id)
                _validate_claim(lost)
                raise AlreadyExecuted(lost)
            return self._fetch(connection, review_id, tenant_id)

    def complete_execution(self, review_id: str, result: dict[str, Any], tenant_id: int = 1) -> ReviewItem:
        with self._transaction() as connection:
            item = self._fetch(connection, review_id, tenant_id)
            connection.execute(
                "UPDATE reviews SET status = %s, result = %s, executed_at = %s "
                "WHERE review_id = %s AND tenant_id = %s",
                (
                    ReviewStatus.EXECUTED.value,
                    Jsonb(_json_safe(result)),
                    datetime.now(UTC),
                    review_id,
                    tenant_id,
                ),
            )
            self._record(
                connection,
                AuditEvent(
                    "approved_action.execute",
                    item.executed_by or "unknown",
                    "completed",
                    review_id,
                    {"operation": item.operation, "approved_by": item.reviewer or "unknown"},
                ),
                tenant_id=tenant_id,
            )
            return self._fetch(connection, review_id, tenant_id)

    def fail_execution(self, review_id: str, error: str, tenant_id: int = 1) -> ReviewItem:
        with self._transaction() as connection:
            item = self._fetch(connection, review_id, tenant_id)
            connection.execute(
                "UPDATE reviews SET status = %s, failure = %s, executed_at = %s "
                "WHERE review_id = %s AND tenant_id = %s",
                (ReviewStatus.FAILED.value, error, datetime.now(UTC), review_id, tenant_id),
            )
            self._record(
                connection,
                AuditEvent(
                    "approved_action.execute",
                    item.executed_by or "unknown",
                    "failed",
                    review_id,
                    {"operation": item.operation},
                ),
                tenant_id=tenant_id,
            )
            return self._fetch(connection, review_id, tenant_id)

    def recover_stale_executions(self, older_than: timedelta) -> list[ReviewItem]:
        cutoff = datetime.now(UTC) - older_than
        with self._transaction() as connection:
            rows = connection.execute(
                "UPDATE reviews SET status = %s, failure = %s "
                "WHERE status = %s AND executed_at < %s "
                "RETURNING *",
                (
                    ReviewStatus.FAILED.value,
                    "execution did not complete; outcome unknown",
                    ReviewStatus.EXECUTING.value,
                    cutoff,
                ),
            ).fetchall()
            for row in rows:
                self._record(
                    connection,
                    AuditEvent(
                        "approved_action.recover",
                        "system",
                        "failed",
                        row["review_id"],
                        {"operation": row["operation"]},
                    ),
                    tenant_id=row.get("tenant_id", 1),
                )
            return [_row_to_item(row) for row in rows]

    def _record(self, connection: psycopg.Connection[DictRow], event: AuditEvent, tenant_id: int = 1) -> None:
        connection.execute(
            "INSERT INTO audit_events (tenant_id, action, actor, outcome, resource_id, detail, occurred_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                tenant_id,
                event.action,
                event.actor,
                event.outcome,
                event.resource_id,
                Jsonb(event.detail) if event.detail else None,
                event.occurred_at.astimezone(UTC),
            ),
        )

    def audit_trail(self, resource_id: str | None = None, tenant_id: int = 1) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit_events WHERE tenant_id = %s"
        params: list[Any] = [tenant_id]
        if resource_id:
            query += " AND resource_id = %s"
            params.append(resource_id)
        with self.pool.connection() as connection:
            connection.row_factory = dict_row
            rows = connection.execute(query + " ORDER BY sequence", tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def get_tenant_analytics(self, tenant_id: int) -> dict[str, Any]:
        with self.pool.connection() as connection:
            connection.row_factory = dict_row
            rows = connection.execute(
                "SELECT operation, payload, status FROM reviews WHERE tenant_id = %s",
                (tenant_id,),
            ).fetchall()

        total_actions = 0
        syncs = 0
        contentions_prevented = 0
        revenue_protected_cad = 0.0

        for row in rows:
            if row["status"] in ("APPROVED", "EXECUTING", "EXECUTED"):
                total_actions += 1
                op = row["operation"]
                if op == "sync_inventory":
                    syncs += 1
                elif op == "resolve_order_contention":
                    contentions_prevented += 1
                    try:
                        payload = row["payload"] if isinstance(row["payload"], dict) else {}
                        loser = payload.get("loser_claim", {})
                        winner = payload.get("winner_claim", {})
                        val = float(winner.get("price_cad", loser.get("price_cad", 0)))
                        revenue_protected_cad += val
                    except Exception:
                        pass

        return {
            "total_automated_actions": total_actions,
            "inventory_syncs": syncs,
            "collisions_prevented": contentions_prevented,
            "revenue_protected_cad": round(revenue_protected_cad, 2),
        }

    def close(self) -> None:
        self.pool.close()


def _json_safe(value: dict[str, Any]) -> dict[str, Any]:
    """Match the SQLite store, which serializes results with ``default=str``."""
    import json
    return json.loads(json.dumps(value, default=str))
