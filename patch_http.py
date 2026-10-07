import re

with open("app/api/http.py", "r") as f:
    content = f.read()

content = content.replace(
    'class Decision(BaseModel):\n    """Reviewer identity is taken from the bearer token, never from the body."""\n\n    approve: bool\n    reason: str = ""',
    'class Decision(BaseModel):\n    """Reviewer identity is taken from the bearer token, never from the body."""\n\n    approve: bool\n    reason: str = ""\n    payload_update: dict[str, Any] | None = None'
)

content = content.replace(
    'return operator.decide(review_id, decision.approve, principal.name, decision.reason)',
    'return operator.decide(review_id, decision.approve, principal.name, decision.reason, decision.payload_update)'
)

with open("app/api/http.py", "w") as f:
    f.write(content)
