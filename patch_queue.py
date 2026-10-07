import re

with open("app/review/queue.py", "r") as f:
    content = f.read()

content = content.replace(
    'def decide(\n        self, review_id: str, *, approve: bool, reviewer: str, reason: str = ""\n    ) -> ReviewItem: ...',
    'def decide(\n        self, review_id: str, *, approve: bool, reviewer: str, reason: str = "", payload_update: dict[str, Any] | None = None\n    ) -> ReviewItem: ...'
)

merge_func = """def _merge_payload(base: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and key in base and isinstance(base[key], dict):
            _merge_payload(base[key], value)
        else:
            base[key] = value

class ReviewQueue:"""

content = content.replace("class ReviewQueue:", merge_func)

old_decide = """    def decide(
        self, review_id: str, *, approve: bool, reviewer: str, reason: str = ""
    ) -> ReviewItem:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        item = self._require(review_id)
        if item.status != ReviewStatus.PENDING:
            raise ValueError("review item has already been decided")
        item.status = ReviewStatus.APPROVED if approve else ReviewStatus.REJECTED
        item.reviewer = reviewer
        item.reason = reason
        item.decided_at = datetime.now(UTC)
        return item"""

new_decide = """    def decide(
        self, review_id: str, *, approve: bool, reviewer: str, reason: str = "", payload_update: dict[str, Any] | None = None
    ) -> ReviewItem:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        item = self._require(review_id)
        if item.status != ReviewStatus.PENDING:
            raise ValueError("review item has already been decided")
        if payload_update:
            _merge_payload(item.payload, payload_update)
        item.status = ReviewStatus.APPROVED if approve else ReviewStatus.REJECTED
        item.reviewer = reviewer
        item.reason = reason
        item.decided_at = datetime.now(UTC)
        return item"""

content = content.replace(old_decide, new_decide)

with open("app/review/queue.py", "w") as f:
    f.write(content)
