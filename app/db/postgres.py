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
from app.review.queue import AlreadyExecuted, ReviewItem, ReviewStatus, _validate_claim

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
    """Durable review state shared by any number of worker processes.

    Every transition is a single transaction whose write carries its own
    ``WHERE status = ...`` predicate, so correctness never depends on what a
    worker read a moment earlier. The audit trail is written in the same
    transaction as the state change it describes.
    """

    def __init__(self, dsn: str, *, pool_size: int = 10) -> None:
        self.pool: ConnectionPool[psycopg.Connection[DictRow]] = ConnectionPool(
            dsn,
            min_size=1,
            max_size=pool_size,
            kwargs={"row_factory": dict_row},
            open=True,
        )

    @contextmanager
    def _transaction(self) -> Iterator[psycopg.Connection[DictRow]]:
        with self.pool.connection() as connection, connection.transaction():
            yield connection

    def _fetch(self, connection: psycopg.Connection[DictRow], review_id: str) -> ReviewItem:
        row = connection.execute(
            "SELECT * FROM reviews WHERE review_id = %s", (review_id,)
        ).fetchone()
        if row is None:
            raise KeyError(review_id)
        return _row_to_item(row)

    def submit(self, item: ReviewItem) -> ReviewItem:
        if item.status != ReviewStatus.PENDING:
            raise ValueError("new review items must be pending")
        with self._transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO reviews (review_id, operation, payload, status, created_at) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (review_id) DO NOTHING",
                (
                    item.review_id,
                    item.operation,
                    Jsonb(item.payload),
                    item.status.value,
                    item.created_at.astimezone(UTC),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("review_id already exists")
        return item

    def get(self, review_id: str) -> ReviewItem | None:
        with self.pool.connection() as connection:
            connection.row_factory = dict_row
            row = connection.execute(
                "SELECT * FROM reviews WHERE review_id = %s", (review_id,)
            ).fetchone()
        return _row_to_item(row) if row else None

    def list_items(self, pending_only: bool = False) -> list[ReviewItem]:
        query = "SELECT * FROM reviews"
        params: tuple[str, ...] = ()
        if pending_only:
            query += " WHERE status = %s"
            params = (ReviewStatus.PENDING.value,)
        with self.pool.connection() as connection:
            connection.row_factory = dict_row
            rows = connection.execute(query + " ORDER BY created_at", params).fetchall()
        return [_row_to_item(row) for row in rows]

    def pending(self) -> list[ReviewItem]:
        return self.list_items(pending_only=True)

    def decide(
        self, review_id: str, *, approve: bool, reviewer: str, reason: str = "", payload_update: dict[str, Any] | None = None
    ) -> ReviewItem:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        status = ReviewStatus.APPROVED if approve else ReviewStatus.REJECTED
        with self._transaction() as connection:
            item = self._fetch(connection, review_id)
            if item.status != ReviewStatus.PENDING:
                raise ValueError("review item has already been decided")
            if payload_update:
                _merge_payload(item.payload, payload_update)
                cursor = connection.execute(
                    "UPDATE reviews SET status = %s, reviewer = %s, reason = %s, decided_at = %s, payload = %s "
                    "WHERE review_id = %s AND status = %s",
                    (
                        status.value,
                        reviewer,
                        reason,
                        datetime.now(UTC),
                        Jsonb(_json_safe(item.payload)),
                        review_id,
                        ReviewStatus.PENDING.value,
                    ),
                )
            else:
                cursor = connection.execute(
                    "UPDATE reviews SET status = %s, reviewer = %s, reason = %s, decided_at = %s "
                    "WHERE review_id = %s AND status = %s",
                    (
                        status.value,
                        reviewer,
                        reason,
                        datetime.now(UTC),
                        review_id,
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
            )
            return self._fetch(connection, review_id)

    def claim_for_execution(self, review_id: str, actor: str) -> ReviewItem:
        with self._transaction() as connection:
            # This read is advisory only -- it exists to raise the precise error
            # for items that are plainly unclaimable. It proves nothing about
            # what the row will be when we write.
            item = self._fetch(connection, review_id)
            _validate_claim(item)
            # The conditional UPDATE is the entire at-most-once guarantee. A
            # racer that read APPROVED above blocks here on the winner's row
            # lock, re-evaluates the predicate once the winner commits, matches
            # zero rows, and loses.
            cursor = connection.execute(
                "UPDATE reviews SET status = %s, executed_by = %s, executed_at = %s "
                "WHERE review_id = %s AND status = %s",
                (
                    ReviewStatus.EXECUTING.value,
                    actor,
                    datetime.now(UTC),
                    review_id,
                    ReviewStatus.APPROVED.value,
                ),
            )
            if cursor.rowcount != 1:
                # Re-read on the post-commit snapshot so the loser is told what
                # actually happened (already executing, executed, or failed).
                lost = self._fetch(connection, review_id)
                _validate_claim(lost)
                raise AlreadyExecuted(lost)
            return self._fetch(connection, review_id)

    def complete_execution(self, review_id: str, result: dict[str, Any]) -> ReviewItem:
        with self._transaction() as connection:
            item = self._fetch(connection, review_id)
            connection.execute(
                "UPDATE reviews SET status = %s, result = %s, executed_at = %s "
                "WHERE review_id = %s",
                (
                    ReviewStatus.EXECUTED.value,
                    Jsonb(_json_safe(result)),
                    datetime.now(UTC),
                    review_id,
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
            )
            return self._fetch(connection, review_id)

    def fail_execution(self, review_id: str, error: str) -> ReviewItem:
        with self._transaction() as connection:
            item = self._fetch(connection, review_id)
            connection.execute(
                "UPDATE reviews SET status = %s, failure = %s, executed_at = %s "
                "WHERE review_id = %s",
                (ReviewStatus.FAILED.value, error, datetime.now(UTC), review_id),
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
            )
            return self._fetch(connection, review_id)

    def recover_stale_executions(self, older_than: timedelta) -> list[ReviewItem]:
        """Move abandoned EXECUTING items to FAILED so they are not stuck forever.

        The outcome of an interrupted mutation is unknown, so recovery means a
        human re-approves rather than an automatic retry.
        """
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
                )
            return [_row_to_item(row) for row in rows]

    def _record(self, connection: psycopg.Connection[DictRow], event: AuditEvent) -> None:
        connection.execute(
            "INSERT INTO audit_events (action, actor, outcome, resource_id, detail, occurred_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (
                event.action,
                event.actor,
                event.outcome,
                event.resource_id,
                Jsonb(event.detail) if event.detail else None,
                event.occurred_at.astimezone(UTC),
            ),
        )

    def audit_trail(self, resource_id: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit_events"
        params: tuple[str, ...] = ()
        if resource_id:
            query += " WHERE resource_id = %s"
            params = (resource_id,)
        with self.pool.connection() as connection:
            connection.row_factory = dict_row
            rows = connection.execute(query + " ORDER BY sequence", params).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self.pool.close()


def _json_safe(value: dict[str, Any]) -> dict[str, Any]:
    """Match the SQLite store, which serializes results with ``default=str``."""
    import json

    return json.loads(json.dumps(value, default=str))  # type: ignore[no-any-return]
