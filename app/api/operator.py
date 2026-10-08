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

    def list_reviews(self, pending_only: bool = False, tenant_id: int = 1) -> list[dict[str, Any]]:
        return [
            {
                "review_id": item.review_id,
                "operation": item.operation,
                "status": item.status.value,
                "reviewer": item.reviewer,
                "reason": item.reason,
                "executed_by": item.executed_by,
            }
            for item in self.reviews.list_items(pending_only, tenant_id=tenant_id)
        ]

    def decide(
        self, review_id: str, approve: bool, reviewer: str, reason: str = "", payload_update: dict[str, Any] | None = None, tenant_id: int = 1
    ) -> dict[str, Any]:
        item = self.reviews.decide(review_id, approve=approve, reviewer=reviewer, reason=reason, payload_update=payload_update, tenant_id=tenant_id)
        logger.info(
            "review.decided",
            review_id=item.review_id,
            operation=item.operation,
            status=item.status.value,
            reviewer=reviewer,
        )
        return {"review_id": item.review_id, "status": item.status.value, "reviewer": item.reviewer}

    def review_detail(self, review_id: str, tenant_id: int = 1) -> dict[str, Any]:
        item = self.reviews.get(review_id, tenant_id=tenant_id)
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

    def propose(self, operation: str, payload: dict[str, Any], tenant_id: int = 1) -> dict[str, Any]:
        if operation not in self.executor.allowed_operations:
            raise ValueError(f"Operation '{operation}' is not supported.")
        
        import uuid

        from app.review.queue import ReviewItem
        
        review_id = f"rev-{uuid.uuid4().hex[:12]}"
        item = ReviewItem(review_id=review_id, operation=operation, payload=payload)
        self.reviews.submit(item, tenant_id=tenant_id)
        
        logger.info("review.proposed", review_id=item.review_id, operation=operation)
        return {
            "review_id": item.review_id,
            "operation": item.operation,
            "status": item.status.value,
        }

    def execute(self, review_id: str, actor: str, tenant_id: int = 1, credentials: dict[str, str] = None) -> dict[str, Any]:
        outcome = self.executor.execute(self.reviews, review_id, actor=actor, tenant_id=tenant_id, credentials=credentials)
        item = outcome["item"]
        return {
            "review_id": item.review_id,
            "status": item.status.value,
            "executed_by": item.executed_by,
            "replayed": outcome["replayed"],
            "result": outcome["result"],
        }

    def delete(self, review_id: str, tenant_id: int = 1) -> bool:
        deleted = self.reviews.delete(review_id, tenant_id=tenant_id)
        if deleted:
            logger.info("review.deleted", review_id=review_id, tenant_id=tenant_id)
        return deleted

    def delete_batch(self, review_ids: list[str], tenant_id: int = 1) -> int:
        count = self.reviews.delete_batch(review_ids, tenant_id=tenant_id)
        logger.info("reviews.batch_deleted", count=count, tenant_id=tenant_id, requested=len(review_ids))
        return count

    def clear(self, tenant_id: int = 1, status: str | None = None) -> int:
        count = self.reviews.clear(tenant_id=tenant_id, status=status)
        logger.info("reviews.cleared", count=count, status=status, tenant_id=tenant_id)
        return count
