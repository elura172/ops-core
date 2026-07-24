from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class ConsentStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    OPTED_IN = "OPTED_IN"
    OPTED_OUT = "OPTED_OUT"


@dataclass
class CustomerProfile:
    customer_id: str
    email: str | None = None
    phone: str | None = None
    marketing_consent: ConsentStatus = ConsentStatus.UNKNOWN
    transactional_consent: ConsentStatus = ConsentStatus.UNKNOWN
    suppressed: bool = False
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def can_receive(self, channel: str, marketing: bool) -> bool:
        if self.suppressed:
            return False
        if channel == "email" and not self.email:
            return False
        if channel == "sms" and not self.phone:
            return False
        consent = self.marketing_consent if marketing else self.transactional_consent
        return consent == ConsentStatus.OPTED_IN


@dataclass(frozen=True)
class LifecycleEvent:
    event_id: str
    customer_id: str
    name: str
    occurred_at: datetime
    order_id: str | None = None


@dataclass(frozen=True)
class MessagePlan:
    event_id: str
    customer_id: str
    channel: str
    template: str
    marketing: bool
    variables: dict[str, str]
