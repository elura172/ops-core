import re

with open("app/db/postgres.py", "r") as f:
    content = f.read()

content = content.replace("from app.review.queue import (", "from app.review.queue import (\n    _merge_payload,")

old_decide = """    def decide(
        self, review_id: str, *, approve: bool, reviewer: str, reason: str = ""
    ) -> ReviewItem:
        if not reviewer.strip():
            raise ValueError("reviewer is required")
        status = ReviewStatus.APPROVED if approve else ReviewStatus.REJECTED
        with self._transaction() as connection:
            item = self._fetch(connection, review_id)
            if item.status != ReviewStatus.PENDING:
                raise ValueError("review item has already been decided")
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
            )"""

new_decide = """    def decide(
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
                )"""

content = content.replace(old_decide, new_decide)

with open("app/db/postgres.py", "w") as f:
    f.write(content)
