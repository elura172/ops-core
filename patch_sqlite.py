import re

with open("app/db/review_store.py", "r") as f:
    content = f.read()

content = content.replace("from app.review.queue import (", "from app.review.queue import (\n    _merge_payload,")

old_decide = """    def decide(
        self, review_id: str, *, approve: bool, reviewer: str, reason: str = ""
    ) -> ReviewItem:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        status = ReviewStatus.APPROVED if approve else ReviewStatus.REJECTED
        decided_at = datetime.now(UTC)
        with self._transaction() as connection:
            item = self._fetch(connection, review_id)
            if item.status != ReviewStatus.PENDING:
                raise ValueError("review item has already been decided")
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
            )"""

new_decide = """    def decide(
        self, review_id: str, *, approve: bool, reviewer: str, reason: str = "", payload_update: dict[str, Any] | None = None
    ) -> ReviewItem:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        status = ReviewStatus.APPROVED if approve else ReviewStatus.REJECTED
        decided_at = datetime.now(UTC)
        with self._transaction() as connection:
            item = self._fetch(connection, review_id)
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
                )"""

content = content.replace(old_decide, new_decide)

with open("app/db/review_store.py", "w") as f:
    f.write(content)
