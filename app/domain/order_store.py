from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.domain.order_events import ApplyOutcome, Outcome, Resolution, classify
from app.domain.orders import Order, OrderStatus
from app.observability.logging import get_logger

logger = get_logger("order_store")


@dataclass(frozen=True)
class OrderEvent:
    event_id: str
    order_id: str
    status: OrderStatus
    occurred_at: datetime


class OrderStore:
    """Small in-memory store for development; mirrors SQLiteOrderRepository semantics."""

    def __init__(self) -> None:
        self.orders: dict[str, Order] = {}
        self.processed_event_ids: set[str] = set()
        self._quarantined: list[OrderEvent] = []
        self._resolved: set[str] = set()

    def apply(self, event: OrderEvent) -> ApplyOutcome:
        if not event.event_id or not event.order_id:
            raise ValueError("event_id and order_id are required")
        if event.event_id in self.processed_event_ids:
            return ApplyOutcome(Outcome.DUPLICATE, event.order_id)
        order = self.orders.get(event.order_id)
        newly_created = order is None
        if order is None:
            order = Order(event.order_id, event.occurred_at)
            self.orders[event.order_id] = order

        decision = classify(order.status, event.status)
        self.processed_event_ids.add(event.event_id)

        if decision.resolution is Resolution.CONFLICT:
            self._quarantined.append(event)
            logger.error(
                "order.event.quarantined",
                order_id=event.order_id,
                event_id=event.event_id,
                current=order.status.value,
                incoming=event.status.value,
            )
            return ApplyOutcome(Outcome.QUARANTINED, event.order_id, order.status)
        if decision.resolution is Resolution.STALE:
            logger.info(
                "order.event.stale",
                order_id=event.order_id,
                event_id=event.event_id,
                current=order.status.value,
                incoming=event.status.value,
            )
            return ApplyOutcome(Outcome.STALE, event.order_id, order.status)
        if decision.resolution is Resolution.UNCHANGED:
            return ApplyOutcome(Outcome.UNCHANGED, event.order_id, order.status)

        order.status = event.status
        order.updated_at = datetime.now(UTC)
        if newly_created and decision.skipped:
            # The order first reached us mid-lifecycle, so created_at is that late
            # event's timestamp, not when the buyer ordered.
            order.created_at_estimated = True
        if decision.skipped:
            logger.warning(
                "order.event.fast_forwarded",
                order_id=event.order_id,
                event_id=event.event_id,
                skipped=[status.value for status in decision.skipped],
            )
        return ApplyOutcome(Outcome.APPLIED, event.order_id, order.status, decision.skipped)

    def active(self) -> list[Order]:
        return [o for o in self.orders.values() if o.status not in {OrderStatus.DELIVERED, OrderStatus.CANCELLED}]

    def quarantined(self, *, include_resolved: bool = False) -> list[dict[str, Any]]:
        return [
            {
                "event_id": event.event_id,
                "order_id": event.order_id,
                "observed_status": event.status.value,
                "occurred_at": event.occurred_at.isoformat(),
                "resolved": 0,
            }
            for event in self._quarantined
            if include_resolved or event.event_id not in self._resolved
        ]

    def resolve_quarantined(self, event_id: str) -> bool:
        open_ids = {e.event_id for e in self._quarantined} - self._resolved
        if event_id not in open_ids:
            return False
        self._resolved.add(event_id)
        return True


def reconcile(local: Iterable[Order], remote: Iterable[tuple[str, OrderStatus]]) -> list[str]:
    """Return order IDs whose local status differs from the remote snapshot."""
    local_by_id = {order.order_id: order.status for order in local}
    return [order_id for order_id, status in remote if local_by_id.get(order_id) != status]
