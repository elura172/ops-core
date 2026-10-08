from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


TERMINAL_STATUSES = frozenset({ReviewStatus.REJECTED, ReviewStatus.EXECUTED, ReviewStatus.FAILED})


@dataclass
class ReviewItem:
    review_id: str
    operation: str
    payload: dict[str, Any]
    status: ReviewStatus = ReviewStatus.PENDING
    reviewer: str | None = None
    reason: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None
    executed_by: str | None = None
    executed_at: datetime | None = None
    result: dict[str, Any] | None = None
    failure: str | None = None
    tenant_id: int = 1


class AlreadyExecuted(Exception):
    """Raised when a claim loses to a call that already executed the item."""

    def __init__(self, item: ReviewItem) -> None:
        super().__init__("review item has already been executed")
        self.item = item


class ReviewStore(Protocol):
    """Owns every review state transition so they stay atomic and durable."""

    def submit(self, item: ReviewItem, tenant_id: int = 1) -> ReviewItem: ...
    def get(self, review_id: str, tenant_id: int = 1) -> ReviewItem | None: ...
    def list_items(self, pending_only: bool = False, tenant_id: int = 1) -> list[ReviewItem]: ...
    def decide(
        self, review_id: str, *, approve: bool, reviewer: str, reason: str = "", payload_update: dict[str, Any] | None = None, tenant_id: int = 1
    ) -> ReviewItem: ...
    def claim_for_execution(self, review_id: str, actor: str, tenant_id: int = 1) -> ReviewItem: ...
    def complete_execution(self, review_id: str, result: dict[str, Any], tenant_id: int = 1) -> ReviewItem: ...
    def fail_execution(self, review_id: str, error: str, tenant_id: int = 1) -> ReviewItem: ...
    def recover_stale_executions(self, older_than: timedelta) -> list[ReviewItem]: ...
    def delete(self, review_id: str, tenant_id: int = 1) -> bool: ...
    def delete_batch(self, review_ids: list[str], tenant_id: int = 1) -> int: ...
    def clear(self, tenant_id: int = 1, status: str | None = None) -> int: ...


def _validate_claim(item: ReviewItem) -> None:
    """Shared precondition so both stores refuse the same transitions."""
    if item.status is ReviewStatus.EXECUTED:
        raise AlreadyExecuted(item)
    if item.status is ReviewStatus.EXECUTING:
        raise ValueError("review item is already executing")
    if item.status is ReviewStatus.FAILED:
        raise PermissionError("failed execution requires a fresh approval")
    if item.status is not ReviewStatus.APPROVED:
        raise PermissionError("only approved review items may execute")


def _merge_payload(base: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and key in base and isinstance(base[key], dict):
            _merge_payload(base[key], value)
        else:
            base[key] = value

class ReviewQueue:
    """In-process store for development and tests.

    State does not survive a restart and is not shared between workers, so the
    at-most-once guarantee holds only within one process. Use SQLiteReviewStore
    for anything that outlives a single run.
    """

    def __init__(self) -> None:
        self.items: dict[str, ReviewItem] = {}

    def submit(self, item: ReviewItem, tenant_id: int = 1) -> ReviewItem:
        if item.review_id in self.items:
            raise ValueError("review_id already exists")
        if item.status != ReviewStatus.PENDING:
            raise ValueError("new review items must be pending")
        item.tenant_id = tenant_id
        self.items[item.review_id] = item
        return item

    def get(self, review_id: str, tenant_id: int = 1) -> ReviewItem | None:
        item = self.items.get(review_id)
        if item and item.tenant_id == tenant_id:
            return item
        return None

    def list_items(self, pending_only: bool = False, tenant_id: int = 1) -> list[ReviewItem]:
        items = [i for i in self.items.values() if i.tenant_id == tenant_id]
        return [i for i in items if i.status is ReviewStatus.PENDING] if pending_only else items

    def pending(self, tenant_id: int = 1) -> list[ReviewItem]:
        return self.list_items(pending_only=True, tenant_id=tenant_id)

    def _require(self, review_id: str, tenant_id: int = 1) -> ReviewItem:
        item = self.get(review_id, tenant_id=tenant_id)
        if item is None:
            raise KeyError(review_id)
        return item

    def decide(
        self, review_id: str, *, approve: bool, reviewer: str, reason: str = "", payload_update: dict[str, Any] | None = None, tenant_id: int = 1
    ) -> ReviewItem:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        item = self._require(review_id, tenant_id=tenant_id)
        if item.status != ReviewStatus.PENDING:
            raise ValueError("review item has already been decided")
        if payload_update:
            _merge_payload(item.payload, payload_update)
        item.status = ReviewStatus.APPROVED if approve else ReviewStatus.REJECTED
        item.reviewer = reviewer
        item.reason = reason
        item.decided_at = datetime.now(UTC)
        return item

    def claim_for_execution(self, review_id: str, actor: str, tenant_id: int = 1) -> ReviewItem:
        item = self._require(review_id, tenant_id=tenant_id)
        _validate_claim(item)
        item.status = ReviewStatus.EXECUTING
        item.executed_by = actor
        item.executed_at = datetime.now(UTC)
        return item

    def complete_execution(self, review_id: str, result: dict[str, Any], tenant_id: int = 1) -> ReviewItem:
        item = self._require(review_id, tenant_id=tenant_id)
        item.status = ReviewStatus.EXECUTED
        item.result = result
        item.executed_at = datetime.now(UTC)
        return item

    def fail_execution(self, review_id: str, error: str, tenant_id: int = 1) -> ReviewItem:
        """Leave the item terminal. The outcome of a partial mutation is unknown,
        so a human must re-approve rather than let a retry duplicate it."""
        item = self._require(review_id, tenant_id=tenant_id)
        item.status = ReviewStatus.FAILED
        item.failure = error
        item.executed_at = datetime.now(UTC)
        return item

    def recover_stale_executions(self, older_than: timedelta) -> list[ReviewItem]:
        cutoff = datetime.now(UTC) - older_than
        recovered = []
        for item in self.items.values():
            if item.status is ReviewStatus.EXECUTING and (item.executed_at or cutoff) < cutoff:
                item.status = ReviewStatus.FAILED
                item.failure = "execution did not complete; outcome unknown"
                recovered.append(item)
        return recovered

    def delete(self, review_id: str, tenant_id: int = 1) -> bool:
        item = self.items.get(review_id)
        if item and item.tenant_id == tenant_id:
            del self.items[review_id]
            return True
        return False

    def delete_batch(self, review_ids: list[str], tenant_id: int = 1) -> int:
        count = 0
        for rid in review_ids:
            item = self.items.get(rid)
            if item and item.tenant_id == tenant_id:
                del self.items[rid]
                count += 1
        return count

    def clear(self, tenant_id: int = 1, status: str | None = None) -> int:
        to_delete = []
        for rid, item in self.items.items():
            if item.tenant_id != tenant_id:
                continue
            if status is None:
                to_delete.append(rid)
            elif status.upper() in ("INACTIVE", "TERMINAL"):
                if item.status in TERMINAL_STATUSES:
                    to_delete.append(rid)
            elif item.status.value == status.upper() or item.status == status:
                to_delete.append(rid)
        for rid in to_delete:
            del self.items[rid]
        return len(to_delete)
