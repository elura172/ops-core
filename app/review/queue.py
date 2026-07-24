from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


@dataclass
class ReviewItem:
    review_id: str
    operation: str
    payload: dict
    status: ReviewStatus = ReviewStatus.PENDING
    reviewer: str | None = None
    reason: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    decided_at: datetime | None = None


class ReviewQueue:
    def __init__(self) -> None:
        self.items: dict[str, ReviewItem] = {}

    def submit(self, item: ReviewItem) -> ReviewItem:
        if item.review_id in self.items:
            raise ValueError("review_id already exists")
        if item.status != ReviewStatus.PENDING:
            raise ValueError("new review items must be pending")
        self.items[item.review_id] = item
        return item

    def decide(
        self, review_id: str, *, approve: bool, reviewer: str, reason: str = ""
    ) -> ReviewItem:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        item = self.items.get(review_id)
        if item is None:
            raise KeyError(review_id)
        if item.status != ReviewStatus.PENDING:
            raise ValueError("review item has already been decided")
        item.status = ReviewStatus.APPROVED if approve else ReviewStatus.REJECTED
        item.reviewer = reviewer
        item.reason = reason
        item.decided_at = datetime.now(UTC)
        return item

    def pending(self) -> list[ReviewItem]:
        return [item for item in self.items.values() if item.status == ReviewStatus.PENDING]
