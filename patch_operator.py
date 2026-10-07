import re

with open("app/api/operator.py", "r") as f:
    content = f.read()

content = content.replace(
    'def decide(\n        self, review_id: str, approve: bool, reviewer: str, reason: str = ""\n    ) -> dict[str, Any]:',
    'def decide(\n        self, review_id: str, approve: bool, reviewer: str, reason: str = "", payload_update: dict[str, Any] | None = None\n    ) -> dict[str, Any]:'
)

content = content.replace(
    'item = self.reviews.decide(review_id, approve=approve, reviewer=reviewer, reason=reason)',
    'item = self.reviews.decide(review_id, approve=approve, reviewer=reviewer, reason=reason, payload_update=payload_update)'
)

with open("app/api/operator.py", "w") as f:
    f.write(content)
