from __future__ import annotations

from dataclasses import dataclass

from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewQueue


@dataclass
class OperatorService:
    reviews: ReviewQueue
    executor: ApprovedActionExecutor

    def health(self) -> dict[str, str]:
        return {"status": "ok", "mode": "dry-run" if self.executor.client.dry_run else "live"}

    def list_reviews(self, pending_only: bool = False) -> list[dict]:
        items = self.reviews.pending() if pending_only else list(self.reviews.items.values())
        return [
            {
                "review_id": item.review_id,
                "operation": item.operation,
                "status": item.status.value,
                "reviewer": item.reviewer,
                "reason": item.reason,
            }
            for item in items
        ]

    def decide(self, review_id: str, approve: bool, reviewer: str, reason: str = "") -> dict:
        item = self.reviews.decide(review_id, approve=approve, reviewer=reviewer, reason=reason)
        return {"review_id": item.review_id, "status": item.status.value, "reviewer": item.reviewer}

    def execute(self, review_id: str) -> dict:
        item = self.reviews.items.get(review_id)
        if item is None:
            raise KeyError(review_id)
        return self.executor.execute(item)
