from __future__ import annotations

from app.domain.audit import AuditEvent, AuditLog
from app.review.queue import ReviewItem, ReviewStatus


class ApprovedActionExecutor:
    """Only executes a narrow allowlist of approved dry-run operations."""

    def __init__(self, client, audit: AuditLog) -> None:
        self.client = client
        self.audit = audit

    def execute(self, item: ReviewItem) -> dict:
        if item.status != ReviewStatus.APPROVED:
            raise PermissionError("only approved review items may execute")
        if item.operation == "create_product_draft":
            result = self.client.create_product_draft(item.payload)
        elif item.operation == "update_inventory":
            result = self.client.update_inventory(item.payload["changes"])
        elif item.operation == "update_prices":
            result = self.client.update_prices(item.payload["changes"])
        else:
            raise ValueError(f"operation is not allowlisted: {item.operation}")
        self.audit.record(
            AuditEvent(
                "approved_action.execute", item.reviewer or "unknown", "completed", item.review_id
            )
        )
        return result
