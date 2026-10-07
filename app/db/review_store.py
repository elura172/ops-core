from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.domain.audit import AuditEvent
from app.observability.logging import get_logger
from app.review.queue import AlreadyExecuted, ReviewItem, ReviewStatus, _validate_claim

logger = get_logger("review_store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
  review_id TEXT PRIMARY KEY,
  tenant_id INTEGER NOT NULL DEFAULT 1,
  operation TEXT NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL,
  reviewer TEXT,
  reason TEXT,
  created_at TEXT NOT NULL,
  decided_at TEXT,
  executed_by TEXT,
  executed_at TEXT,
  result TEXT,
  failure TEXT
);
CREATE TABLE IF NOT EXISTS audit_events (
  sequence INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id INTEGER NOT NULL DEFAULT 1,
  action TEXT NOT NULL,
  actor TEXT NOT NULL,
  outcome TEXT NOT NULL,
  resource_id TEXT,
  detail TEXT,
  occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_status ON reviews(status);
"""


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_item(row: sqlite3.Row) -> ReviewItem:
    return ReviewItem(
        review_id=row["review_id"],
        operation=row["operation"],
        payload=json.loads(row["payload"]),
        status=ReviewStatus(row["status"]),
        reviewer=row["reviewer"],
        reason=row["reason"],
        created_at=_dt(row["created_at"]) or datetime.now(UTC),
        decided_at=_dt(row["decided_at"]),
        executed_by=row["executed_by"],
        executed_at=_dt(row["executed_at"]),
        result=json.loads(row["result"]) if row["result"] else None,
        failure=row["failure"],
    )


class SQLiteReviewStore:
    """Durable review state.

    Every transition runs inside ``BEGIN IMMEDIATE`` and claims are conditional
    ``UPDATE ... WHERE status = 'APPROVED'`` statements, so two workers racing the
    same approval cannot both execute it. The audit trail is written in the same
    transaction as the state change it describes.
    """

    def __init__(self, path: str | Path) -> None:
        if str(path) == ":memory:":
            logger.warning("reviews.memory_database", detail="approvals will not survive restart")
        self.connection = sqlite3.connect(
            str(path), check_same_thread=False, isolation_level=None, timeout=30
        )
        self.connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.connection.execute("PRAGMA busy_timeout = 30000")
        if str(path) != ":memory:":
            self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.executescript(SCHEMA)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(reviews)")}
            if "tenant_id" not in columns:
                self.connection.execute("ALTER TABLE reviews ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1")
                self.connection.execute("ALTER TABLE audit_events ADD COLUMN tenant_id INTEGER NOT NULL DEFAULT 1")
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise
            self.connection.execute("COMMIT")

    def _fetch(self, connection: sqlite3.Connection, review_id: str, tenant_id: int = 1) -> ReviewItem:
        row = connection.execute(
            "SELECT * FROM reviews WHERE review_id = ? AND tenant_id = ?", (review_id, tenant_id)
        ).fetchone()
        if row is None:
            raise KeyError(review_id)
        return _row_to_item(row)

    def submit(self, item: ReviewItem, tenant_id: int = 1) -> ReviewItem:
        if item.status != ReviewStatus.PENDING:
            raise ValueError("new review items must be pending")
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT 1 FROM reviews WHERE review_id = ? AND tenant_id = ?", (item.review_id, tenant_id)
            ).fetchone()
            if existing:
                raise ValueError("review_id already exists")
            connection.execute(
                "INSERT INTO reviews(review_id, operation, payload, status, created_at, tenant_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    item.review_id,
                    item.operation,
                    json.dumps(item.payload, sort_keys=True),
                    item.status.value,
                    item.created_at.astimezone(UTC).isoformat(),
                    tenant_id,
                ),
            )
        return item

    def get(self, review_id: str, tenant_id: int = 1) -> ReviewItem | None:
        # Reads share the connection with in-flight transactions, so they take the
        # same lock; sqlite3 connections are not safe for concurrent use.
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM reviews WHERE review_id = ? AND tenant_id = ?", (review_id, tenant_id)
            ).fetchone()
        return _row_to_item(row) if row else None

    def list_items(self, pending_only: bool = False, tenant_id: int = 1) -> list[ReviewItem]:
        query = "SELECT * FROM reviews WHERE tenant_id = ?"
        params: tuple[int | str, ...] = (tenant_id,)
        if pending_only:
            query += " AND status = ?"
            params = (tenant_id, ReviewStatus.PENDING.value)
        with self._lock:
            rows = self.connection.execute(query + " ORDER BY created_at", params).fetchall()
        return [_row_to_item(row) for row in rows]

    def pending(self, tenant_id: int = 1) -> list[ReviewItem]:
        return self.list_items(pending_only=True, tenant_id=tenant_id)

    def decide(
        self, review_id: str, *, approve: bool, reviewer: str, reason: str = "", payload_update: dict[str, Any] | None = None, tenant_id: int = 1
    ) -> ReviewItem:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        status = ReviewStatus.APPROVED if approve else ReviewStatus.REJECTED
        decided_at = datetime.now(UTC)
        with self._transaction() as connection:
            item = self._fetch(connection, review_id, tenant_id)
            if item.status != ReviewStatus.PENDING:
                raise ValueError("review item has already been decided")
            if payload_update:
                _merge_payload(item.payload, payload_update)
                connection.execute(
                    "UPDATE reviews SET status = ?, reviewer = ?, reason = ?, decided_at = ?, payload = ? "
                    "WHERE review_id = ? AND status = ?",
                    (
                        status.value,
                        reviewer,
                        reason,
                        decided_at.isoformat(),
                        json.dumps(item.payload, sort_keys=True),
                        review_id,
                        ReviewStatus.PENDING.value,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE reviews SET status = ?, reviewer = ?, reason = ?, decided_at = ? "
                    "WHERE review_id = ? AND status = ?",
                    (
                        status.value,
                        reviewer,
                        reason,
                        decided_at.isoformat(),
                        review_id,
                        ReviewStatus.PENDING.value,
                    ),
                )
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
            return self._fetch(connection, review_id, tenant_id)

    def claim_for_execution(self, review_id: str, actor: str, tenant_id: int = 1) -> ReviewItem:
        claimed_at = datetime.now(UTC)
        with self._transaction() as connection:
            item = self._fetch(connection, review_id, tenant_id)
            _validate_claim(item)
            cursor = connection.execute(
                "UPDATE reviews SET status = ?, executed_by = ?, executed_at = ? "
                "WHERE review_id = ? AND status = ?",
                (
                    ReviewStatus.EXECUTING.value,
                    actor,
                    claimed_at.isoformat(),
                    review_id,
                    ReviewStatus.APPROVED.value,
                ),
            )
            if cursor.rowcount != 1:
                # Another worker claimed it between the read and the write.
                raise AlreadyExecuted(self._fetch(connection, review_id, tenant_id))
            return self._fetch(connection, review_id, tenant_id)

    def complete_execution(self, review_id: str, result: dict[str, Any], tenant_id: int = 1) -> ReviewItem:
        with self._transaction() as connection:
            item = self._fetch(connection, review_id, tenant_id)
            connection.execute(
                "UPDATE reviews SET status = ?, result = ?, executed_at = ? WHERE review_id = ?",
                (
                    ReviewStatus.EXECUTED.value,
                    json.dumps(result, sort_keys=True, default=str),
                    datetime.now(UTC).isoformat(),
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
            return self._fetch(connection, review_id, tenant_id)

    def fail_execution(self, review_id: str, error: str, tenant_id: int = 1) -> ReviewItem:
        with self._transaction() as connection:
            item = self._fetch(connection, review_id, tenant_id)
            connection.execute(
                "UPDATE reviews SET status = ?, failure = ?, executed_at = ? WHERE review_id = ?",
                (
                    ReviewStatus.FAILED.value,
                    error,
                    datetime.now(UTC).isoformat(),
                    review_id,
                ),
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
            return self._fetch(connection, review_id, tenant_id)

    def recover_stale_executions(self, older_than: timedelta) -> list[ReviewItem]:
        """Move abandoned EXECUTING items to FAILED so they are not stuck forever.

        The outcome of an interrupted mutation is unknown, so recovery means a human
        re-approves rather than an automatic retry.
        """
        cutoff = (datetime.now(UTC) - older_than).isoformat()
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM reviews WHERE status = ? AND executed_at < ?",
                (ReviewStatus.EXECUTING.value, cutoff),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE reviews SET status = ?, failure = ? WHERE review_id = ?",
                    (
                        ReviewStatus.FAILED.value,
                        "execution did not complete; outcome unknown",
                        row["review_id"],
                    ),
                )
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

    def delete(self, review_id: str, tenant_id: int = 1) -> bool:
        with self._transaction() as connection:
            res = connection.execute(
                "DELETE FROM reviews WHERE review_id = ? AND tenant_id = ?",
                (review_id, tenant_id),
            )
            return bool(res.rowcount and res.rowcount > 0)

    def clear(self, tenant_id: int = 1, status: str | None = None) -> int:
        with self._transaction() as connection:
            if status is None:
                res = connection.execute(
                    "DELETE FROM reviews WHERE tenant_id = ?",
                    (tenant_id,),
                )
            elif status.upper() in ("INACTIVE", "TERMINAL"):
                res = connection.execute(
                    "DELETE FROM reviews WHERE tenant_id = ? AND status IN ('REJECTED', 'EXECUTED', 'FAILED')",
                    (tenant_id,),
                )
            else:
                res = connection.execute(
                    "DELETE FROM reviews WHERE tenant_id = ? AND status = ?",
                    (tenant_id, status.upper()),
                )
            return int(res.rowcount or 0)

    def _record(self, connection: sqlite3.Connection, event: AuditEvent) -> None:
        connection.execute(
            "INSERT INTO audit_events(action, actor, outcome, resource_id, detail, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.action,
                event.actor,
                event.outcome,
                event.resource_id,
                json.dumps(event.detail, sort_keys=True) if event.detail else None,
                event.occurred_at.astimezone(UTC).isoformat(),
            ),
        )

    def audit_trail(self, resource_id: str | None = None, tenant_id: int = 1) -> list[dict[str, Any]]:
        query = "SELECT * FROM audit_events WHERE tenant_id = ?"
        params: tuple[int | str, ...] = (tenant_id,)
        if resource_id:
            query += " AND resource_id = ?"
            params = (tenant_id, resource_id)
        with self._lock:
            rows = self.connection.execute(query + " ORDER BY sequence", params).fetchall()
        return [dict(row) for row in rows]

    def get_tenant_analytics(self, tenant_id: int) -> dict[str, Any]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT operation, payload, status FROM reviews WHERE tenant_id = ?",
                (tenant_id,)
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
                        payload = json.loads(row["payload"])
                        loser = payload.get("loser_claim", {})
                        winner = payload.get("winner_claim", {})
                        # Consider the protected revenue as the value of the winner that wasn't oversold, 
                        # plus the loser if an alternate was found. Let's just sum the winner's value as a baseline 
                        # for "revenue protected from collision refund".
                        val = float(winner.get("price_cad", loser.get("price_cad", 0)))
                        revenue_protected_cad += val
                    except Exception:
                        pass
                        
        return {
            "total_automated_actions": total_actions,
            "inventory_syncs": syncs,
            "collisions_prevented": contentions_prevented,
            "revenue_protected_cad": round(revenue_protected_cad, 2)
        }

    def close(self) -> None:
        self.connection.close()
