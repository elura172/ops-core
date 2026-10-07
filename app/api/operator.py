from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.observability.logging import get_logger
from app.review.executor import ApprovedActionExecutor
from app.review.queue import ReviewStore

logger = get_logger("operator")


@dataclass
class OperatorService:
    reviews: ReviewStore
    executor: ApprovedActionExecutor

    def health(self) -> dict[str, str]:
        return {"status": "ok", "mode": "dry-run" if self.executor.client.dry_run else "live"}

    def list_reviews(self, pending_only: bool = False) -> list[dict[str, Any]]:
        return [
            {
                "review_id": item.review_id,
                "operation": item.operation,
                "status": item.status.value,
                "reviewer": item.reviewer,
                "reason": item.reason,
                "executed_by": item.executed_by,
            }
            for item in self.reviews.list_items(pending_only)
        ]

    def decide(
        self, review_id: str, approve: bool, reviewer: str, reason: str = ""
    ) -> dict[str, Any]:
        item = self.reviews.decide(review_id, approve=approve, reviewer=reviewer, reason=reason)
        logger.info(
            "review.decided",
            review_id=item.review_id,
            operation=item.operation,
            status=item.status.value,
            reviewer=reviewer,
        )
        return {"review_id": item.review_id, "status": item.status.value, "reviewer": item.reviewer}

    def review_detail(self, review_id: str) -> dict[str, Any]:
        item = self.reviews.get(review_id)
        if item is None:
            raise KeyError(review_id)
        blocked = None
        if item.operation not in self.executor.allowed_operations:
            blocked = "This operation is not supported."
        else:
            try:
                self.executor.preflight(item.operation)
            except PermissionError as exc:
                blocked = str(exc)
        return {**asdict(item), "execution_blocked": blocked}

    def propose(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if operation not in self.executor.allowed_operations:
            raise ValueError(f"Operation '{operation}' is not supported.")
        
        import uuid

        from app.review.queue import ReviewItem
        
        review_id = f"rev-{uuid.uuid4().hex[:12]}"
        item = ReviewItem(review_id=review_id, operation=operation, payload=payload)
        self.reviews.submit(item)
        
        logger.info("review.proposed", review_id=item.review_id, operation=operation)
        return {
            "review_id": item.review_id,
            "operation": item.operation,
            "status": item.status.value,
        }

    def execute(self, review_id: str, actor: str) -> dict[str, Any]:
        outcome = self.executor.execute(self.reviews, review_id, actor=actor)
        item = outcome["item"]
        return {
            "review_id": item.review_id,
            "status": item.status.value,
            "executed_by": item.executed_by,
            "replayed": outcome["replayed"],
            "result": outcome["result"],
        }
