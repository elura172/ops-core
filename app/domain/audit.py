from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json


@dataclass(frozen=True)
class AuditEvent:
    action: str
    actor: str
    outcome: str
    resource_id: str | None = None
    detail: dict[str, str] | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_json(self) -> str:
        data = asdict(self)
        data["occurred_at"] = self.occurred_at.isoformat()
        return json.dumps(data, sort_keys=True)


class AuditLog:
    """Append-only development log; production should use durable restricted storage."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)
