from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class OrderStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PAID = "PAID"
    FULFILLMENT_PENDING = "FULFILLMENT_PENDING"
    LABEL_CREATED = "LABEL_CREATED"
    SHIPPED = "SHIPPED"
    IN_TRANSIT = "IN_TRANSIT"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"


ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.RECEIVED: frozenset({OrderStatus.PAID, OrderStatus.CANCELLED}),
    OrderStatus.PAID: frozenset({OrderStatus.FULFILLMENT_PENDING, OrderStatus.CANCELLED}),
    OrderStatus.FULFILLMENT_PENDING: frozenset({OrderStatus.LABEL_CREATED, OrderStatus.CANCELLED}),
    OrderStatus.LABEL_CREATED: frozenset({OrderStatus.SHIPPED, OrderStatus.CANCELLED}),
    OrderStatus.SHIPPED: frozenset({OrderStatus.IN_TRANSIT, OrderStatus.DELIVERED}),
    OrderStatus.IN_TRANSIT: frozenset({OrderStatus.DELIVERED}),
    OrderStatus.DELIVERED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}


@dataclass
class Order:
    order_id: str
    created_at: datetime
    status: OrderStatus = OrderStatus.RECEIVED
    last_alert_severity: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def transition(self, target: OrderStatus) -> None:
        if target == self.status:
            return
        if target not in ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(f"invalid order transition: {self.status} -> {target}")
        self.status = target
        self.updated_at = datetime.now(UTC)


@dataclass(frozen=True)
class SLAEvaluation:
    order_id: str
    severity: str
    elapsed_ratio: float
    remaining_minutes: int


def evaluate_sla(order: Order, sla_hours: int = 24, warning_ratio: float = 0.70) -> SLAEvaluation:
    if sla_hours <= 0 or not 0 < warning_ratio < 1:
        raise ValueError("sla_hours must be positive and warning_ratio must be between 0 and 1")
    now = datetime.now(UTC)
    created = order.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    elapsed = max(0.0, (now - created).total_seconds())
    total = sla_hours * 3600
    ratio = elapsed / total
    severity = "CRITICAL" if ratio >= 0.90 else "WARNING" if ratio >= warning_ratio else "OK"
    remaining = max(0, int((total - elapsed) / 60))
    return SLAEvaluation(order.order_id, severity, round(ratio, 4), remaining)
